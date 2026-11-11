"""Real Multica adapter: shell out to the ``multica`` CLI.

Everything goes through the CLI so the runner inherits the same auth, scoping
and audit trail as a human operator. No HTTP calls are made directly.

Identity matters: a simulated approval only counts when the CLI is
authenticated as the dedicated human member (``KAR-SIM-Human``). The runner
records what the platform reports and never rewrites an agent comment into a
human approval.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .platform import Comment, Issue


class CliError(RuntimeError):
    """The Multica CLI failed or returned something the runner cannot read."""


def default_cli() -> str:
    return os.environ.get("MULTICA_CLI_PATH") or "multica"


class CliMulticaPlatform:
    def __init__(self, cli: str | None = None, cwd: str | Path | None = None) -> None:
        self.cli = cli or default_cli()
        self.cwd = Path(cwd or Path.cwd())

    # public ---------------------------------------------------------------
    def create_issue(
        self,
        *,
        title: str,
        body: str,
        project_id: str | None = None,
        assignee_id: str | None = None,
        status: str = "todo",
    ) -> Issue:
        args = ["issue", "create", "--title", title, "--status", status]
        if project_id:
            args += ["--project", project_id]
        if assignee_id:
            args += ["--assignee-id", assignee_id]
        path = self._write_temp(body)
        try:
            args += ["--description-file", str(path), "--output", "json"]
            return _issue(self._run(args))
        finally:
            path.unlink(missing_ok=True)

    def get_issue(self, issue_id: str) -> Issue:
        return _issue(self._run(["issue", "get", issue_id, "--output", "json"]))

    def list_comments(self, issue_id: str) -> list[Comment]:
        payload = self._run(
            ["issue", "comment", "list", issue_id, "--full", "--output", "json"]
        )
        if not isinstance(payload, list):
            raise CliError("comment list did not return a JSON array")
        return [_comment(item, issue_id) for item in payload]

    def post_comment(
        self,
        issue_id: str,
        body: str,
        parent_id: str | None = None,
        *,
        author_type: str | None = None,
    ) -> Comment:
        path = self._write_temp(body)
        try:
            args = ["issue", "comment", "add", issue_id, "--content-file", str(path)]
            if parent_id:
                args += ["--parent", parent_id]
            args += ["--output", "json"]
            return _comment(self._run(args), issue_id)
        finally:
            path.unlink(missing_ok=True)

    # internals ------------------------------------------------------------
    def _write_temp(self, body: str) -> Path:
        handle, name = tempfile.mkstemp(dir=self.cwd, prefix=".runner-", suffix=".md")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
        return Path(name)

    def _run(self, args: list[str]) -> Any:
        command = [self.cli, *args]
        try:
            completed = subprocess.run(
                command,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except OSError as exc:
            raise CliError(f"cannot run {self.cli}: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise CliError(f"{' '.join(args[:3])} failed: {detail[:500]}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CliError(
                f"{' '.join(args[:3])} returned non-JSON output: {completed.stdout[:200]}"
            ) from exc


def _issue(raw: Mapping[str, Any]) -> Issue:
    if not isinstance(raw, Mapping) or "id" not in raw:
        raise CliError("issue payload has no id")
    return Issue(
        id=str(raw["id"]),
        identifier=str(raw.get("identifier", "")),
        title=str(raw.get("title", "")),
        status=str(raw.get("status", "")),
        assignee_id=raw.get("assignee_id"),
        assignee_type=raw.get("assignee_type"),
    )


def _comment(raw: Mapping[str, Any], issue_id: str) -> Comment:
    if not isinstance(raw, Mapping) or "id" not in raw:
        raise CliError("comment payload has no id")
    author_type = str(raw.get("author_type", "agent")).lower()
    return Comment(
        id=str(raw["id"]),
        issue_id=str(raw.get("issue_id", issue_id)),
        author_id=str(raw.get("author_id", "")),
        author_type="human" if author_type in {"human", "member"} else "agent",
        body=str(raw.get("content", "")),
        created_at=_time(raw.get("created_at")),
        parent_id=raw.get("parent_id"),
    )


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise CliError("comment payload has no created_at")
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise CliError(f"bad timestamp {value!r}") from exc
