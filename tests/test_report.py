"""Aggregate scorecard and release gate."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kareval import analyze, load_record  # noqa: E402
from runner.report import aggregate, render_markdown  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


class AggregateTests(unittest.TestCase):
    def test_all_green_passes_the_gate(self):
        analyses = [analyze(load_record(FIXTURES / "quick-01-pass.json"))]
        report = aggregate(analyses, {"soft": 100.0})
        self.assertTrue(report.release_gate, report.reasons)
        self.assertEqual(report.hard_violations, 0)
        self.assertIn("PASS", render_markdown(report, "4.4.0"))

    def test_one_hard_violation_fails_the_gate(self):
        analyses = [analyze(load_record(FIXTURES / "standard-02-stale-approval.json"))]
        report = aggregate(analyses)
        self.assertFalse(report.release_gate)
        self.assertGreaterEqual(report.hard_violations, 1)

    def test_soft_regression_fails_the_gate(self):
        analyses = [analyze(load_record(FIXTURES / "program-02-no-h3.json"))]
        report = aggregate(analyses, {"soft": 100.0})
        self.assertFalse(report.release_gate)

    def test_lane_trend_against_baseline(self):
        analyses = [analyze(load_record(FIXTURES / "quick-01-pass.json"))]
        report = aggregate(analyses, {"soft": 100.0, "lanes": {"quick": 90.0}})
        quick = next(lane for lane in report.lanes if lane.lane == "quick")
        self.assertIn("↑", quick.trend)

    def test_json_shape(self):
        analyses = [analyze(load_record(FIXTURES / "quick-01-pass.json"))]
        payload = json.loads(json.dumps(aggregate(analyses).as_dict()))
        self.assertEqual(payload["lanes"][0]["lane"], "quick")
        self.assertIn("release_gate", payload)


if __name__ == "__main__":
    unittest.main()
