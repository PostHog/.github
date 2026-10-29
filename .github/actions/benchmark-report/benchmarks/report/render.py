"""Render bounded Markdown from data, never artifact-provided markup or URLs."""

import json
import re
import unicodedata
from pathlib import Path

MAX_REPORT_BYTES = 58_000  # Reserve room for the publisher's trusted markers.
REVIEW_THRESHOLD_PERCENT = 5
REVIEW_PAIRS = 6

SINGLE_FLAG_NAMES = {
    "boolean": "Evaluate one boolean flag",
    "rollout": "Evaluate a rollout flag",
    "variant": "Evaluate a multivariate flag",
    "property": "Evaluate a property-filtered flag",
    "regex": "Evaluate a regex-filtered flag",
    "cohort": "Evaluate a cohort-filtered flag",
    "early": "Evaluate a flag (early match)",
    "late": "Evaluate a flag (late match)",
    "no-match": "Evaluate a flag (no match)",
}
METRIC_NAMES = {
    "elapsed_time_per_op": "Time per operation",
    "process_cpu_time_per_op": "Process CPU time per operation",
    "caller_thread_cpu_time_per_op": "Caller-thread CPU time per operation",
    "allocated_bytes_per_op": "Allocated memory per operation",
    "allocations_per_op": "Allocations per operation",
    "idle_process_rss": "Idle process memory (RSS)",
    "observed_workload_peak_rss": "Observed peak process memory (RSS)",
    "process_lifetime_peak_rss": "Lifetime peak process memory (RSS)",
    "post_settling_process_rss": "Process memory after settling (RSS)",
    "post_settling_heap_used": "Managed heap after settling",
}


def safe_text(value, limit: int = 240) -> str:
    text = str(value)
    text = "".join(" " if unicodedata.category(c).startswith("C") or c.isspace() else c for c in text)
    # Bound encoded bytes, including entity expansion, without splitting an entity.
    pieces = []
    size = 0
    for c in text:
        # Entities stop Markdown/HTML syntax; the separator prevents mentions and URL autolinking.
        piece = c if c.isalnum() or c == " " else f"&#{ord(c)};" + ("&#8203;" if c in "@:." else "")
        length = len(piece.encode("utf-8"))
        if size + length > limit - 3:
            pieces.append("…")
            break
        pieces.append(piece)
        size += length
    return "".join(pieces)


def json_text(value) -> str:
    # Safe even when a consumer embeds the comparison in HTML; it remains ordinary JSON on disk.
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026") + "\n"


def _quantity(value, unit: str) -> str:
    if value is None:
        return "—"
    suffix = "/op" if unit.endswith("/op") else ""
    if unit in ("ns", "ns/op"):
        scales = ((1e9, "s"), (1e6, "ms"), (1e3, "µs"), (1, "ns"))
    elif unit in ("bytes", "bytes/op"):
        scales = ((1024**3, "GiB"), (1024**2, "MiB"), (1024, "KiB"), (1, "B"))
    else:
        scales = ((1, unit.removesuffix("/op")),)
    scale, label = next(((scale, label) for scale, label in scales if abs(value) >= scale), scales[-1])
    scaled = value / scale
    number = "<0.01" if 0 < scaled < 0.01 else f"{scaled:,.2f}".rstrip("0").rstrip(".")
    return f"{number} {safe_text(label, 40)}{suffix}"


def _workload_name(identity: str) -> str:
    if identity.startswith("flags.single."):
        return SINGLE_FLAG_NAMES.get(identity.removeprefix("flags.single."), identity)
    match = re.fullmatch(r"flags\.(all|load|refresh)\.([0-9]{1,7})", identity)
    if match:
        mode, count = match.groups()
        count = f"{int(count):,}"
        return {
            "all": f"Evaluate all {count} flags",
            "load": f"Evaluate {count} flags under concurrent load",
            "refresh": f"Refresh {count} flag definitions",
        }[mode]
    match = re.fullmatch(r"capture\.(enqueue|drain)(?:\.(steady|burst))?\.([0-9]{1,7})", identity)
    if match:
        mode, strategy, size = match.groups()
        operation = "Queue an event" if mode == "enqueue" else "Capture and deliver events"
        schedule = f", {strategy}" if strategy else ""
        return f"{operation} ({int(size):,}-byte payload{schedule})"
    return identity


