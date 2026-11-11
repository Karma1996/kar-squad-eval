"""Scenario loading and the pinning guard."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner.clock import FakeClock  # noqa: E402
from runner.loop import RunConfig, RunnerError, run_case  # noqa: E402
from runner.platform import FakePlatform  # noqa: E402
from runner.scenario import (  # noqa: E402
    ScenarioError,
    load_scenario,
    load_smoke,
    parse_scenario,
)

ROOT = Path(__file__).resolve().parents[1]


class ScenarioTests(unittest.TestCase):
    def test_loads_quick_case(self):
        scenario = load_scenario(ROOT / "cases" / "quick-01-hotfix.json")
        self.assertEqual(scenario.lane, "quick")
        self.assertEqual(scenario.required_gates, ("quick-accept-merge",))
        self.assertEqual(scenario.human_policy[0].action, "approve")

    def test_smoke_set_is_three_cases(self):
        scenarios = load_smoke(ROOT / "cases")
        self.assertEqual([item.id for item in scenarios], ["quick-01", "standard-01", "program-01"])
        self.assertEqual({item.lane for item in scenarios}, {"quick", "standard", "program"})

    def test_rejects_moving_branch(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                {
                    "schema_version": 1,
                    "id": "x-01",
                    "lane": "quick",
                    "title": "t",
                    "intent": "i",
                    "arena": {"repo": "r", "base_sha": "main", "seed": "s"},
                    "expected": {"lane": "quick"},
                }
            )

    def test_rejects_lane_disagreement(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(
                {
                    "schema_version": 1,
                    "id": "x-01",
                    "lane": "quick",
                    "title": "t",
                    "intent": "i",
                    "arena": {"repo": "r", "base_sha": "abc1234", "seed": "s"},
                    "expected": {"lane": "standard"},
                }
            )

    def test_rejects_stale_approval_without_candidate(self):
        raw = json.loads((ROOT / "cases" / "standard-02-stale-approval.json").read_text())
        raw["human_policy"][1] = {"gate": "h2-design", "action": "approve_stale"}
        with self.assertRaises(ScenarioError):
            parse_scenario(raw)

    def test_live_run_refuses_unpinned_case(self):
        scenario = load_scenario(ROOT / "cases" / "quick-01-hotfix.json")
        self.assertFalse(scenario.pinned)  # golden cases still carry TBD
        clock = FakeClock()
        platform = FakePlatform(clock)
        with self.assertRaises(RunnerError):
            run_case(scenario, platform, clock, RunConfig(mode="live"))


if __name__ == "__main__":
    unittest.main()
