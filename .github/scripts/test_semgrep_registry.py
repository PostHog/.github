import io
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

import semgrep_registry


def rule(rule_id: str, pattern: str = "foo(...)") -> dict:
    return {"id": rule_id, "languages": ["python"], "severity": "WARNING", "message": "m", "pattern": pattern}


def fake_urlopen(payloads: dict[str, list[dict]]):
    def urlopen(request: urllib.request.Request, timeout: int = 0) -> io.BytesIO:
        registry_id = request.full_url.split("/c/", 1)[1]
        return io.BytesIO(json.dumps({"rules": payloads[registry_id]}).encode("utf-8"))

    return urlopen


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
    def render(self, summary: dict, dry_run: dict | None = None) -> str:
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory, "summary.json")
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            dry_run_path = None
            if dry_run is not None:
                dry_run_path = Path(directory, "dry-run.json")
                dry_run_path.write_text(json.dumps(dry_run), encoding="utf-8")
            out = Path(directory, "report.md")
            semgrep_registry.report(summary_path, out, dry_run_path, "PostHog/posthog")
            return out.read_text(encoding="utf-8")

    def test_report_lists_rules_and_dry_run_counts(self) -> None:
        markdown = self.render(
            {
                "changed": True,
                "totals": {"added": 1, "removed": 1, "changed": 0},
                "snapshots": {"test": {"added": ["new.rule"], "removed": ["old.rule"], "changed": []}},
            },
            {
                "results": [{"check_id": "new.rule"}, {"check_id": "new.rule"}],
                "errors": [{"message": "Internal matching error\ndetails"}],
            },
        )

        self.assertIn("| test | 1 | 1 | 0 |", markdown)
        self.assertIn("- `new.rule`", markdown)
        self.assertIn("- `old.rule`", markdown)
        self.assertIn("2 finding(s), 1 analysis error(s).", markdown)
        self.assertIn("| `new.rule` | 2 |", markdown)
        self.assertIn("- Internal matching error", markdown)

    def test_report_without_changes_or_dry_run(self) -> None:
        markdown = self.render({"changed": False, "totals": {"added": 0, "removed": 0, "changed": 0}, "snapshots": {}})

        self.assertIn("No rule changes.", markdown)
        self.assertNotIn("Dry run", markdown)


if __name__ == "__main__":
    unittest.main()