def review_change(row: dict) -> str | None:
    """Descriptive review filter, not a significance test or an execution gate."""
    change = row.get("change_percent")
    paired = row.get("paired_change_percent", [])
    if (
        row["status"] != "inconclusive"
        or row.get("execution_failed")
        or change is None
        or abs(change) < REVIEW_THRESHOLD_PERCENT
        or row.get("pairs") != REVIEW_PAIRS
        or len(paired) != REVIEW_PAIRS
        or "Incomplete repetitions" in row.get("reasons", [])
        or not all(value is not None and (value > 0 if change > 0 else value < 0) for value in paired)
    ):
        return None
    direction = (row["head"] or row["base"])["direction"]
    improved = change < 0 if direction == "lower" else change > 0
    return "improvement" if improved else "regression"


def _change(row: dict, unit: str) -> str:
    change = row.get("change_percent")
    if change is None:
        return "—"
    if change == 0:
        return "No observed change"
    amount = "<0.1" if abs(change) < 0.1 else f"{abs(change):.1f}".rstrip("0").rstrip(".")
    if unit in ("ns", "ns/op"):
        description = "less time" if change < 0 else "more time"
    elif unit in ("bytes", "bytes/op"):
        description = "less memory" if change < 0 else "more memory"
    elif unit in ("count", "count/op"):
        description = "fewer" if change < 0 else "more"
    else:
        description = "lower" if change < 0 else "higher"
    return f"{amount}% {description}"


def _row(row: dict, *, details=False, workload=None) -> str:
    metric = row["head"] or row["base"] or {}
    unit = metric.get("unit", "")
    label = safe_text(METRIC_NAMES.get(row["metric"], row["metric"]), 160)
    if metric.get("quality") == "estimated":
        label += " (estimated)"
    workload_label = safe_text(_workload_name(row["workload"]), 240)
    if details and "shard_id" in row:
        workload_label += "<br>Shard: " + safe_text(row["shard_id"], 100)
    line = (
        f"| {workload_label} | {label} | {_quantity(row.get('base_median'), unit)} | "
        f"{_quantity(row.get('head_median'), unit)} | {_change(row, unit)} |"
    )
    if details:
        flagged = review_change(row)
        assessment = f"Worth reviewing: observed {flagged}" if flagged else "Not flagged"
        if row["status"] != "inconclusive":
            assessment = safe_text(row["status"])
        reasons = [r for r in row.get("reasons", []) if r != "Descriptive only; uncertainty is not calibrated"]
        reasons.extend(row.get("explanations", []))
        for reason in dict.fromkeys(reasons):
            assessment += "; " + safe_text(reason, 220)
        for role, context in (workload or {}).items():
            failures = context.get("failures", [])
            for failure in failures[:3]:
                reason = failure.get("reason") or failure.get("error")
                if reason:
                    assessment += (
                        f"; {role} {safe_text(failure['pass'], 32)} repetition {failure['repetition']}: "
                        + safe_text(reason, 220)
                    )
            if len(failures) > 3:
                assessment += f"; {len(failures) - 3} more {role} failures in comparison.json"
        line += f" {assessment} |"
    return line + "\n"


def _incomplete(comparison: dict) -> bool:
    return not any("base_median" in row for row in comparison["rows"]) or any(
        row["status"] in ("not comparable", "no baseline", "missing head")
        or "Incomplete repetitions" in row.get("reasons", [])
        for row in comparison["rows"]
    )


def headline_rows(comparison: dict) -> list[dict]:
    if comparison["execution_failed"] or _incomplete(comparison):
        return []
    return sorted(
        (row for row in comparison["rows"] if review_change(row)),
        key=lambda row: (-abs(row["change_percent"]), row["workload"], row["metric"]),
    )


