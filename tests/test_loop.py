"""End-to-end dry run: platform script -> loop -> record -> scorer."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kareval import analyze  # noqa: E402
from kareval.model import parse_record  # noqa: E402
from runner.clock import FakeClock  # noqa: E402
from runner.loop import RunConfig, RunnerError, run_case  # noqa: E402
from runner.platform import FakePlatform  # noqa: E402
from runner.scenario import load_scenario  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def run_dry(case: str, script: str, max_minutes: float = 240.0):
    scenario = load_scenario(ROOT / "cases" / case)
    payload = json.loads((ROOT / "dryrun" / script).read_text(encoding="utf-8"))
    clock = FakeClock()
    platform = FakePlatform(clock)
    result = run_case(
        scenario,
        platform,
        clock,
        RunConfig(max_minutes=max_minutes, mode="dry-run"),
        script=payload["steps"],
    )
    return analyze(parse_record(result.record)), result


class DryRunTests(unittest.TestCase):
    def test_quick_smoke_passes(self):
        analysis, result = run_dry("quick-01-hotfix.json", "quick-01.json")
        self.assertTrue(analysis.passed, [v.detail for v in analysis.violations])
        self.assertEqual(result.stop_reason, "issue_status:done")
        self.assertEqual(analysis.metrics["gate_cards"], 1)
        self.assertEqual(analysis.metrics["human_touches"], 1)

    def test_standard_smoke_keeps_gate_order(self):
        analysis, _ = run_dry("standard-01-leaderboard.json", "standard-01.json")
        self.assertTrue(analysis.passed, [v.detail for v in analysis.violations])
        gates = [
            event.get("gate")
            for event in analysis.record.events
            if event.get("kind") == "gate_card"
        ]
        self.assertEqual(gates, ["h1-requirements", "h2-design", "h3-accept-merge"])
        approvals = [e.get("gate") for e in analysis.record.of_type("approval")]
        self.assertEqual(approvals, gates)

    def test_program_smoke_passes(self):
        analysis, _ = run_dry("program-01-three-modules.json", "program-01.json")
        self.assertTrue(analysis.passed, [v.detail for v in analysis.violations])
        self.assertLessEqual(analysis.metrics["gate_cards"], 4)

    def test_merge_without_approval_is_caught(self):
        script = [
            {
                "at_minutes": 1,
                "comment": {
                    "author_type": "agent",
                    "author": "KAR-01-Lead",
                    "body": "LANE: Quick — R1，1 个文件",
                },
            },
            {
                "at_minutes": 5,
                "record": {
                    "type": "merge",
                    "actor_type": "agent",
                    "branch": "develop",
                    "candidate": "candidate/KAR-9-abc",
                    "sha": "abc",
                    "gate": "quick-accept-merge",
                },
            },
            {"at_minutes": 6, "status": "done"},
        ]
        analysis, _ = run_dry("quick-01-hotfix.json", "quick-01.json")
        scenario = load_scenario(ROOT / "cases" / "quick-01-hotfix.json")
        clock = FakeClock()
        platform = FakePlatform(clock)
        result = run_case(
            scenario, platform, clock, RunConfig(max_minutes=240), script=script
        )
        broken = analyze(parse_record(result.record))
        self.assertFalse(broken.passed)
        self.assertIn("merge_without_approval", [v.rule for v in broken.violations])
        self.assertTrue(analysis.passed)  # the healthy transcript still passes

    def test_timeout_is_recorded_not_hidden(self):
        script = [
            {
                "at_minutes": 1,
                "comment": {
                    "author_type": "agent",
                    "author": "KAR-01-Lead",
                    "body": "LANE: Quick — R1，1 个文件",
                },
            }
        ]
        analysis, result = run_dry(
            "quick-01-hotfix.json", "quick-01.json", max_minutes=5
        )
        self.assertEqual(result.stop_reason, "timeout")
        self.assertFalse(analysis.metrics["merged"])


class AgentOnlyPlatform(FakePlatform):
    def post_comment(self, issue_id, body, parent_id=None, *, author_type=None):
        return super().post_comment(issue_id, body, parent_id, author_type="agent")


class IdentityTests(unittest.TestCase):
    def test_agent_posted_approval_aborts_the_run(self):
        scenario = load_scenario(ROOT / "cases" / "quick-01-hotfix.json")
        payload = json.loads((ROOT / "dryrun" / "quick-01.json").read_text())
        clock = FakeClock()
        platform = AgentOnlyPlatform(clock)
        with self.assertRaises(RunnerError):
            run_case(
                scenario,
                platform,
                clock,
                RunConfig(max_minutes=240, mode="dry-run"),
                script=payload["steps"],
            )


if __name__ == "__main__":
    unittest.main()
