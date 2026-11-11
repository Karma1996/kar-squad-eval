"""Preflight for a live simulation.

Every live run creates real issues, wakes the real squad and spends real tokens,
so the cheap mistakes must fail before the first run, not after four hours of
polling. ``preflight`` performs only read-only calls: it checks the shell
environment, the CLI identity, the sandbox project, the assignee, the frozen
case pins and (optionally) the arena repository.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .identity import IdentityError, Member, check_task_environment, verify
from .scenario import ScenarioError, load_scenario, load_smoke

Run = Callable[[Sequence[str]], "tuple[int, str, str]"]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def render(self) -> str:
        lines = []
        for check in self.checks:
            mark = "ok  " if check.ok else "FAIL"
            lines.append(f"[{mark}] {check.name}: {check.detail}")
        lines.append("")
        lines.append("preflight passed" if self.ok else "preflight failed")
        return "\n".join(lines)


def _subprocess_run(cli: str) -> Run:
    def run(args: Sequence[str]) -> tuple[int, str, str]:
        completed = subprocess.run(
            [cli, *args], capture_output=True, text=True, timeout=180, check=False
        )
        return completed.returncode, completed.stdout, completed.stderr

    return run


def _json(run: Run, args: Sequence[str]) -> Any:
    code, out, err = run(args)
    if code != 0:
        raise RuntimeError((err or out or "command failed").strip()[:300])
    return json.loads(out)


def _identity_check(cli: str, name: str, env: Mapping[str, str] | None, run: Run) -> Check:
    try:
        member: Member = verify(cli, name, env=env, run=run)
    except IdentityError as exc:
        return Check("identity", False, str(exc))
    return Check(
        "identity",
        True,
        f"CLI acts as {member.name} ({member.email}), workspace member {member.id}",
    )


def _environment_check(env: Mapping[str, str] | None) -> Check:
    try:
        check_task_environment(env)
    except IdentityError as exc:
        return Check("environment", False, str(exc))
    return Check("environment", True, "no MULTICA_TOKEN in the environment")


def _project_check(project_id: str, run: Run) -> Check:
    try:
        raw = _json(run, ["project", "get", project_id, "--output", "json"])
    except (RuntimeError, json.JSONDecodeError) as exc:
        return Check("project", False, f"cannot read project {project_id}: {exc}")
    if not isinstance(raw, Mapping) or "id" not in raw:
        return Check("project", False, f"project {project_id} returned no id")
    return Check(
        "project",
        True,
        f"{raw.get('title', '?')} ({raw['id']}), visibility={raw.get('visibility', '?')}",
    )


def _assignee_check(assignee_id: str, run: Run) -> Check:
    errors: list[str] = []
    for command, what in (("squad", "squad"), ("agent", "agent")):
        try:
            raw = _json(run, [command, "get", assignee_id, "--output", "json"])
        except (RuntimeError, json.JSONDecodeError) as exc:
            errors.append(f"{what}: {exc}")
            continue
        if isinstance(raw, Mapping) and "id" in raw:
            return Check("assignee", True, f"{what} {raw.get('name', '?')} ({raw['id']})")
        errors.append(f"{what}: payload has no id")
    return Check("assignee", False, f"no squad/agent with id {assignee_id}; " + " | ".join(errors))


def _cases_check(cases_dir: str) -> Check:
    try:
        smoke = load_smoke(cases_dir)
    except ScenarioError as exc:
        return Check("cases", False, str(exc))
    if not smoke:
        return Check("cases", False, "the smoke set is empty")
    unpinned = [scenario.id for scenario in smoke if not scenario.pinned]
    if unpinned:
        return Check("cases", False, f"not pinned to an arena sha: {', '.join(unpinned)}")
    shas = sorted({scenario.arena.base_sha[:7] for scenario in smoke})
    return Check(
        "cases",
        True,
        f"{len(smoke)} smoke cases pinned to arena {', '.join(shas)} "
        f"({', '.join(s.id for s in smoke)})",
    )


def _gh_run() -> Run:
    def run(args: Sequence[str]) -> tuple[int, str, str]:
        completed = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=120, check=False
        )
        return completed.returncode, completed.stdout, completed.stderr

    return run


def _arena_check(repo: str, run: Run) -> Check:
    try:
        raw = _json(run, ["repo", "view", repo, "--json", "nameWithOwner,isPrivate,defaultBranchRef"])
    except (RuntimeError, json.JSONDecodeError) as exc:
        return Check("arena", False, f"cannot read {repo} through gh: {exc}")
    branch = ""
    if isinstance(raw, Mapping) and isinstance(raw.get("defaultBranchRef"), Mapping):
        branch = str(raw["defaultBranchRef"].get("name", ""))
    return Check(
        "arena",
        True,
        f"{raw.get('nameWithOwner', repo) if isinstance(raw, Mapping) else repo} "
        f"(private={raw.get('isPrivate', '?') if isinstance(raw, Mapping) else '?'}"
        + (f", default={branch}" if branch else "")
        + ")",
    )


def run_preflight(
    *,
    cli: str,
    project_id: str,
    assignee_id: str,
    human: str = "KAR-SIM-Human",
    cases_dir: str = "cases",
    arena_repo: str | None = None,
    env: Mapping[str, str] | None = None,
    run: Run | None = None,
    gh_run: Run | None = None,
    check_gh: bool = True,
) -> Preflight:
    run = run or _subprocess_run(cli)
    report = Preflight()
    report.checks.append(_environment_check(env))
    report.checks.append(_identity_check(cli, human, env, run))
    report.checks.append(_project_check(project_id, run))
    report.checks.append(_assignee_check(assignee_id, run))
    report.checks.append(_cases_check(cases_dir))
    if arena_repo and check_gh:
        if shutil.which("gh") is None:
            report.checks.append(Check("arena", False, "gh is not on PATH"))
        else:
            report.checks.append(_arena_check(arena_repo, gh_run or _gh_run()))
    return report
