"""The simulated Decision Owner.

The human is a script, not a language model: the point is to make the gate
policy reproducible. Each policy step fires once per matching card, at
``card.created_at + after_minutes``.

The simulated human never approves silently: every approval names the exact
candidate it binds to, which is what the ``stale_approval`` hard gate checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .contract import GateCardView
from .scenario import HumanStep


@dataclass(frozen=True)
class Decision:
    card_id: str
    gate: str
    action: str
    body: str
    parent_id: str | None
    candidate: str | None = None
    decision: str = "approve"


class SimHuman:
    def __init__(self, policy: tuple[HumanStep, ...], author: str = "KAR-SIM-Human") -> None:
        self.policy = policy
        self.author = author
        self._fired: set[tuple[str, int]] = set()

    def due(self, cards: list[GateCardView], now: datetime) -> list[Decision]:
        decisions: list[Decision] = []
        for card in cards:
            for index, step in enumerate(self.policy):
                if step.gate != card.gate:
                    continue
                key = (card.comment_id, index)
                if key in self._fired:
                    continue
                if now < card.created_at + timedelta(minutes=step.after_minutes):
                    continue
                self._fired.add(key)
                decision = self._decide(card, step)
                if decision is not None:
                    decisions.append(decision)
                break
        return decisions

    def _decide(self, card: GateCardView, step: HumanStep) -> Decision | None:
        if step.action == "silent":
            return None
        parent = card.comment_id
        candidate = card.candidate
        if step.action == "approve_stale":
            candidate = step.stale_candidate
        if step.action == "approve_wrong_thread":
            parent = None

        if step.action == "clarify":
            return Decision(
                card_id=card.comment_id,
                gate=card.gate,
                action=step.action,
                body=step.message or "这张卡批准的是哪个版本？请写明 candidate。",
                parent_id=parent,
                candidate=candidate,
                decision="clarify",
            )
        if step.action == "reject":
            return Decision(
                card_id=card.comment_id,
                gate=card.gate,
                action=step.action,
                body=step.message or "不批准。请先补齐证据再发卡。",
                parent_id=parent,
                candidate=candidate,
                decision="reject",
            )

        version = candidate or "未绑定版本"
        body = f"/approve\n\n绑定版本：{version}"
        if step.message and step.message != "/approve":
            body = f"{step.message}\n\n绑定版本：{version}"
        return Decision(
            card_id=card.comment_id,
            gate=card.gate,
            action=step.action,
            body=body,
            parent_id=parent,
            candidate=candidate,
            decision="approve",
        )
