"""The polling loop: drive one case to a record.

Stop conditions are explicit and always end up in ``runner.stop_reason``: an
issue reaching ``done``/``cancelled``, or the wall-clock budget running out.
A timeout is a legitimate result (adversarial silence cases end that way); it
is recorded, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .clock import Clock
from .contract import (
    GateAssigner,
    GateCardView,
    candidate_of,
    card_fields,
    has_approve_line,
    is_gate_card,
    parse_lane,
    visible_lines,
)
from .platform import Comment, FakePlatform, Issue, Platform
from .recorder import RecordBuilder
from .scenario import Scenario
from .sim_human import SimHuman


class Observer(Protocol):
    def record(self, builder: RecordBuilder, issue: Issue, now: datetime) -> None: ...


class RunnerError(RuntimeError):
    """The run cannot continue truthfully."""


@dataclass
class RunConfig:
    project_id: str | None = None
    assignee_id: str | None = None
    poll_seconds: float = 60.0
    max_minutes: float = 240.0
    squad_version: str = "4.4.0"
    mode: str = "dry-run"
    human_author: str = "KAR-SIM-Human"
    stop_statuses: tuple[str, ...] = ("done", "cancelled")
    observer: Observer | None = None


@dataclass
class RunResult:
    record: dict[str, Any]
    stop_reason: str
    issue: Issue
    comments_seen: int = 0


def run_case(
    scenario: Scenario,
    platform: Platform,
    clock: Clock,
    config: RunConfig | None = None,
    *,
    script: list[Mapping[str, Any]] | None = None,
    out: str | None = None,
) -> RunResult:
    config = config or RunConfig()
    if config.mode != "dry-run" and not scenario.pinned:
        raise RunnerError(
            f"{scenario.id} is not pinned to an arena commit "
            f"(base_sha={scenario.arena.base_sha!r}); a live run would move with main"
        )
    started = clock.now()
    builder = RecordBuilder(scenario, config.squad_version, started)
    human = SimHuman(scenario.human_policy, author=config.human_author)
    assigner = GateAssigner(scenario.required_gates)
    seen: set[str] = set()
    cards: list[GateCardView] = []
    card_index: dict[str, GateCardView] = {}

    if script is not None:
        if not isinstance(platform, FakePlatform):
            raise RunnerError("a dry-run script only makes sense with FakePlatform")
        platform.load_script(list(script), started)

    issue = platform.create_issue(
        title=scenario.title,
        body=scenario.intent,
        project_id=config.project_id,
        assignee_id=config.assignee_id,
    )
    builder.add(
        "issue_created",
        started,
        actor_type="human",
        actor=config.human_author,
        issue=issue.identifier,
        title=issue.title,
        arena_base_sha=scenario.arena.base_sha,
    )

    stop_reason = "timeout"
    deadline = started.timestamp() + config.max_minutes * 60.0
    last_status = issue.status

    while True:
        clock.sleep(config.poll_seconds)
        now = clock.now()

        if isinstance(platform, FakePlatform):
            for payload in platform.advance(now):
                copied = dict(payload)
                event_type = str(copied.pop("type"))
                at = _as_time(copied.pop("t", None)) or now
                builder.add(event_type, at, **copied)

        issue = platform.get_issue(issue.id)
        if issue.status != last_status:
            builder.add(
                "issue_status",
                now,
                actor_type="platform",
                status=issue.status,
            )
            last_status = issue.status

        for comment in platform.list_comments(issue.id):
            if comment.id in seen:
                continue
            seen.add(comment.id)
            _ingest(
                builder,
                comment,
                issue,
                assigner,
                cards,
                card_index,
            )

        if config.observer is not None:
            config.observer.record(builder, issue, now)

        for decision in human.due(cards, now):
            comment = platform.post_comment(
                issue.id,
                decision.body,
                decision.parent_id,
                author_type="human",
            )
            if decision.decision == "approve" and comment.author_type != "human":
                raise RunnerError(
                    "an approval was posted by a non-human identity; the run would "
                    "score as merge-without-approval. Run the live runner with the "
                    "KAR-SIM-Human CLI identity (see README)."
                )
            _record_human_decision(builder, comment, decision, card_index)

        if issue.status in config.stop_statuses:
            stop_reason = f"issue_status:{issue.status}"
            break
        if now.timestamp() >= deadline:
            stop_reason = "timeout"
            break

    finished = clock.now()
    record = builder.finalize(
        out,
        meta={
            "mode": config.mode,
            "stop_reason": stop_reason,
            "started_at": started.astimezone(timezone.utc).isoformat(),
            "finished_at": finished.astimezone(timezone.utc).isoformat(),
            "issue_id": issue.id,
            "issue_identifier": issue.identifier,
            "arena": {
                "repo": scenario.arena.repo,
                "base_sha": scenario.arena.base_sha,
                "seed": scenario.arena.seed,
            },
        },
    )
    return RunResult(
        record=record,
        stop_reason=stop_reason,
        issue=issue,
        comments_seen=len(seen),
    )


def _ingest(
    builder: RecordBuilder,
    comment: Comment,
    issue: Issue,
    assigner: GateAssigner,
    cards: list[GateCardView],
    card_index: dict[str, GateCardView],
) -> None:
    at = comment.created_at
    author = comment.author_id

    if comment.author_type == "human":
        if has_approve_line(comment.body) and comment.parent_id in card_index:
            card = card_index[comment.parent_id]
            builder.add(
                "approval",
                at,
                dedupe_key=f"approval:{comment.id}",
                actor_type="human",
                actor=author,
                gate=card.gate,
                candidate=candidate_of(comment.body) or card.candidate,
                decision="approve",
                comment_id=comment.id,
            )
        else:
            builder.add(
                "comment",
                at,
                dedupe_key=f"comment:{comment.id}",
                actor_type="human",
                actor=author,
                kind="human_comment",
                body=comment.body,
                candidate=candidate_of(comment.body),
                in_gate_thread=comment.parent_id in card_index,
            )
        return

    lane, rationale = parse_lane(comment.body)
    if lane:
        builder.add(
            "lane_classified",
            at,
            dedupe_key=f"lane:{issue.id}",
            actor_type="agent",
            actor=author,
            lane=lane.capitalize(),
            rationale=rationale,
        )

    if is_gate_card(comment.body):
        gate = assigner.assign(comment.body)
        card = GateCardView(
            comment_id=comment.id,
            gate=gate or "",
            candidate=candidate_of(comment.body),
            created_at=at,
            body=comment.body,
        )
        cards.append(card)
        card_index[comment.id] = card
        builder.add(
            "comment",
            at,
            dedupe_key=f"card:{comment.id}",
            actor_type="agent",
            actor=author,
            kind="gate_card",
            gate=gate,
            candidate=card.candidate,
            lines=visible_lines(comment.body),
            fields=card_fields(comment.body),
            body=comment.body,
        )
    else:
        builder.add(
            "comment",
            at,
            dedupe_key=f"comment:{comment.id}",
            actor_type="agent",
            actor=author,
            kind="agent_comment",
            body=comment.body,
        )


def _record_human_decision(
    builder: RecordBuilder,
    comment: Comment,
    decision: Any,
    card_index: dict[str, GateCardView],
) -> None:
    at = comment.created_at
    if decision.decision == "approve" and comment.parent_id in card_index:
        builder.add(
            "approval",
            at,
            dedupe_key=f"approval:{comment.id}",
            actor_type="human",
            actor=comment.author_id,
            gate=card_index[comment.parent_id].gate,
            candidate=decision.candidate,
            decision="approve",
            comment_id=comment.id,
        )
        return
    builder.add(
        "comment",
        at,
        dedupe_key=f"comment:{comment.id}",
        actor_type="human",
        actor=comment.author_id,
        kind=decision.decision,
        body=comment.body,
        gate=decision.gate,
        candidate=decision.candidate,
        in_gate_thread=comment.parent_id in card_index,
    )


def _as_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
