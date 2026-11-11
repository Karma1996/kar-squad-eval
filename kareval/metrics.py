"""Soft metrics and the composite score.

The score exists to compare two squad versions on the same case set, not to be
a vanity number: hard-gate violations always fail a case, and a low human touch
count is only good inside the gate rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .model import Record
from .rules import Violation, check

SOFT_PENALTIES = {
    "violation": 20.0,
    "human_touch_over_budget": 5.0,
    "wait_over_24h": 10.0,
    "rework_round": 8.0,
    "candidate_recut": 10.0,
    "escaped_s0_s2": 25.0,
    "escaped_s3": 10.0,
}

S0_TO_S2 = {"s0", "s1", "s2"}


@dataclass(frozen=True)
class Analysis:
    record: Record
    violations: tuple[Violation, ...]
    metrics: dict[str, Any]
    score: float

    @property
    def passed(self) -> bool:
        return not self.violations


def _seconds(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds())


def human_touches(record: Record) -> int:
    """Human approval, clarification and commentary events."""
    return sum(
        1
        for e in record.events
        if e.actor_type == "human"
        and (e.type == "approval" or (e.type == "comment" and e.get("kind") != "gate_card"))
    )


def wait_human(record: Record) -> dict[str, float | int | None]:
    """Per gate card: card posted -> first human decision on that gate."""
    waits: list[float] = []
    unresolved = 0
    for card in (e for e in record.events if e.type == "comment" and e.get("kind") == "gate_card"):
        gate = card.get("gate")
        decision = next(
            (
                e
                for e in record.events
                if e.type == "approval"
                and e.actor_type == "human"
                and e.t >= card.t
                and (gate is None or e.get("gate") == gate)
            ),
            None,
        )
        if decision is None:
            unresolved += 1
            continue
        waits.append(_seconds(card.t, decision.t))

    waits.sort()
    return {
        "count": len(waits),
        "unresolved": unresolved,
        "total_seconds": sum(waits),
        "max_seconds": waits[-1] if waits else None,
        "p50_seconds": waits[len(waits) // 2] if waits else None,
    }


def rework_rounds(record: Record) -> int:
    return sum(
        1
        for e in record.of_type("handoff")
        if str(e.get("result", "")).lower() in {"rejected", "returned", "bounced"}
    )


def candidate_recuts(record: Record) -> int:
    return len(record.of_type("candidate_invalidated"))


def escaped_defects(record: Record) -> dict[str, int]:
    by_severity: dict[str, int] = {}
    for event in record.of_type("post_merge_defect"):
        severity = str(event.get("severity", "s3")).lower()
        by_severity[severity] = by_severity.get(severity, 0) + 1
    return by_severity


def cycle_time(record: Record) -> float:
    merges = record.of_type("merge")
    end = merges[-1].t if merges else record.last.t
    return _seconds(record.first.t, end)


def compute_metrics(record: Record) -> dict[str, Any]:
    waits = wait_human(record)
    defects = escaped_defects(record)
    cards = sum(
        1
        for e in record.events
        if e.type == "comment" and e.get("kind") == "gate_card"
    )
    return {
        "gate_cards": cards,
        "human_touches": human_touches(record),
        "human_touch_budget": record.expected.human_touch_budget,
        "wait_human": waits,
        "rework_rounds": rework_rounds(record),
        "candidate_recuts": candidate_recuts(record),
        "escaped_defects": defects,
        "cycle_time_seconds": round(cycle_time(record), 3),
        "merged": bool(record.of_type("merge")),
    }


def score_case(
    record: Record,
    violations: tuple[Violation, ...],
    metrics: dict[str, Any],
) -> float:
    score = 100.0
    score -= SOFT_PENALTIES["violation"] * len(violations)

    excess_touches = metrics["human_touches"] - record.expected.human_touch_budget
    if excess_touches > 0:
        score -= SOFT_PENALTIES["human_touch_over_budget"] * excess_touches

    max_wait = metrics["wait_human"].get("max_seconds")
    if max_wait is not None and max_wait > 24 * 3600:
        score -= SOFT_PENALTIES["wait_over_24h"]

    score -= SOFT_PENALTIES["rework_round"] * metrics["rework_rounds"]
    score -= SOFT_PENALTIES["candidate_recut"] * metrics["candidate_recuts"]

    for severity, count in metrics["escaped_defects"].items():
        if severity in S0_TO_S2:
            score -= SOFT_PENALTIES["escaped_s0_s2"] * count
        else:
            score -= SOFT_PENALTIES["escaped_s3"] * count

    return round(max(0.0, min(100.0, score)), 1)


def analyze(record: Record) -> Analysis:
    violations = tuple(check(record))
    metrics = compute_metrics(record)
    return Analysis(
        record=record,
        violations=violations,
        metrics=metrics,
        score=score_case(record, violations, metrics),
    )
