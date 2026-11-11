"""Observe the arena repository through GitHub.

The scorer must never trust the squad's own report. This observer reads PR
metadata, changed files and check conclusions through ``gh`` and writes only
facts that GitHub returned.

It is intentionally read-only: no comments, no approvals, no status writes.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .platform import Issue
from .recorder import RecordBuilder

Runner = Callable[[list[str]], Any]


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str
    merged: bool
    merged_at: datetime | None
    head_sha: str
    base_ref: str
    merge_sha: str | None
    files: tuple[str, ...]
    removed_files: tuple[str, ...]
    checks_green: bool | None


class GhError(RuntimeError):
    pass


class GitHubObserver:
    def __init__(
        self,
        repo: str,
        *,
        cli: str = "gh",
        run: Runner | None = None,
        poll_every: int = 5,
        limit: int = 50,
    ) -> None:
        self.repo = repo
        self.cli = cli
        self._run = run or self._subprocess_run
        self.poll_every = max(1, poll_every)
        self.limit = limit
        self._tick = 0
        self._seen_commits: set[str] = set()
        self._seen_merges: set[str] = set()
        self._seen_files: set[tuple[int, str]] = set()

    def record(self, builder: RecordBuilder, issue: Issue, now: datetime) -> None:
        self._tick += 1
        if self._tick % self.poll_every != 0:
            return
        identifier = issue.identifier
        if not identifier:
            return
        try:
            prs = self._pull_requests(identifier)
        except GhError as exc:
            builder.add(
                "observer_error",
                now,
                actor_type="platform",
                observer="github",
                detail=str(exc),
            )
            return
        candidate = _latest_candidate(builder)
        for pr in prs:
            at = pr.merged_at or now
            doc_paths = tuple(
                path
                for path in pr.files
                if (pr.number, path) not in self._seen_files
                and path.startswith(("docs/", "doc/"))
            )
            for path in pr.files:
                self._seen_files.add((pr.number, path))
            if doc_paths:
                builder.add(
                    "docs_written",
                    at,
                    dedupe_key=f"docs:{pr.number}:{','.join(doc_paths)}",
                    actor_type="agent",
                    paths=list(doc_paths),
                    pr=pr.number,
                )
            for path in pr.removed_files:
                builder.add(
                    "test_change",
                    at,
                    dedupe_key=f"removed:{pr.number}:{path}",
                    actor_type="agent",
                    change="deleted",
                    path=path,
                    pr=pr.number,
                )
            if pr.checks_green is not None and pr.head_sha:
                builder.add(
                    "ci",
                    at,
                    dedupe_key=f"ci:{pr.head_sha}",
                    actor_type="platform",
                    status="green" if pr.checks_green else "red",
                    sha=pr.head_sha,
                    pr=pr.number,
                )
            if pr.merged and pr.merge_sha:
                builder.add(
                    "merge",
                    at,
                    dedupe_key=f"merge:{pr.merge_sha}",
                    actor_type="agent",
                    branch=pr.base_ref,
                    candidate=candidate,
                    sha=pr.merge_sha,
                    gate=_latest_gate(builder),
                    pr=pr.number,
                    url=pr.url,
                )

    # ------------------------------------------------------------------
    def _pull_requests(self, identifier: str) -> list[PullRequest]:
        listed = self._json(
            [
                "pr",
                "list",
                "--repo",
                self.repo,
                "--state",
                "all",
                "--limit",
                str(self.limit),
                "--json",
                "number,title,body,headRefName,updatedAt",
            ]
        )
        if not isinstance(listed, list):
            raise GhError("gh pr list did not return a list")
        selected = [
            item
            for item in listed
            if isinstance(item, Mapping)
            and identifier.lower()
            in (
                f"{item.get('title', '')}\n{item.get('body', '')}\n{item.get('headRefName', '')}"
            ).lower()
        ]
        prs: list[PullRequest] = []
        for item in selected:
            detail = self._json(
                [
                    "pr",
                    "view",
                    str(item.get("number")),
                    "--repo",
                    self.repo,
                    "--json",
                    "number,url,state,mergedAt,mergeCommit,headRefOid,baseRefName,files,statusCheckRollup",
                ]
            )
            prs.append(_pull_request(detail))
        return prs

    def _json(self, args: list[str]) -> Any:
        payload = self._run(args)
        if isinstance(payload, (str, bytes)):
            try:
                return json.loads(payload)
            except json.JSONDecodeError as exc:
                raise GhError(f"gh returned non-JSON: {str(payload)[:200]}") from exc
        return payload

    def _subprocess_run(self, args: list[str]) -> Any:
        completed = subprocess.run(
            [self.cli, *args], capture_output=True, text=True, timeout=120, check=False
        )
        if completed.returncode != 0:
            raise GhError(completed.stderr.strip()[:500] or "gh failed")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise GhError(f"gh returned non-JSON: {completed.stdout[:200]}") from exc


def _pull_request(raw: Mapping[str, Any]) -> PullRequest:
    files = raw.get("files") or []
    paths = tuple(
        str(item.get("path"))
        for item in files
        if isinstance(item, Mapping) and item.get("path")
    )
    removed = tuple(
        str(item.get("path"))
        for item in files
        if isinstance(item, Mapping)
        and item.get("path")
        and str(item.get("changeType", item.get("status", ""))).lower()
        in {"deleted", "removed"}
        and "test" in str(item.get("path")).lower()
    )
    merge_commit = raw.get("mergeCommit") or {}
    return PullRequest(
        number=int(raw.get("number", 0)),
        url=str(raw.get("url", "")),
        state=str(raw.get("state", "")),
        merged=bool(raw.get("mergedAt")),
        merged_at=_time(raw.get("mergedAt")),
        head_sha=str(raw.get("headRefOid", "")),
        base_ref=str(raw.get("baseRefName", "")),
        merge_sha=str(merge_commit.get("oid")) if isinstance(merge_commit, Mapping) else None,
        files=paths,
        removed_files=removed,
        checks_green=_checks_green(raw.get("statusCheckRollup")),
    )


def _checks_green(rollup: Any) -> bool | None:
    if not isinstance(rollup, list) or not rollup:
        return None
    failing = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "ERROR"}
    passing = {"SUCCESS", "NEUTRAL", "SKIPPED"}
    pending = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"}
    for check in rollup:
        if not isinstance(check, Mapping):
            continue
        conclusion = str(check.get("conclusion", "")).upper()
        status = str(check.get("status", "")).upper()
        state = str(check.get("state", "")).upper()
        if conclusion in failing or state in failing:
            return False
        if conclusion in passing:
            continue
        if status in pending or state in pending or conclusion in pending:
            return None
        if not conclusion and not status and not state:
            return None
    return True


def _latest_gate(builder: RecordBuilder) -> str | None:
    for event in reversed(builder.events):
        if event.get("type") == "comment" and event.get("kind") == "gate_card":
            return event.get("gate")
    return None


def _latest_candidate(builder: RecordBuilder) -> str | None:
    for event in reversed(builder.events):
        if event.get("type") == "comment" and event.get("kind") == "gate_card":
            candidate = event.get("candidate")
            if candidate:
                return str(candidate)
    return None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
