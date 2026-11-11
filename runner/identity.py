"""Who is the runner authenticated as?

A live simulation only means something when the comments the runner posts are
authored by the dedicated human member ``KAR-SIM-Human``. Two failure modes are
silent without an explicit check:

1. An agent run exports ``MULTICA_TOKEN``; the CLI then authenticates as that
   task token no matter which profile is selected.
2. A missing or misdirected profile: never assume ``--profile`` took effect.
   Inside an agent run, for instance, ``multica --profile kar-sim-human auth
   status`` still reports the operator's own account.

Both are caught here by comparing ``auth status`` against the workspace member
list, before the runner creates any issue. Verification is read-only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

Run = Callable[[Sequence[str]], "tuple[int, str, str]"]

_AUTH_USER = re.compile(r"^User:\s*(?P<name>.+?)\s*(?:\((?P<email>[^()]+)\))?\s*$")


class IdentityError(RuntimeError):
    """The CLI identity is not the dedicated simulation human."""


@dataclass(frozen=True)
class CliIdentity:
    """What ``multica auth status`` reports for the CLI that will run."""

    name: str
    email: str = ""

    def label(self) -> str:
        return f"{self.name} ({self.email})" if self.email else self.name


@dataclass(frozen=True)
class Member:
    """A workspace member row, as far as identity checks need it."""

    id: str
    user_id: str
    name: str
    email: str
    role: str


def parse_auth_status(text: str) -> CliIdentity | None:
    """Parse the human-readable ``auth status`` output."""
    for line in text.splitlines():
        match = _AUTH_USER.match(line.strip())
        if match:
            return CliIdentity(
                name=match.group("name").strip(),
                email=(match.group("email") or "").strip(),
            )
    return None


def parse_members(payload: Any) -> list[Member]:
    if not isinstance(payload, list):
        raise IdentityError("workspace member list did not return a JSON array")
    members: list[Member] = []
    for raw in payload:
        if not isinstance(raw, Mapping) or "id" not in raw:
            raise IdentityError("workspace member row has no id")
        members.append(
            Member(
                id=str(raw["id"]),
                user_id=str(raw.get("user_id", "")),
                name=str(raw.get("name", "")),
                email=str(raw.get("email", "")),
                role=str(raw.get("role", "")),
            )
        )
    return members


def find_member(members: Sequence[Member], name: str) -> Member | None:
    wanted = name.strip().casefold()
    return next((m for m in members if m.name.strip().casefold() == wanted), None)


def find_daemon_task_marker(start: str | Path | None = None) -> Path | None:
    """Return the daemon task marker above ``start``, if there is one.

    A worktree created by the Multica daemon carries
    ``.multica/daemon_task_context.json``. While that marker is above the
    working directory the CLI refuses to use local profiles at all
    (``daemon-managed task requires a task-local Multica config root``), so the
    live runner must run from a plain clone elsewhere.
    """
    current = Path(start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        marker = directory / ".multica" / "daemon_task_context.json"
        if marker.is_file():
            return marker
    return None


def check_task_environment(
    env: Mapping[str, str] | None = None, cwd: str | Path | None = None
) -> None:
    """Refuse to run inside a Multica agent run or daemon task worktree.

    The CLI detects an agent execution context (``MULTICA_AGENT_ID``) and then
    authenticates as the task token (``MULTICA_TOKEN``) regardless of
    ``--profile``. Unsetting only ``MULTICA_TOKEN`` is not enough: the CLI then
    refuses with ``agent execution context requires MULTICA_TOKEN``. The task
    worktree is refused too, because its daemon marker disables local profiles.
    The live runner has to start from a plain terminal and a plain directory.
    """
    env = os.environ if env is None else env
    token = env.get("MULTICA_TOKEN")
    agent = env.get("MULTICA_AGENT_ID")
    if token or agent:
        where = ", ".join(
            name for name in ("MULTICA_AGENT_ID", "MULTICA_TOKEN") if env.get(name)
        )
        raise IdentityError(
            f"this shell is a Multica agent run ({where} set), so the CLI would "
            "authenticate as that task token and ignore the human profile. Run the "
            "live simulation from a plain terminal outside the agent run."
        )
    marker = find_daemon_task_marker(cwd)
    if marker is not None:
        raise IdentityError(
            f"the working directory belongs to a Multica daemon task ({marker}), "
            "where the CLI refuses to use local profiles. Clone the eval repo to an "
            "ordinary directory and run from there, e.g. "
            "`git clone git@github.com:Karma1996/kar-squad-eval.git ~/projects/kar-squad-eval`."
        )


def _subprocess_run(cli: str) -> Run:
    def run(args: Sequence[str]) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                [cli, *args],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except OSError as exc:
            raise IdentityError(f"cannot run {cli}: {exc}") from exc
        return completed.returncode, completed.stdout, completed.stderr

    return run


def _json(run: Run, args: Sequence[str], what: str) -> Any:
    code, out, err = run(args)
    if code != 0:
        detail = (err or out or "").strip()
        raise IdentityError(f"{what} failed: {detail[:300]}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise IdentityError(f"{what} returned non-JSON output: {out[:200]}") from exc


def _cli_identity(run: Run) -> CliIdentity:
    code, out, err = run(["auth", "status"])
    if code != 0:
        detail = (err or out or "").strip()
        raise IdentityError(f"auth status failed: {detail[:300]}")
    identity = parse_auth_status(out)
    if identity is None:
        raise IdentityError(f"cannot read the identity from auth status: {out[:200]}")
    return identity


def verify(
    cli: str,
    expected_name: str = "KAR-SIM-Human",
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    run: Run | None = None,
) -> Member:
    """Fail unless the CLI acts as the expected human workspace member.

    Returns the matching member row so callers can log the verified identity.
    """
    check_task_environment(env, cwd)
    run = run or _subprocess_run(cli)

    identity = _cli_identity(run)
    members = parse_members(
        _json(run, ["workspace", "member", "list", "--output", "json"], "member list")
    )
    member = find_member(members, expected_name)
    if member is None:
        raise IdentityError(
            f"workspace has no member named {expected_name!r}; known members: "
            + ", ".join(sorted(m.name for m in members))
        )
    if member.role != "member":
        raise IdentityError(
            f"{expected_name} has role {member.role!r}; the simulation human must be a "
            "plain member, not an owner/admin."
        )
    if member.user_id and identity.email and identity.email.casefold() != member.email.casefold():
        raise IdentityError(
            f"CLI authenticates as {identity.label()} but {expected_name} is "
            f"{member.name} ({member.email}). Create the profile with "
            "`multica --profile kar-sim-human login --token` and verify from a plain "
            "terminal that `multica --profile kar-sim-human auth status` reports "
            f"{expected_name}."
        )
    if identity.name.casefold() != member.name.casefold():
        raise IdentityError(
            f"CLI authenticates as {identity.label()}, not {expected_name} "
            f"({member.name} <{member.email}>). Check the profile or MULTICA_TOKEN."
        )
    return member
