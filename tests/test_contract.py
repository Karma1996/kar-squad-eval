"""Comment -> event contract."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner.contract import (  # noqa: E402
    GateAssigner,
    candidate_of,
    card_fields,
    has_approve_line,
    is_gate_card,
    marked_gate,
    parse_lane,
    visible_lines,
)
from runner.platform import Comment  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from runner.sim_human import SimHuman  # noqa: E402
from runner.scenario import HumanStep  # noqa: E402
from runner.contract import GateCardView  # noqa: E402

CARD = """行动：确认排行榜需求
谁决定：KAR-SIM-Human（Decision Owner）
绑定版本：candidate/KAR-201-h1aa11
检查：文档齐全 / FR 可追溯 / 原型可打开
理由：R2，需要完整门禁
附条件：无
复制：/approve
不做的后果：开发不开始
<!-- kar-gate: h1-requirements -->"""


class ContractTests(unittest.TestCase):
    def test_parse_lane_with_rationale(self):
        lane, rationale = parse_lane("LANE: Standard — R2，8 个文件，测试：pytest")
        self.assertEqual(lane, "standard")
        self.assertIn("R2", rationale)

    def test_card_fields_and_line_count(self):
        self.assertEqual(visible_lines(CARD), 8)
        fields = card_fields(CARD)
        self.assertEqual(fields["checks"], ["文档齐全", "FR 可追溯", "原型可打开"])
        self.assertEqual(fields["version"], "candidate/KAR-201-h1aa11")
        self.assertEqual(fields["copy_line"], "/approve")

    def test_card_detection(self):
        self.assertTrue(is_gate_card(CARD))
        self.assertFalse(is_gate_card("LANE: Quick — no card here"))
        # a human approving a candidate without the eight elements is not a card
        self.assertFalse(is_gate_card("/approve candidate/KAR-1-abc"))

    def test_marker_is_invisible_to_line_count(self):
        with_marker = CARD + "\n<!-- kar-gate: x -->"
        self.assertEqual(visible_lines(with_marker), 8)
        self.assertEqual(marked_gate(CARD), "h1-requirements")

    def test_approve_needs_a_separator(self):
        self.assertTrue(has_approve_line("复制：/approve"))
        self.assertTrue(has_approve_line("/approve\n"))
        self.assertFalse(has_approve_line("not/approved"))

    def test_candidate_extraction(self):
        self.assertEqual(
            candidate_of("批准 candidate/KAR-1-abc123。"), "candidate/KAR-1-abc123"
        )
        self.assertIsNone(candidate_of("no version"))

    def test_gate_assigner_marker_then_keyword_then_order(self):
        assigner = GateAssigner(("h1-requirements", "h2-design", "h3-accept-merge"))
        self.assertEqual(assigner.assign(CARD), "h1-requirements")
        self.assertEqual(assigner.assign("方案确认：看设计"), "h2-design")
        self.assertEqual(assigner.assign("一张没有关键词的卡"), "h3-accept-merge")
        self.assertIsNone(assigner.assign("多余的卡"))


class SimHumanTests(unittest.TestCase):
    NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)

    def card(self, created=NOW):
        return GateCardView(
            comment_id="c1",
            gate="h1-requirements",
            candidate="candidate/KAR-201-h1aa11",
            created_at=created,
            body=CARD,
        )

    def test_waits_until_policy_delay(self):
        human = SimHuman((HumanStep(gate="h1-requirements", after_minutes=45),))
        self.assertEqual(human.due([self.card()], self.NOW.replace(minute=30)), [])
        decisions = human.due([self.card()], self.NOW.replace(minute=45))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].candidate, "candidate/KAR-201-h1aa11")
        self.assertEqual(decisions[0].parent_id, "c1")
        # fires once
        self.assertEqual(human.due([self.card()], self.NOW.replace(hour=12)), [])

    def test_stale_binding(self):
        human = SimHuman(
            (
                HumanStep(
                    gate="h1-requirements",
                    action="approve_stale",
                    stale_candidate="candidate/KAR-201-old",
                ),
            )
        )
        [decision] = human.due([self.card()], self.NOW)
        self.assertEqual(decision.candidate, "candidate/KAR-201-old")

    def test_wrong_thread_is_a_top_level_comment(self):
        human = SimHuman(
            (HumanStep(gate="h1-requirements", action="approve_wrong_thread"),)
        )
        [decision] = human.due([self.card()], self.NOW)
        self.assertIsNone(decision.parent_id)

    def test_silence_and_clarify(self):
        silent = SimHuman((HumanStep(gate="h1-requirements", action="silent"),))
        self.assertEqual(silent.due([self.card()], self.NOW), [])
        clarifier = SimHuman(
            (HumanStep(gate="h1-requirements", action="clarify", message="哪个版本？"),)
        )
        [decision] = clarifier.due([self.card()], self.NOW)
        self.assertEqual(decision.decision, "clarify")


class CommentModelTests(unittest.TestCase):
    def test_comment_is_frozen(self):
        comment = Comment(
            id="c1",
            issue_id="i1",
            author_id="a",
            author_type="human",
            body="/approve",
            created_at=SimHumanTests.NOW,
        )
        with self.assertRaises(Exception):
            comment.body = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
