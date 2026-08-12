import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import semgrep_registry


def rule(rule_id: str, pattern: str = "foo(...)") -> dict:
    return {"id": rule_id, "languages": ["python"], "severity": "WARNING", "message": "m", "pattern": pattern}


def fake_urlopen(payloads: dict[str, list[dict]]):
    def urlopen(request: urllib.request.Request, timeout: int = 0) -> io.BytesIO:
        registry_id = request.full_url.split("/c/", 1)[1]
        return io.BytesIO(json.dumps({"rules": payloads[registry_id]}).encode("utf-8"))

    return urlopen


class FetchRetryTest(unittest.TestCase):
    def flaky_urlopen(self, failures: int):
        calls = {"count": 0}

        def urlopen(request: urllib.request.Request, timeout: int = 0) -> io.BytesIO:
            calls["count"] += 1
            if calls["count"] <= failures:
                raise urllib.error.URLError("connection reset")
            return io.BytesIO(json.dumps({"rules": [rule("a")]}).encode("utf-8"))

        return urlopen, calls

    def test_fetch_recovers_from_transient_failures(self) -> None:
        urlopen, calls = self.flaky_urlopen(failures=semgrep_registry.FETCH_ATTEMPTS - 1)

        with mock.patch.object(semgrep_registry, "FETCH_BACKOFF_SECONDS", 0):
            rules = semgrep_registry.fetch_registry_rules("p/test", urlopen)

        self.assertEqual([r["id"] for r in rules], ["a"])
        self.assertEqual(calls["count"], semgrep_registry.FETCH_ATTEMPTS)

    def test_fetch_raises_after_exhausting_attempts(self) -> None:
        urlopen, calls = self.flaky_urlopen(failures=semgrep_registry.FETCH_ATTEMPTS)

        with mock.patch.object(semgrep_registry, "FETCH_BACKOFF_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "Could not fetch .*p/test"):
                semgrep_registry.fetch_registry_rules("p/test", urlopen)

        self.assertEqual(calls["count"], semgrep_registry.FETCH_ATTEMPTS)


class DiffTest(unittest.TestCase):
    def test_compute_diff(self) -> None:
        old = {"a": rule("a"), "b": rule("b"), "c": rule("c")}
        new = {"b": rule("b"), "c": rule("c", pattern="bar(...)"), "d": rule("d")}

        self.assertEqual(
            semgrep_registry.compute_diff(old, new),
            {"added": ["d"], "removed": ["a"], "changed": ["c"]},
        )

    def test_merge_rules_keeps_first_duplicate(self) -> None:
        merged = semgrep_registry.merge_rules([[rule("a", pattern="first")], [rule("a", pattern="second"), rule("b")]])

        self.assertEqual(sorted(merged), ["a", "b"])
        self.assertEqual(merged["a"]["pattern"], "first")

    def test_snapshot_roundtrip_is_sorted_and_loadable(self) -> None:
        rules = {"b": rule("b"), "a": rule("a")}
        rendered = semgrep_registry.render_snapshot(["p/test"], rules)

        self.assertTrue(rendered.startswith("# GENERATED FILE"))
        self.assertIn("p/test", rendered)
        self.assertLess(rendered.index("id: a"), rendered.index("id: b"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "snapshot.yaml")
            path.write_text(rendered, encoding="utf-8")
            self.assertEqual(semgrep_registry.load_snapshot(path), rules)


class SyncTest(unittest.TestCase):
    def sync(self, registry_dir: Path, payloads: dict[str, list[dict]]) -> dict:
        return semgrep_registry.sync(registry_dir, registry_dir / "summary.json", fake_urlopen(payloads))

    def test_sync_writes_snapshots_and_diffs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_dir = Path(directory)
            (registry_dir / "sources.json").write_text(json.dumps({"test": ["p/one", "p/two"]}), encoding="utf-8")

            first = self.sync(registry_dir, {"p/one": [rule("a")], "p/two": [rule("b")]})
            self.assertTrue(first["changed"])
            self.assertEqual(first["snapshots"]["test"]["added"], ["a", "b"])

            unchanged = self.sync(registry_dir, {"p/one": [rule("a")], "p/two": [rule("b")]})
            self.assertFalse(unchanged["changed"])
            self.assertEqual(unchanged["totals"], {"added": 0, "removed": 0, "changed": 0})

            updated = self.sync(registry_dir, {"p/one": [rule("a", pattern="bar(...)")], "p/two": [rule("c")]})
            self.assertTrue(updated["changed"])
            self.assertEqual(
                updated["snapshots"]["test"],
                {"added": ["c"], "removed": ["b"], "changed": ["a"]},
            )
            self.assertEqual(json.loads((registry_dir / "summary.json").read_text())["totals"]["added"], 1)

    def test_changed_rules_extracts_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry_dir = Path(directory)
            (registry_dir / "sources.json").write_text(json.dumps({"test": ["p/one"]}), encoding="utf-8")
            self.sync(registry_dir, {"p/one": [rule("a"), rule("b")]})
            self.sync(registry_dir, {"p/one": [rule("a"), rule("b", pattern="bar(...)"), rule("c")]})

            out = registry_dir / "changed.yaml"
            count = semgrep_registry.changed_rules(registry_dir, registry_dir / "summary.json", out)

            self.assertEqual(count, 2)
            extracted = semgrep_registry.load_snapshot(out)
            self.assertEqual(sorted(extracted), ["b", "c"])
            self.assertEqual(extracted["b"]["pattern"], "bar(...)")


class ReportTest(unittest.TestCase):
    def render(self, summary: dict, dry_runs: dict[str, dict] | None = None) -> str:
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory, "summary.json")
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            dry_run_dir = None
            if dry_runs is not None:
                dry_run_dir = Path(directory, "dry-run")
                dry_run_dir.mkdir()
                for repo, dry_run in dry_runs.items():
                    Path(dry_run_dir, repo.replace("/", "__") + ".json").write_text(
                        json.dumps(dry_run), encoding="utf-8"
                    )
            out = Path(directory, "report.md")
            semgrep_registry.report(summary_path, out, dry_run_dir)
            return out.read_text(encoding="utf-8")

    def test_report_lists_rules_and_per_repo_dry_run_counts(self) -> None:
        markdown = self.render(
            {
                "changed": True,
                "totals": {"added": 1, "removed": 1, "changed": 0},
                "snapshots": {"test": {"added": ["new.rule"], "removed": ["old.rule"], "changed": []}},
            },
            {
                "PostHog/posthog": {
                    "results": [{"check_id": "new.rule"}, {"check_id": "new.rule"}],
                    "errors": [{"message": "Internal matching error\ndetails"}],
                },
                "PostHog/posthog-js": {"results": [], "errors": []},
            },
        )

        self.assertIn("| test | 1 | 1 | 0 |", markdown)
        self.assertIn("- `new.rule`", markdown)
        self.assertIn("- `old.rule`", markdown)
        self.assertIn("## Dry run of added/changed rules against PostHog/posthog", markdown)
        self.assertIn("2 finding(s), 1 analysis error(s).", markdown)
        self.assertIn("| `new.rule` | 2 |", markdown)
        self.assertIn("- Internal matching error", markdown)
        self.assertIn("## Dry run of added/changed rules against PostHog/posthog-js", markdown)
        self.assertIn("0 finding(s), 0 analysis error(s).", markdown)

    def test_report_without_changes_or_dry_run(self) -> None:
        markdown = self.render({"changed": False, "totals": {"added": 0, "removed": 0, "changed": 0}, "snapshots": {}})

        self.assertIn("No rule changes.", markdown)
        self.assertNotIn("Dry run", markdown)


if __name__ == "__main__":
    unittest.main()
