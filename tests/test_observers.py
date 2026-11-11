"""Observers: GitHub facts become record events."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner.observers import GitHubObserver, _checks_green  # noqa: E402
from runner.platform import Issue  # noqa: E402
from runner.recorder import RecordBuilder  # noqa: E402
from runner.scenario import load_scenario  # noqa: E402
from runner.clock import FakeClock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def fake_gh(payloads):
    calls = []

    def run(args):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return payloads["list"]
        if args[:2] == ["pr", "view"]:
            return payloads["view"]
        raise AssertionError(f"unexpected gh call: {args}")

    return run, calls


class GitHubObserverTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(ROOT / "cases" / "quick-01-hotfix.json")
        self.clock = FakeClock()
        self.builder = RecordBuilder(self.scenario, "4.4.0", self.clock.now())
        self.builder.add(
            "comment",
            self.clock.now(),
            actor_type="agent",
            kind="gate_card",
            gate="quick-accept-merge",
            candidate="candidate/KAR-101-abc123",
        )
        self.issue = Issue(
            id="i1", identifier="KAR-101", title="fix", status="todo"
        )

    def test_records_docs_tests_ci_and_merge(self):
        payloads = {
            "list": [
                {
                    "number": 7,
                    "title": "KAR-101 fix deploy --dry-run",
                    "body": "",
                    "headRefName": "fix/KAR-101-deploy",
                }
            ],
            "view": {
                "number": 7,
                "url": "https://github.com/x/y/pull/7",
                "state": "MERGED",
                "mergedAt": "2026-11-11T12:00:00Z",
                "mergeCommit": {"oid": "def456"},
                "headRefOid": "abc123",
                "baseRefName": "develop",
                "files": [
                    {"path": "docs/DESIGN.md"},
                    {"path": "src/app.py"},
                    {"path": "tests/test_old.py", "changeType": "deleted"},
                ],
                "statusCheckRollup": [
                    {"conclusion": "SUCCESS", "status": "COMPLETED"}
                ],
            },
        }
        run, calls = fake_gh(payloads)
        observer = GitHubObserver("x/y", run=run, poll_every=1)
        observer.record(self.builder, self.issue, self.clock.now())
        types = [event["type"] for event in self.builder.events]
        self.assertIn("docs_written", types)
        self.assertIn("test_change", types)
        self.assertIn("ci", types)
        self.assertIn("merge", types)
        merge = next(e for e in self.builder.events if e["type"] == "merge")
        self.assertEqual(merge["sha"], "def456")
        self.assertEqual(merge["candidate"], "candidate/KAR-101-abc123")
        self.assertEqual(merge["branch"], "develop")
        # idempotent: a second poll must not duplicate events
        before = len(self.builder.events)
        observer.record(self.builder, self.issue, self.clock.now())
        self.assertEqual(len(self.builder.events), before)
        self.assertGreaterEqual(len(calls), 2)

    def test_pr_not_mentioning_the_issue_is_ignored(self):
        payloads = {
            "list": [{"number": 9, "title": "unrelated", "body": "", "headRefName": "x"}],
            "view": {},
        }
        run, calls = fake_gh(payloads)
        observer = GitHubObserver("x/y", run=run, poll_every=1)
        observer.record(self.builder, self.issue, self.clock.now())
        self.assertEqual([e for e in self.builder.events if e["type"] == "merge"], [])
        self.assertEqual(len(calls), 1)

    def test_check_rollup_states(self):
        self.assertTrue(_checks_green([{"conclusion": "SUCCESS"}]))
        self.assertFalse(_checks_green([{"conclusion": "FAILURE"}]))
        self.assertIsNone(_checks_green([{"status": "IN_PROGRESS"}]))
        self.assertIsNone(_checks_green([]))

    def test_gh_failure_is_recorded_not_raised(self):
        def run(args):
            from runner.observers import GhError

            raise GhError("gh is down")

        observer = GitHubObserver("x/y", run=run, poll_every=1)
        observer.record(self.builder, self.issue, self.clock.now())
        errors = [e for e in self.builder.events if e["type"] == "observer_error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("gh is down", errors[0]["detail"])


if __name__ == "__main__":
    unittest.main()
