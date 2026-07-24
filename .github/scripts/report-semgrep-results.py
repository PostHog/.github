#!/usr/bin/env python3
"""Turn Semgrep JSON output into GitHub annotations and an accurate summary."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def command_escape(value: object, *, property_value: bool = False) -> str:
    escaped = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def annotation(level: str, message: str, **properties: object) -> None:
    rendered_properties = ",".join(
        f"{key}={command_escape(value, property_value=True)}" for key, value in properties.items() if value is not None
    )
    separator = " " if rendered_properties else ""
    print(f"::{level}{separator}{rendered_properties}::{command_escape(message)}")


def error_type(error: dict[str, Any]) -> str:
    value = error.get("type", "Semgrep error")
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value)


def report_finding(result: dict[str, Any]) -> None:
    extra = result.get("extra", {})
    severity = str(extra.get("severity", "WARNING")).upper()
    level = "error" if severity == "ERROR" else "warning" if severity == "WARNING" else "notice"
    start = result.get("start", {})
    end = result.get("end", {})
    annotation(
        level,
        str(extra.get("message", "Semgrep finding")),
        file=result.get("path"),
        line=start.get("line"),
        col=start.get("col"),
        endLine=end.get("line"),
        endColumn=end.get("col"),
        title=result.get("check_id", "Semgrep finding"),
    )


def report_error(error: dict[str, Any]) -> None:
    spans = error.get("spans") or []
    span = spans[0] if spans else {}
    start = span.get("start", {})
    end = span.get("end", {})
    annotation(
        "error",
        str(error.get("message", "Semgrep analysis error")),
        file=error.get("path") or span.get("file"),
        line=start.get("line"),
        col=start.get("col"),
        endLine=end.get("line"),
        endColumn=end.get("col"),
        title=f"Semgrep {error_type(error)}",
    )


def write_summary(findings: int, errors: int, outcome: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    if errors:
        status = f"Scan incomplete: {errors} analysis error(s); {findings} finding(s) in successfully parsed code."
    elif outcome != "success":
        status = f"Scan failed with {findings} finding(s) and no structured analysis errors."
    else:
        status = f"Scan complete: {findings} finding(s); no analysis errors."

    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(f"## Semgrep\n\n{status}\n")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <semgrep.json>", file=sys.stderr)
        return 2

    output_path = Path(sys.argv[1])
    if not output_path.is_file():
        annotation("error", f"Semgrep did not create its JSON output at {output_path}", title="Semgrep scan failed")
        return 1

    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        annotation("error", f"Could not read Semgrep JSON output: {error}", title="Semgrep scan failed")
        return 1

    findings = output.get("results") or []
    errors = output.get("errors") or []
    outcome = os.environ.get("SEMGREP_OUTCOME", "success")

    for finding in findings:
        report_finding(finding)
    for error in errors:
        report_error(error)

    if outcome != "success" and not findings and not errors:
        annotation("error", "Semgrep exited unsuccessfully without a structured finding or error.", title="Semgrep scan failed")

    write_summary(len(findings), len(errors), outcome)
    return 1 if errors or outcome != "success" else 0


if __name__ == "__main__":
    raise SystemExit(main())
