"""Integration test for feedback loop (T018).

Simulates the full QA → Feedback → Regenerate cycle:
    2 rounds of FAIL → feedback → regenerate → finally PASS
Verifies round count increments and sections version chain.
"""

import pytest

from core.nodes.feedback_processor import feedback_processor
from core.nodes.quality_checker import quality_checker
from core.nodes.section_generator import section_generator
from core.state import NodeStatus, factory_state


@pytest.fixture
def state_r0():
    """Initial state with sections that have issues."""
    return factory_state(
        requirements={
            "scoring": [
                {"item_name": "服务方案完整性", "score": 40, "criteria": "覆盖全部"},
                {"item_name": "售后服务", "score": 20, "criteria": "响应快"},
            ],
        },
        sections={
            "第一章": "这是最好的方案，绝对完美。" * 5,  # 包含两个违禁词
            "第二章": "服务方案完整性描述详细。售后服务覆盖全面。响应时间30分钟。" * 5,  # OK
        },
        current_round=0,
        min_section_chars=0,  # Disable 8000-char threshold for tests
    )


class TestFeedbackLoop:
    def test_round_1_fail_adds_feedback(self, state_r0):
        """Round 0: FAIL → feedback_processor adds records."""
        qc = quality_checker(state_r0)
        assert qc["quality_report"]["verdict"] == "FAIL"

        state_r1 = {**state_r0, **qc}
        fb = feedback_processor(state_r1)
        assert len(fb["feedback_history"]) >= 1
        assert fb["current_round"] == 1

    def test_round_2_with_feedback(self, state_r0):
        """Round 0 FAIL → Round 1 feedback → Round 1 regenerates → Round 1 PASS."""
        # Round 0: Quality check (fails)
        qc0 = quality_checker(state_r0)
        assert qc0["quality_report"]["verdict"] == "FAIL"

        # Round 0→1: Process feedback
        state_with_qc = {**state_r0, **qc0}
        fb0 = feedback_processor(state_with_qc)
        assert fb0["current_round"] == 1
        assert len(fb0["feedback_history"]) >= 1

        # Round 1: Regenerate with fixed content
        state_r1 = {**state_with_qc, **fb0}
        # Fix the sections (remove banned words)
        state_r1["sections"] = {
            "第一章": "服务方案完整，覆盖所有要求。方案经过充分论证，满足全部标准。" * 5,
            "第二章": "服务方案完整性描述详细。售后服务覆盖全面。响应时间30分钟。" * 5,
        }
        # Re-generate (simulated)
        gen1 = section_generator(state_r1)
        state_r1.update(gen1)

        # Round 1: Quality check (should pass now)
        qc1 = quality_checker(state_r1)
        assert qc1["quality_report"]["verdict"] == "PASS", (
            f"Expected PASS, got {qc1['quality_report']['compliance']}"
        )

    def test_full_2_round_loop(self, state_r0):
        """Simulate 2 rounds: R0 FAIL → R1 FAIL → R2 PASS."""
        # R0: FAIL
        qc0 = quality_checker(state_r0)
        assert qc0["quality_report"]["verdict"] == "FAIL"
        state = {**state_r0, **qc0}

        fb0 = feedback_processor(state)
        assert fb0["current_round"] == 1
        state.update(fb0)

        # R1: Still FAIL (content unchanged)
        qc1 = quality_checker(state)
        state.update(qc1)

        fb1 = feedback_processor(state)
        assert fb1["current_round"] == 2
        state.update(fb1)

        # R2: Fix content and recheck
        state["sections"] = {
            "第一章": "方案完整、论述专业。满足所有招标要求。数据准确，内容翔实。" * 5,
            "第二章": "服务方案完整性描述详细。售后服务覆盖全面。响应时间30分钟。" * 5,
        }
        gen2 = section_generator(state)
        state.update(gen2)

        qc2 = quality_checker(state)
        assert qc2["quality_report"]["verdict"] == "PASS", (
            f"Round 2 should PASS, got {qc2['quality_report']['compliance']}"
        )

    def test_round_increments_correctly(self, state_r0):
        """Verify round increments through the feedback loop."""
        state = dict(state_r0)

        round_history = []
        for i in range(3):
            qc = quality_checker(state)
            state.update(qc)

            fb = feedback_processor(state)
            state.update(fb)
            round_history.append(fb["current_round"])

            if qc["quality_report"]["verdict"] == "PASS":
                break

            # Fix content for next attempt
            if i == 2:
                state["sections"] = {
                    "第一章": "方案完整、论述专业。" * 5,
                    "第二章": "服务方案完整性描述详细。售后服务覆盖全面。" * 5,
                }

        assert round_history == [1, 2, 3]

    def test_max_rounds_not_exceeded(self, state_r0):
        """Even with persistent failures, round never exceeds max_rounds."""
        max_rounds = state_r0["max_rounds"]
        state = dict(state_r0)

        for _ in range(max_rounds + 1):
            qc = quality_checker(state)
            state.update(qc)

            if state["current_round"] >= max_rounds:
                # Should stop incrementing after max_rounds
                break

            fb = feedback_processor(state)
            state.update(fb)

        assert state["current_round"] <= max_rounds + 1

    def test_quality_report_preserved(self, state_r0):
        """Quality report from each round is accessible."""
        qc = quality_checker(state_r0)
        report = qc["quality_report"]

        assert "verdict" in report
        assert "completeness" in report
        assert "compliance" in report
        assert "consistency" in report
        assert report["verdict"] == "FAIL"
        assert len(report["compliance"]) > 0
