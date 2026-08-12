#!/usr/bin/env python3
"""Vendor Semgrep registry packs as pinned snapshots under .semgrep/registry/.

Scan workflows point at the snapshot files instead of live `p/...` registry
configs, so a registry-side rule change can never alter CI behavior until a
snapshot update lands on main. The semgrep-registry-update workflow runs
`sync` on a schedule, opens a PR with any changes, and notifies Slack.

Commands:
    sync            Fetch every pack in sources.json, rewrite the snapshot
                    files, and (optionally) write a JSON diff summary.
    changed-rules   Emit a rules file containing only the added/changed rule
                    definitions from a `sync` summary, for dry-run scans.
    report          Render a `sync` summary (plus optional dry-run scan
                    outputs) as markdown for the update PR body.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

# ruamel.yaml rather than PyYAML: it is a core dependency of semgrep itself,
# so it is guaranteed inside the semgrep container images this script runs in
# (PyYAML was dropped from the image in 1.172.0). It is also the canonical
# serializer for the snapshots — regenerate them inside the pinned semgrep
# image, not with a locally installed YAML library, to keep output stable.
from ruamel.yaml import YAML

SEMGREP_URL = "https://semgrep.dev"
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = 10
GENERATED_HEADER = (
    "# GENERATED FILE - DO NOT EDIT.\n"
    "# Snapshot of Semgrep registry config(s): {sources}\n"
    "# Rules are fetched from https://semgrep.dev/c/<id>, deduplicated by rule id,\n"
    "# and sorted. Refresh with: python3 .github/scripts/semgrep_registry.py sync\n"
    "# (the semgrep-registry-update workflow does this on a schedule).\n"
)


def _yaml() -> YAML:
    parser = YAML(typ="safe", pure=True)
    parser.default_flow_style = False
    parser.allow_unicode = True
    parser.width = 120
    return parser


def load_yaml(text: str) -> Any:
    return _yaml().load(text)


def dump_yaml(data: Any) -> str:
    buffer = io.StringIO()
    _yaml().dump(data, buffer)
    return buffer.getvalue()


def fetch_registry_rules(registry_id: str, urlopen: Callable[..., Any] = urllib.request.urlopen) -> list[dict[str, Any]]:
    """Download a registry config (p/<pack> or r/<rule>) and return its rules."""
    url = f"{SEMGREP_URL}/c/{registry_id}"
    request = urllib.request.Request(url, headers={"User-Agent": "posthog-semgrep-registry-sync"})
    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
            break
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < FETCH_ATTEMPTS:
                print(f"Fetch of {url} failed (attempt {attempt}): {error}; retrying", file=sys.stderr)
                time.sleep(FETCH_BACKOFF_SECONDS * attempt)
    else:
        raise RuntimeError(f"Could not fetch {url} after {FETCH_ATTEMPTS} attempts: {last_error}")

    data = load_yaml(raw)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuntimeError(f"Unexpected response from {url}: no top-level 'rules' list")
    rules = data["rules"]
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            raise RuntimeError(f"Unexpected response from {url}: rule without a string 'id'")
    return rules


def merge_rules(rule_lists: list[list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Merge rule lists into an id-keyed dict, keeping the first definition of duplicate ids."""
    merged: dict[str, dict[str, Any]] = {}
    for rules in rule_lists:
        for rule in rules:
            merged.setdefault(rule["id"], rule)
    return merged


def render_snapshot(sources: list[str], rules_by_id: dict[str, dict[str, Any]]) -> str:
    header = GENERATED_HEADER.format(sources=", ".join(sources))
    body = dump_yaml({"rules": [rules_by_id[rule_id] for rule_id in sorted(rules_by_id)]})
    return header + body


def load_snapshot(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = load_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuntimeError(f"Existing snapshot {path} is not a valid rules file")
    return {rule["id"]: rule for rule in data["rules"]}


def compute_diff(old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(rule_id for rule_id in set(old) & set(new) if old[rule_id] != new[rule_id]),
    }


def load_sources(registry_dir: Path) -> dict[str, list[str]]:
    sources_path = registry_dir / "sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    if not isinstance(sources, dict) or not all(
        isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids) for ids in sources.values()
    ):
        raise RuntimeError(f"{sources_path} must map snapshot names to non-empty lists of registry ids")
    return sources


