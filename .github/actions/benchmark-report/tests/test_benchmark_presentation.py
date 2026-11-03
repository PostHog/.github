"""Readable, descriptive review flags without changing measured data or CI policy."""

import copy
import html
import json

import pytest

from benchmarks.report import cli
from benchmarks.report.compare import compare
from benchmarks.report.render import MAX_REPORT_BYTES, headline_rows, render, review_change
from tests.test_benchmark_report import pair


def measured_pair(values=None, *, base_values=None, direction="lower", unit="ns/op"):
    values = values or [110] * 6
    base_values = base_values or [100] * len(values)
    base, head = pair()
    for doc, samples in ((base, base_values), (head, values)):
        doc["suite"]["repetitions"] = len(samples)
        workload = copy.deepcopy(doc["benchmarks"][0])
        workload["id"] = "flags.single.boolean"
        metric = workload["metrics"][0]
        metric.update(direction=direction, unit=unit)
        metric["samples"] = [dict(metric["samples"][0], repetition=i, value=value) for i, value in enumerate(samples)]
        workload["metrics"] = [metric]
        workload["runs"] = [dict(workload["runs"][0], repetition=i) for i in range(len(samples))]
        doc["benchmarks"] = [workload]
    return base, head


def decoded(report):
    return html.unescape(report).replace("\u200b", "")


@pytest.mark.parametrize(
    "values,expected",
    [
        ([105] * 6, "regression"),
        ([95] * 6, "improvement"),
        ([104.999] * 6, None),
        ([95.001] * 6, None),
        ([110] * 5 + [99], None),
        ([110] * 5 + [100], None),
        ([90] * 5 + [101], None),
        ([100] * 6, None),
        ([110] * 5, None),
        ([110] * 7, None),
    ],
)
def test_review_filter_requires_size_and_all_six_pairs(values, expected):
    comparison = compare(*measured_pair(values))
    assert review_change(comparison["rows"][0]) == expected
    assert bool(headline_rows(comparison)) == bool(expected)
    assert not comparison["execution_failed"]


def test_higher_is_better_is_respected():
    assert review_change(compare(*measured_pair(direction="higher", unit="ops/s"))["rows"][0]) == "improvement"


@pytest.mark.parametrize("base_values", [[0] * 6, [0] + [100] * 5])
def test_undefined_percentages_do_not_qualify(base_values):
    assert review_change(compare(*measured_pair(base_values=base_values))["rows"][0]) is None


@pytest.mark.parametrize("problem", ["failure", "incompatible", "incomplete", "missing"])
def test_partial_or_incompatible_reports_do_not_claim_no_changes(problem):
    base, head = measured_pair()
    kwargs = {}
    if problem == "failure":
        kwargs["source_status"] = "failure"
    elif problem == "incompatible":
        head["execution"]["cpu"] = "different CPU"
    elif problem == "incomplete":
        for doc in (base, head):
            doc["suite"]["repetitions"] = 7
    else:
        head["benchmarks"][0]["metrics"] = []
    comparison = compare(base, head, **kwargs)
    assert headline_rows(comparison) == []
    report = render(comparison)
    assert "No overall performance conclusion is available" in report
    assert "No changes flagged" not in report


def test_readable_table_and_data_preservation():
    comparison = compare(*measured_pair([80_000] * 6, base_values=[100_000] * 6))
    original = copy.deepcopy(comparison)
    report = decoded(render(comparison))
    summary, details = report.split("<details>", 1)
    assert "1 change worth reviewing: 1 observed improvement" in summary
    assert "Evaluate one boolean flag | Time per operation | 100 µs/op | 80 µs/op | 20% less time" in summary
    assert "View all results" in details and "Assessment" in details
    assert "inconclusive" not in report
    assert "not proof" in summary
    assert comparison == original


