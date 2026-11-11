"""Turn platform comments into scored events.

The squad's gate cards are written for humans, so the recorder cannot rely on
internal gate names (``h2-design``) appearing in the body. It resolves a gate
in this order:

1. an explicit invisible marker ``<!-- kar-gate: h2-design -->``;
2. a keyword from the card's human-facing title (需求确认 / 方案确认 / 验收与合并);
3. falling back to the next expected gate of the case, in order.

Markers are the reliable path and are the recommended contract for the squad
skill (see README "契约"). Until the skill emits them, keywords + order keep
the recorder deterministic for the golden set.

Line counts used by the ``gate_card_format`` hard gate exclude HTML-comment-only
lines, because those are invisible to the human deciding from the card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

LANE_RE = re.compile(r"\bLANE:\s*(Quick|Standard|Program)\b", re.IGNORECASE)
GATE_MARKER_RE = re.compile(r"<!--\s*kar-gate:\s*([a-z0-9][a-z0-9_-]*)\s*-->", re.IGNORECASE)
CANDIDATE_RE = re.compile(r"candidate/[A-Za-z0-9][A-Za-z0-9._/-]*")
APPROVE_RE = re.compile(r"(^|[^A-Za-z0-9_])/approve\b", re.IGNORECASE)
MARKER_LINE_RE = re.compile(r"^\s*<!--.*-->\s*$")

GATE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("h1-requirements", ("需求确认", "需求基线", "requirements sign-off")),
    ("h2-design", ("方案确认", "方案基线", "design sign-off")),
    ("h3-accept-merge", ("验收与合并", "验收＋合并", "验收+合并", "accept and merge")),
    ("wave-1", ("wave 1", "wave-1", "第一批")),
)

#: label -> canonical field of the eight-element gate card.
LABELS: dict[str, tuple[str, ...]] = {
    "action": ("行动", "动作", "action"),
    "decision_owner": ("谁决定", "决策人", "decision owner"),
    "version": ("绑定版本", "版本", "version"),
    "checks": ("检查", "checks"),
    "rationale": ("理由", "rationale"),
    "conditions": ("附条件", "条件", "conditions"),
    "copy_line": ("复制", "一行", "copy", "copy line"),
    "consequence": ("不做的后果", "后果", "consequence"),
}

_LABEL_LOOKUP: dict[str, str] = {}
for _field, _names in LABELS.items():
    for _name in _names:
        _LABEL_LOOKUP[_name.lower()] = _field


def parse_lane(body: str) -> tuple[str | None, str]:
    """Return ``(lane, rationale)`` from a ``LANE: ...`` line."""
    match = LANE_RE.search(body)
    if not match:
        return None, ""
    lane = match.group(1).lower()
    line = next(
        (line.strip() for line in body.splitlines() if LANE_RE.search(line)),
        "",
    )
    rationale = LANE_RE.sub("", line, count=1).strip(" \t—-–:：")
    return lane, rationale


def candidate_of(body: str) -> str | None:
    match = CANDIDATE_RE.search(body)
    return match.group(0).rstrip(".,;)") if match else None


def has_approve_line(body: str) -> bool:
    return bool(APPROVE_RE.search(body))


def visible_lines(body: str) -> int:
    return sum(
        1
        for line in body.splitlines()
        if line.strip() and not MARKER_LINE_RE.match(line)
    )


def marked_gate(body: str) -> str | None:
    match = GATE_MARKER_RE.search(body)
    return match.group(1).lower() if match else None


def keyword_gate(body: str) -> str | None:
    lowered = body.lower()
    for gate, keywords in GATE_KEYWORDS:
        for keyword in keywords:
            if keyword in lowered:
                return gate
    return None


def card_fields(body: str) -> dict[str, object]:
    """Best-effort parse of the eight required card elements."""
    fields: dict[str, object] = {}
    checks: list[str] = []
    lines = [line.strip().lstrip("->*• ").strip() for line in body.splitlines()]
    lines = [line for line in lines if line and not MARKER_LINE_RE.match(line)]
    if lines:
        fields["action"] = lines[0]

    for line in lines:
        for name, canonical in _LABEL_LOOKUP.items():
            pattern = re.compile(rf"^\**{re.escape(name)}\**\s*[:：]\s*(.+)$", re.IGNORECASE)
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1).strip()
            if canonical == "checks":
                checks = _split_checks(value)
                if checks:
                    fields["checks"] = checks
            elif value:
                fields[canonical] = value
            break
    return fields


def _split_checks(value: str) -> list[str]:
    parts = re.split(r"\s*(?:/|、|\||\d\))\s*", value)
    return [part.strip(" ;；") for part in parts if part and part.strip(" ;；")][:3]


def is_gate_card(body: str) -> bool:
    """A gate card is an agent comment a human can approve as written."""
    if not has_approve_line(body) or not candidate_of(body):
        return False
    fields = card_fields(body)
    return len(fields) >= 4 and "checks" in fields and len(fields.get("checks", [])) == 3


@dataclass(frozen=True)
class GateCardView:
    comment_id: str
    gate: str
    candidate: str | None
    created_at: datetime
    body: str


class GateAssigner:
    """Resolve a gate id for each card, keeping fallback order stable."""

    def __init__(self, expected_gates: tuple[str, ...]) -> None:
        self._remaining = list(expected_gates)

    def assign(self, body: str) -> str | None:
        gate = marked_gate(body) or keyword_gate(body)
        if gate and gate in self._remaining:
            self._remaining.remove(gate)
            return gate
        if gate:
            return gate
        if not self._remaining:
            return None
        return self._remaining.pop(0)
