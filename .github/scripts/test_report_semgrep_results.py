import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("report-semgrep-results.py")


class ReportSemgrepResultsTest(unittest.TestCase):
    def run_report(self, output: dict, outcome: str = "success") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory, "semgrep.json")
            summary_path = Path(directory, "summary.md")
            output_path.write_text(json.dumps(output), encoding="utf-8")
            env = {**os.environ, "SEMGREP_OUTCOME": outcome, "GITHUB_STEP_SUMMARY": str(summary_path)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(output_path)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            result.summary = summary_path.read_text(encoding="utf-8")
            return result

    def test_successful_clean_scan(self) -> None:
        result = self.run_report({"results": [], "errors": []})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Scan complete: 0 finding(s); no analysis errors.", result.summary)

    def test_partial_parsing_is_annotated_and_fails(self) -> None:
        result = self.run_report(
            {
                "results": [],
                "errors": [
                    {
                        "type": ["PartialParsing", []],
                        "message": "Syntax error\nwhile parsing",
                        "path": ".github/workflows/release.yml",
                        "spans": [
                            {
                                "file": ".github/workflows/release.yml",
                                "start": {"line": 12, "col": 3},
                                "end": {"line": 12, "col": 8},
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("::error file=.github/workflows/release.yml,line=12,col=3", result.stdout)
        self.assertIn("title=Semgrep PartialParsing::Syntax error%0Awhile parsing", result.stdout)
        self.assertIn("Scan incomplete: 1 analysis error(s)", result.summary)

    def test_blocking_finding_is_annotated_and_fails(self) -> None:
        result = self.run_report(
            {
                "results": [
                    {
                        "check_id": "example.rule",
                        "path": ".github/workflows/example.yml",
                        "start": {"line": 4, "col": 1},
                        "end": {"line": 4, "col": 9},
                        "extra": {"severity": "ERROR", "message": "Unsafe command"},
                    }
                ],
                "errors": [],
            },
            outcome="failure",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("::error file=.github/workflows/example.yml,line=4,col=1", result.stdout)
        self.assertIn("title=example.rule::Unsafe command", result.stdout)
        self.assertIn("Scan failed with 1 finding(s)", result.summary)


if __name__ == "__main__":
    unittest.main()
