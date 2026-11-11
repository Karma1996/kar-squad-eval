"""Preflight: the read-only gate before a live simulation spends real runs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runner.__main__ import main
from runner.identity import IdentityError
from runner.preflight import run_preflight

PROJECT = "109edb85-ca1b-472f-952f-cb399e1dfd6f"
SQUAD = "1478e645-4c4c-4250-b0c8-fad5b494616d"
MEMBER = {
    "id": "9791acef-1d97-44f2-8502-0e7391fc1a7b",
    "user_id": "d9833006-05ed-42cb-ad9b-9c4f38c58bb4",
    "name": "KAR-SIM-Human",
    "email": "sim@example.com",
    "role": "member",
}


class FakeCli:
    def __init__(self, *, auth="User:    KAR-SIM-Human (sim@example.com)", project=True, squad=True):
        self.auth = auth
        self.project = project
        self.squad = squad

    def __call__(self, args):
        args = list(args)
        if args == ["auth", "status"]:
            return 0, self.auth, ""
        if args == ["workspace", "member", "list", "--output", "json"]:
            return 0, json.dumps([MEMBER]), ""
        if args[:2] == ["project", "get"] and self.project:
            return 0, json.dumps({"id": PROJECT, "title": "kar-sim-arena", "visibility": "workspace"}), ""
        if args[:2] == ["squad", "get"] and self.squad:
            return 0, json.dumps({"id": SQUAD, "name": "KAR Delivery Squad"}), ""
        return 1, "", "not found"


class PreflightTest(unittest.TestCase):
    def build(self, cli=None, **kwargs):
        return run_preflight(
            cli="multica",
            project_id=PROJECT,
            assignee_id=SQUAD,
            cases_dir="cases",
            env={},
            run=cli or FakeCli(),
            check_gh=False,
            **kwargs,
        )

    def test_passes_on_a_properly_configured_environment(self):
        report = self.build(arena_repo="Karma1996/kar-sim-arena")
        self.assertTrue(report.ok, report.render())
        self.assertEqual(
            [check.name for check in report.checks],
            ["environment", "identity", "project", "assignee", "cases"],
        )
        self.assertIn("preflight passed", report.render())

    def test_fails_when_an_agent_task_token_is_present(self):
        report = run_preflight(
            cli="multica",
            project_id=PROJECT,
            assignee_id=SQUAD,
            env={"MULTICA_TOKEN": "mat_x"},
            run=FakeCli(),
            check_gh=False,
        )
        self.assertFalse(report.ok)
        environment = next(c for c in report.checks if c.name == "environment")
        self.assertFalse(environment.ok)
        self.assertIn("MULTICA_TOKEN", environment.detail)

    def test_fails_when_the_cli_acts_as_the_operator(self):
        report = self.build(FakeCli(auth="User:    karma (owner@example.com)"))
        self.assertFalse(report.ok)
        identity = next(c for c in report.checks if c.name == "identity")
        self.assertFalse(identity.ok)
        self.assertIn("authenticates as karma", identity.detail)

    def test_fails_when_the_project_is_missing(self):
        report = self.build(FakeCli(project=False))
        self.assertFalse(report.ok)
        self.assertFalse(next(c for c in report.checks if c.name == "project").ok)

    def test_fails_when_the_assignee_is_not_a_squad(self):
        report = self.build(FakeCli(squad=False))
        self.assertFalse(report.ok)
        assignee = next(c for c in report.checks if c.name == "assignee")
        self.assertFalse(assignee.ok)
        self.assertIn("no squad/agent", assignee.detail)

    def test_fails_on_unpinned_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = json.loads(Path("cases/quick-01-hotfix.json").read_text(encoding="utf-8"))
            case["id"] = "quick-99"
            case["arena"]["base_sha"] = "TBD-arena-baseline"
            (root / "quick-99.json").write_text(json.dumps(case), encoding="utf-8")
            (root / "smoke.json").write_text(json.dumps({"cases": ["quick-99"]}), encoding="utf-8")
            report = run_preflight(
                cli="multica",
                project_id=PROJECT,
                assignee_id=SQUAD,
                cases_dir=str(root),
                env={},
                run=FakeCli(),
                check_gh=False,
            )
        self.assertFalse(report.ok)
        cases = next(c for c in report.checks if c.name == "cases")
        self.assertFalse(cases.ok)
        self.assertIn("quick-99", cases.detail)

    def test_missing_gh_fails_the_arena_check(self):
        report = run_preflight(
            cli="multica",
            project_id=PROJECT,
            assignee_id=SQUAD,
            cases_dir="cases",
            arena_repo="Karma1996/kar-sim-arena",
            env={},
            run=FakeCli(),
            gh_run=lambda _args: (1, "", "not logged in"),
            check_gh=True,
        )
        arena = next(c for c in report.checks if c.name == "arena")
        self.assertFalse(arena.ok)
        self.assertIn("gh", arena.detail)


class LiveGuardTest(unittest.TestCase):
    """A live run must not create an issue before the identity is verified."""

    def test_live_smoke_needs_a_project_and_assignee(self):
        self.assertEqual(main(["smoke", "--cases", "cases"]), 2)

    def test_live_smoke_aborts_when_the_identity_is_wrong(self):
        with mock.patch("runner.__main__.verify", side_effect=IdentityError("not the human")):
            self.assertEqual(
                main(["smoke", "--cases", "cases", "--project", "p", "--assignee", "a"]),
                2,
            )

    def test_live_smoke_announces_the_verified_identity(self):
        member = type("M", (), {"name": "KAR-SIM-Human", "email": "sim@example.com", "id": "m"})
        with mock.patch("runner.__main__.verify", return_value=member), mock.patch(
            "runner.__main__.load_smoke", return_value=[]
        ):
            self.assertEqual(
                main(["smoke", "--cases", "cases", "--project", "p", "--assignee", "a"]), 0
            )


if __name__ == "__main__":
    unittest.main()