@pytest.mark.parametrize(
    "unit,base_value,head_value,expected",
    [
        ("bytes/op", 12 * 1024, 15 * 1024, "12 KiB/op | 15 KiB/op | 25% more memory"),
        ("bytes", 128 * 1024**2, 256 * 1024**2, "128 MiB | 256 MiB | 100% more memory"),
        ("ns/op", 18_780_000, 15_000_000, "18.78 ms/op | 15 ms/op"),
        ("count/op", 10, 20, "10 count/op | 20 count/op | 100% more"),
        ("ns", 1_000_000_000, 2_000_000_000, "1 s | 2 s | 100% more time"),
        ("ops/s", 1000, 2000, "1,000 ops/s | 2,000 ops/s | 100% higher"),
        ("ns/op", 100, 100.01, "<0.1% more time"),
        ("bytes/op", 0, 0.001, "0 B/op | <0.01 B/op | —"),
    ],
)
def test_readable_units_and_changes(unit, base_value, head_value, expected):
    report = decoded(render(compare(*measured_pair([head_value] * 6, base_values=[base_value] * 6, unit=unit))))
    assert expected in report


@pytest.mark.parametrize(
    "identity,label",
    [
        ("flags.all.1000", "Evaluate all 1,000 flags"),
        ("flags.load.1000", "Evaluate 1,000 flags under concurrent load"),
        ("flags.refresh.1000", "Refresh 1,000 flag definitions"),
        ("capture.enqueue.4096", "Queue an event (4,096-byte payload)"),
        ("capture.drain.steady.32", "Capture and deliver events (32-byte payload, steady)"),
        ("capture.drain.burst.4096", "Capture and deliver events (4,096-byte payload, burst)"),
        ("custom.workload", "custom.workload"),
    ],
)
def test_workload_names(identity, label):
    base, head = measured_pair()
    for doc in (base, head):
        doc["benchmarks"][0]["id"] = identity
    assert label in decoded(render(compare(base, head)))


def test_headline_cap_keeps_all_flags_in_expanded_results():
    base, head = measured_pair()
    for doc in (base, head):
        doc["benchmarks"] = [dict(copy.deepcopy(doc["benchmarks"][0]), id=f"test-{i}") for i in range(15)]
    comparison = compare(base, head)
    assert len(headline_rows(comparison)) == 15
    summary, details = render(comparison).split("<details>", 1)
    assert "15 changes worth reviewing" in summary
    assert summary.count("10% more time") == 12
    assert "3 more are in all results" in summary
    assert details.count("10% more time") == 15


def test_run_links_use_only_validated_caller_coordinates():
    comparison = compare(*measured_pair())
    expected = "https://github.com/PostHog/posthog-node/actions/runs/123"
    assert expected in render(comparison, repository="PostHog/posthog-node", run_id=123)
    for repository, run_id in [
        ("bad](https://evil.example)", 123),
        ("PostHog/x", "123)"),
        ("../..", 123),
        ("x/" + "x" * 202, 123),
    ]:
        assert "https://" not in render(comparison, repository=repository, run_id=run_id)


def test_offline_cli_flags_are_informational_and_link_its_run(tmp_path):
    base, head = measured_pair()
    for role, doc in (("base", base), ("head", head)):
        (tmp_path / f"{role}.json").write_text(json.dumps(doc))
    output = tmp_path / "report"
    assert (
        cli.main(
            [
                "--base-results",
                str(tmp_path / "base.json"),
                "--head-results",
                str(tmp_path / "head.json"),
                "--output-directory",
                str(output),
            ],
            {"GITHUB_REPOSITORY": "PostHog/posthog-node", "GITHUB_RUN_ID": "123"},
        )
        == 0
    )
    assert "1 observed regression" in (output / "report.md").read_text()
    assert "https://github.com/PostHog/posthog-node/actions/runs/123" in (output / "report.md").read_text()
    assert json.loads((output / "comparison.json").read_text()) == compare(base, head)


def test_many_flagged_hostile_rows_are_bounded_and_escaped():
    base, head = measured_pair()
    attack = "<script>@team [click](https://evil.example) |" * 4
    for doc in (base, head):
        doc["benchmarks"] = [dict(copy.deepcopy(doc["benchmarks"][0]), id=attack + str(i)) for i in range(128)]
        doc["benchmarks"][0]["metrics"][0]["id"] = attack
    report = render(compare(base, head))
    assert len(report.encode()) < MAX_REPORT_BYTES
    assert "<script>" not in report and "@team" not in report and "https://evil.example" not in report
