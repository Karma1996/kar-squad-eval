"""The platform seam.

``Platform`` is the only surface the runner has on Multica. The CLI adapter
talks to the real workspace; ``FakePlatform`` replays a dry-run script so the
whole loop and every recorder rule can be exercised without a workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol

from .clock import Clock


@dataclass(frozen=True)
class Issue:
    id: str
    identifier: str
    title: str
    status: str
    assignee_id: str | None = None
    assignee_type: str | None = None


@dataclass(frozen=True)
class Comment:
    id: str
    issue_id: str
    author_id: str
    author_type: str  # "agent" | "human"
    body: str
    created_at: datetime
    parent_id: str | None = None


class Platform(Protocol):
    def create_issue(
        self,
        *,
        title: str,
        body: str,
        project_id: str | None = None,
        assignee_id: str | None = None,
    ) -> Issue: ...

    def get_issue(self, issue_id: str) -> Issue: ...

    def list_comments(self, issue_id: str) -> list[Comment]: ...

    def post_comment(
        self,
        issue_id: str,
        body: str,
        parent_id: str | None = None,
        *,
        author_type: str | None = None,
    ) -> Comment: ...


@dataclass
class _FakeIssue:
    id: str
    identifier: str
    title: str
    status: str
    body: str
    assignee_id: str | None
    created_at: datetime


class FakePlatform:
    """In-memory platform driven by a dry-run script.

    Script steps are dicts, either::

        {"at_minutes": 3, "comment": {"author_type": "agent",
                                      "author": "KAR-01-Lead", "body": "..."}}
        {"at_minutes": 30, "record": {"type": "ci", "status": "green", "sha": "..."}}
        {"at_minutes": 31, "status": "done"}

    ``advance(now)`` emits every due step and returns the ``record`` payloads
    for the loop to append to the case record.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock
        self.issues: dict[str, _FakeIssue] = {}
        self.comments: list[Comment] = []
        self._next_issue = 1
        self._next_comment = 1
        self._script: list[tuple[datetime, Mapping[str, Any]]] = []
        self._script_cursor = 0
        self._start: datetime | None = None

    # script ---------------------------------------------------------------
    def load_script(self, steps: list[Mapping[str, Any]], start: datetime) -> None:
        self._start = start
        parsed: list[tuple[datetime, Mapping[str, Any]]] = []
        for step in steps:
            minutes = float(step.get("at_minutes", 0))
            parsed.append((start + timedelta(minutes=minutes), step))
        self._script = sorted(parsed, key=lambda item: item[0])
        self._script_cursor = 0

    def advance(self, now: datetime) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        issue = next(iter(self.issues.values()), None)
        while self._script_cursor < len(self._script):
            at, step = self._script[self._script_cursor]
            if at > now:
                break
            self._script_cursor += 1
            if "comment" in step and issue is not None:
                spec = step["comment"]
                self._post(
                    issue,
                    body=str(spec.get("body", "")),
                    parent_id=spec.get("parent_id"),
                    author_type=str(spec.get("author_type", "agent")),
                    author=str(spec.get("author", "KAR-01-Lead")),
                    at=at,
                )
            elif "record" in step:
                payload = dict(step["record"])
                payload.setdefault("t", at.isoformat())
                events.append(payload)
            elif "status" in step and issue is not None:
                issue.status = str(step["status"])
            elif "comment" in step:
                raise RuntimeError("script comment before the issue exists")
        return events

    # platform -------------------------------------------------------------
    def create_issue(
        self,
        *,
        title: str,
        body: str,
        project_id: str | None = None,
        assignee_id: str | None = None,
    ) -> Issue:
        number = self._next_issue
        self._next_issue += 1
        issue = _FakeIssue(
            id=f"issue-{number}",
            identifier=f"KAR-{100 + number}",
            title=title,
            status="todo",
            body=body,
            assignee_id=assignee_id,
            created_at=self._now(),
        )
        self.issues[issue.id] = issue
        return _issue_view(issue)

    def get_issue(self, issue_id: str) -> Issue:
        return _issue_view(self.issues[issue_id])

    def list_comments(self, issue_id: str) -> list[Comment]:
        return [c for c in self.comments if c.issue_id == issue_id]

    def post_comment(
        self,
        issue_id: str,
        body: str,
        parent_id: str | None = None,
        *,
        author_type: str | None = None,
    ) -> Comment:
        issue = self.issues.get(issue_id)
        if issue is None:
            raise KeyError(issue_id)
        canonical = "human" if author_type in {"human", "member"} else "agent"
        return self._post(
            issue,
            body=body,
            parent_id=parent_id,
            author_type=canonical,
            author="KAR-SIM-Human" if canonical == "human" else "fake-agent",
            at=self._now(),
        )

    def set_status(self, issue_id: str, status: str) -> None:
        self.issues[issue_id].status = status

    # helpers --------------------------------------------------------------
    def _now(self) -> datetime:
        if self.clock is not None:
            return self.clock.now()
        return datetime.now().astimezone()

    def _post(
        self,
        issue: _FakeIssue,
        *,
        body: str,
        parent_id: str | None,
        author_type: str,
        author: str,
        at: datetime,
    ) -> Comment:
        comment_id = f"c{self._next_comment}"
        self._next_comment += 1
        canonical = "human" if author_type in {"human", "member"} else "agent"
        comment = Comment(
            id=comment_id,
            issue_id=issue.id,
            author_id=author,
            author_type=canonical,
            body=body,
            created_at=at,
            parent_id=parent_id,
        )
        self.comments.append(comment)
        return comment


def _issue_view(issue: _FakeIssue) -> Issue:
    return Issue(
        id=issue.id,
        identifier=issue.identifier,
        title=issue.title,
        status=issue.status,
        assignee_id=issue.assignee_id,
    )