def sync(registry_dir: Path, summary_path: Path | None, urlopen: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    sources = load_sources(registry_dir)
    summary: dict[str, Any] = {"snapshots": {}, "totals": {"added": 0, "removed": 0, "changed": 0}}

    for name in sorted(sources):
        registry_ids = sources[name]
        snapshot_path = registry_dir / f"{name}.yaml"
        new_rules = merge_rules([fetch_registry_rules(registry_id, urlopen) for registry_id in registry_ids])
        old_rules = load_snapshot(snapshot_path)
        diff = compute_diff(old_rules, new_rules)
        snapshot_path.write_text(render_snapshot(registry_ids, new_rules), encoding="utf-8")

        summary["snapshots"][name] = diff
        for key in summary["totals"]:
            summary["totals"][key] += len(diff[key])
        print(
            f"{name}: {len(new_rules)} rules "
            f"(+{len(diff['added'])} added, -{len(diff['removed'])} removed, ~{len(diff['changed'])} changed)"
        )

    summary["changed"] = any(summary["totals"].values())
    if summary_path:
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def changed_rules(registry_dir: Path, summary_path: Path, out_path: Path) -> int:
    """Write a rules file with the definitions of every added/changed rule in the summary."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, diff in sorted(summary["snapshots"].items()):
        wanted = set(diff["added"]) | set(diff["changed"])
        if not wanted:
            continue
        snapshot = load_snapshot(registry_dir / f"{name}.yaml")
        for rule_id in sorted(wanted):
            if rule_id in seen:
                continue
            if rule_id not in snapshot:
                raise RuntimeError(f"Rule {rule_id} from summary is missing in snapshot {name}.yaml")
            rules.append(snapshot[rule_id])
            seen.add(rule_id)
    out_path.write_text(dump_yaml({"rules": rules}), encoding="utf-8")
    print(f"Wrote {len(rules)} added/changed rule(s) to {out_path}", file=sys.stderr)
    print(len(rules))
    return len(rules)


def report(summary_path: Path, out_path: Path, dry_run_dir: Path | None) -> None:
    """Render the sync summary (and optional per-repo dry-run scan outputs) as markdown."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    lines = ["## Semgrep registry snapshot changes", ""]

    totals = summary["totals"]
    if not summary.get("changed"):
        lines.append("No rule changes.")
    else:
        lines.append("| Snapshot | Added | Removed | Changed |")
        lines.append("| --- | ---: | ---: | ---: |")
        for name, diff in sorted(summary["snapshots"].items()):
            if any(diff.values()):
                lines.append(f"| {name} | {len(diff['added'])} | {len(diff['removed'])} | {len(diff['changed'])} |")
        lines.append(f"| **total** | {totals['added']} | {totals['removed']} | {totals['changed']} |")
        for kind, label in (("added", "Added"), ("changed", "Changed"), ("removed", "Removed")):
            rule_ids = sorted({rule_id for diff in summary["snapshots"].values() for rule_id in diff[kind]})
            if rule_ids:
                lines.extend(["", f"### {label} rules", ""])
                lines.extend(f"- `{rule_id}`" for rule_id in rule_ids)

    for dry_run_path in sorted(dry_run_dir.glob("*.json")) if dry_run_dir else []:
        # File names encode the scanned repo as owner__repo; owner names
        # can't contain underscores, so the first "__" is the separator.
        repo = dry_run_path.stem.replace("__", "/", 1)
        dry_run = json.loads(dry_run_path.read_text(encoding="utf-8"))
        results = dry_run.get("results") or []
        errors = dry_run.get("errors") or []
        lines.extend(["", f"## Dry run of added/changed rules against {repo}", ""])
        lines.append(f"{len(results)} finding(s), {len(errors)} analysis error(s).")
        if results:
            counts: dict[str, int] = {}
            for result in results:
                counts[result["check_id"]] = counts.get(result["check_id"], 0) + 1
            lines.extend(["", "| Rule | Findings |", "| --- | ---: |"])
            for rule_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"| `{rule_id}` | {count} |")
        if errors:
            lines.extend(["", "Analysis errors (these would break scans if enforced):", ""])
            seen_messages: set[str] = set()
            for error in errors:
                message = str(error.get("message", "")).split("\n")[0][:200]
                if message not in seen_messages:
                    lines.append(f"- {message}")
                    seen_messages.add(message)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote report to {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".semgrep" / "registry",
        help="Directory holding sources.json and the snapshot files",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Fetch packs and rewrite snapshots")
    sync_parser.add_argument("--summary", type=Path, help="Write a JSON diff summary to this path")

    changed_parser = subparsers.add_parser("changed-rules", help="Emit added/changed rule definitions from a sync summary")
    changed_parser.add_argument("--summary", type=Path, required=True)
    changed_parser.add_argument("--out", type=Path, required=True)

    report_parser = subparsers.add_parser("report", help="Render a sync summary as markdown")
    report_parser.add_argument("--summary", type=Path, required=True)
    report_parser.add_argument("--out", type=Path, required=True)
    report_parser.add_argument(
        "--dry-run-dir", type=Path, help="Directory of per-repo semgrep JSON outputs named owner__repo.json"
    )

    arguments = parser.parse_args()
    if arguments.command == "sync":
        sync(arguments.registry_dir, arguments.summary)
    elif arguments.command == "changed-rules":
        changed_rules(arguments.registry_dir, arguments.summary, arguments.out)
    else:
        report(arguments.summary, arguments.out, arguments.dry_run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
