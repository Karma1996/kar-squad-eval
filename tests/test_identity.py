"""Identity verification before a live run.

The dangerous case is not "nobody logged in" (the CLI fails loudly) but "the
requested profile does not exist, so the CLI silently falls back to the default
profile" — approvals then come from the operator's own account while looking
like human approvals. These tests pin that behaviour down.
"""

from __future__ import annotations

import json
import unittest

from runner.identity import (
    CliIdentity,
    IdentityError,
    check_task_environment,
    find_member,
    parse_auth_status,
    parse_members,
    verify,
)

MEMBERS = [
    {
        "id": "9791acef-1d97-44f2-8502-0e7391fc1a7b",
        "user_id": "d9833006-05ed-42cb-ad9b-9c4f38c58bb4",
        "name": "KAR-SIM-Human",
        "email": "sim@example.com",
        "role": "member",
        "workspace_id": "ws",
    },
    {
        "id": "member-owner",
        "user_id": "user-owner",
        "name": "karma",
        "email": "owner@example.com",
        "role": "owner",
    },
]


class FakeCli:
    def __init__(self, *, auth: str, members=None) -> None:
        self.auth = auth
        self.members = MEMBERS if members is None else members
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        if list(args) == ["auth", "status"]:
            return 0, self.auth, ""
        if list(args) == ["workspace", "member", "list", "--output", "json"]:
            return 0, json.dumps(self.members), ""
        return 1, "", f"unexpected {args}"


class ParseTest(unittest.TestCase):
    def test_reads_name_and_email(self):
        identity = parse_auth_status("Server:  https://api.multica.ai\nUser:    karma (a@b.c)\n")
        self.assertEqual(identity, CliIdentity("karma", "a@b.c"))

    def test_reads_name_without_email(self):
        self.assertEqual(parse_auth_status("User: KAR-SIM-Human"), CliIdentity("KAR-SIM-Human"))

    def test_returns_none_without_a_user_line(self):
        self.assertIsNone(parse_auth_status("Server: https://api.multica.ai\n"))

    def test_rejects_non_list_member_payload(self):
        with self.assertRaises(IdentityError):
            parse_members({"members": []})

    def test_find_member_is_case_insensitive(self):
        members = parse_members(MEMBERS)
        self.assertEqual(find_member(members, "kar-sim-human").id, MEMBERS[0]["id"])
        self.assertIsNone(find_member(members, "nobody"))


class TaskEnvironmentTest(unittest.TestCase):
    def test_accepts_a_plain_shell(self):
        check_task_environment({"PATH": "/bin"})

    def test_rejects_an_agent_task_token(self):
        with self.assertRaises(IdentityError) as caught:
            check_task_environment({"MULTICA_TOKEN": "mat_x", "MULTICA_TASK_ID": "t-1"})
        self.assertIn("MULTICA_TOKEN", str(caught.exception))

    def test_rejects_an_agent_context_without_a_token(self):
        with self.assertRaises(IdentityError) as caught:
            check_task_environment({"MULTICA_AGENT_ID": "agent-1"})
        self.assertIn("plain terminal", str(caught.exception))


class VerifyTest(unittest.TestCase):
    def test_passes_when_the_cli_is_the_simulation_human(self):
        cli = FakeCli(auth="User:    KAR-SIM-Human (sim@example.com)")
        member = verify("multica", run=cli, env={})
        self.assertEqual(member.id, MEMBERS[0]["id"])

    def test_refuses_before_any_cli_call_under_a_task_token(self):
        def boom(_args):
            raise AssertionError("must not call the CLI with a task token")

        with self.assertRaises(IdentityError):
            verify("multica", run=boom, env={"MULTICA_TOKEN": "mat_x"})

    def test_catches_the_cli_acting_as_the_operator(self):
        cli = FakeCli(auth="User:    karma (owner@example.com)")
        with self.assertRaises(IdentityError) as caught:
            verify("multica", run=cli, env={})
        message = str(caught.exception)
        self.assertIn("authenticates as karma", message)
        self.assertIn("kar-sim-human", message)

    def test_requires_the_member_to_exist(self):
        cli = FakeCli(auth="User: KAR-SIM-Human (sim@example.com)", members=MEMBERS[1:])
        with self.assertRaises(IdentityError) as caught:
            verify("multica", run=cli, env={})
        self.assertIn("no member named", str(caught.exception))

    def test_requires_a_plain_member_role(self):
        members = [dict(MEMBERS[0], role="owner")]
        cli = FakeCli(auth="User: KAR-SIM-Human (sim@example.com)", members=members)
        with self.assertRaises(IdentityError) as caught:
            verify("multica", run=cli, env={})
        self.assertIn("role", str(caught.exception))

    def test_rejects_a_same_email_different_name(self):
        cli = FakeCli(auth="User: Another (sim@example.com)")
        with self.assertRaises(IdentityError) as caught:
            verify("multica", run=cli, env={})
        self.assertIn("not KAR-SIM-Human", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