def render(comparison: dict, *, repository=None, run_id=None) -> str:
    flagged = headline_rows(comparison)
    chunks = [f"## SDK performance: {safe_text(comparison['sdk'])}\n\n"]
    if comparison["execution_failed"]:
        chunks.append("**Benchmark execution failed. No overall performance conclusion is available.**\n\n")
    elif _incomplete(comparison):
        chunks.append(
            "**No overall performance conclusion is available. "
            "Measurements are missing, incomplete or incompatible.**\n\n"
        )
    elif flagged:
        improvements = sum(review_change(row) == "improvement" for row in flagged)
        regressions = len(flagged) - improvements
        counts = []
        for count, name in ((improvements, "improvement"), (regressions, "regression")):
            if count:
                counts.append(f"{count} observed {name}{'s' if count != 1 else ''}")
        chunks.append(
            f"**{len(flagged)} change{'s' if len(flagged) != 1 else ''} worth reviewing: {', '.join(counts)}.**\n\n"
        )
    else:
        chunks.append("**Benchmarks completed. No changes flagged for review.**\n\n")
    chunks.append(
        "Flags require at least a 5% change in the median and all six pairs agreeing on direction. "
        "This is a noise filter, not proof of a performance change or a CI gate.\n\n"
    )
    table = "| Test | Measurement | Before PR | With PR | Change |\n| --- | --- | ---: | ---: | --- |\n"
    if flagged:
        chunks.append(table)
        chunks.extend(_row(row) for row in flagged[:12])
        if len(flagged) > 12:
            chunks.append(f"\nShowing the 12 largest flagged changes; {len(flagged) - 12} more are in all results.\n")
    chunks.append(
        "\n<details>\n<summary>View all results</summary>\n\n"
        "| Test | Measurement | Before PR | With PR | Change | Assessment |\n"
        "| --- | --- | ---: | ---: | --- | --- |\n"
    )
    # Reserve the footer and explanations before adding bounded rows.
    size = len("".join(chunks).encode("utf-8"))
    shown = 0
    for row in comparison["rows"]:
        line = _row(row, details=True, workload=comparison["workloads"][row["workload"]])
        if size + len(line.encode("utf-8")) > MAX_REPORT_BYTES - 3000:
            break
        chunks.append(line)
        size += len(line.encode("utf-8"))
        shown += 1
    chunks.append(
        "\nValues are medians of paired measurements. Not flagged does not mean unchanged. "
        "Unavailable means a measurement could not be provided, not zero. "
        "Estimated allocation measurements retain their instrumentation uncertainty. "
        "RSS measures the whole process, not just SDK allocations; observed peaks are sampled checkpoints. "
        "Paired ranges in the diagnostics describe variability, not confidence intervals.\n\n"
    )
    if comparison["comparison_version"] == 2:
        chunks.append(
            "Each shard uses its own runner; compare Before PR against With PR within a row, not across shards.\n\n"
        )
        context = next(iter(comparison["shards"].values()))["context"]
    else:
        context = comparison["context"]
    chunks.append("</details>\n\n")
    revisions = []
    for role in ("base", "head"):
        revision = context[role]["revision"]
        revisions.append(
            f"`{safe_text(revision['commit'])}`" + (" (dirty working tree)" if revision.get("dirty") else "")
        )
    chunks.append(f"Compared {revisions[0]} → {revisions[1]}.\n\n")
    # Link only to a GitHub run built from caller metadata, never a result-supplied URL.
    if (
        isinstance(repository, str)
        and len(repository) <= 201
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        and all(part not in (".", "..") for part in repository.split("/"))
        and re.fullmatch(r"[1-9][0-9]{0,18}", str(run_id))
    ):
        chunks.append(f"[Full report and diagnostics](https://github.com/{repository}/actions/runs/{run_id})\n\n")
    chunks.append(
        f"{len(comparison['rows'])} metric rows; {shown} rows shown in all results. "
        "Omitted or shortened data remain in `comparison.json`, including per-runner environments, "
        "resource limits, measurement methods, samples and checkpoints.\n"
    )
    report = "".join(chunks)
    assert len(report.encode("utf-8")) < MAX_REPORT_BYTES
    return report


def write_outputs(output_directory: str, comparison: dict, report: str, environ: dict) -> tuple[Path, Path]:
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report_path, comparison_path = directory / "report.md", directory / "comparison.json"
    report_path.write_text(report, encoding="utf-8")
    comparison_path.write_text(json_text(comparison), encoding="utf-8")
    if environ.get("GITHUB_STEP_SUMMARY"):
        with Path(environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(report)
    if environ.get("GITHUB_OUTPUT"):
        # Paths are caller-controlled, but must not inject additional action outputs.
        if any(c in str(directory) for c in "\r\n"):
            raise ValueError("Output directory contains a newline")
        with Path(environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(f"report-path={report_path}\ncomparison-path={comparison_path}\n")
    return report_path, comparison_path
