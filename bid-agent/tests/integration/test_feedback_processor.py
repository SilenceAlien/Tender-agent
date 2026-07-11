"""Integration tests for FeedbackProcessor node."""

import pytest

from core.nodes.feedback_processor import (
    _classify_issue,
    _extract_section_name,
    feedback_processor,
)
from core.state import NodeStatus, factory_state


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def state_with_failures():
    return factory_state(
        sections={
            "第一章 服务方案": "内容A" * 30,
            "第二章 技术方案": "内容B" * 30,
        },
        quality_report={
            "verdict": "FAIL",
            "completeness": {"服务方案完整性": True, "售后服务": False},
            "compliance": [
                "「第一章 服务方案」包含违禁词「最佳」",
                "「第二章 技术方案」内容过短（60字）",
            ],
            "consistency": ["跨章节公司名称引用不一致"],
            "total_items": 5,
            "passed_items": 2,
        },
        current_round=0,
    )


# ── Classification Tests ───────────────────────────────────────────────


class TestClassifyIssue:
    def test_style_adjust(self):
        fb_type, scope = _classify_issue("字体不符合要求")
        assert fb_type == "style_adjust"

    def test_structure(self):
        fb_type, scope = _classify_issue("标题层级需要优化")
        assert fb_type == "structure"

    def test_score_align(self):
        fb_type, scope = _classify_issue("评分标准不匹配")
        assert fb_type == "score_align"

    def test_content_fix_default(self):
        fb_type, scope = _classify_issue("某某描述不够详细")
        assert fb_type == "content_fix"
        assert scope == "local"


# ── Section Extraction Tests ───────────────────────────────────────────


class TestExtractSectionName:
    def test_extract_from_brackets(self):
        sections = {"第一章 服务方案": "", "第二章 技术方案": ""}
        name = _extract_section_name("「第一章 服务方案」内容有问题", sections)
        assert name == "第一章 服务方案"

    def test_extract_not_found_returns_first(self):
        sections = {"第一章": "", "第二章": ""}
        name = _extract_section_name("某段内容有问题", sections)
        assert name == "第一章"


# ── Node Tests ─────────────────────────────────────────────────────────


class TestFeedbackProcessorNode:
    def test_no_issues_returns_empty(self):
        state = factory_state(
            quality_report={"compliance": [], "consistency": [], "completeness": {}},
            current_round=0,
        )
        result = feedback_processor(state)
        assert result["feedback_history"] == []
        assert result["current_round"] == 0

    def test_creates_feedback_records(self, state_with_failures):
        result = feedback_processor(state_with_failures)
        records = result["feedback_history"]
        assert len(records) >= 3  # compliance(2) + consistency(1) + completeness(1)

    def test_increments_round(self, state_with_failures):
        result = feedback_processor(state_with_failures)
        assert result["current_round"] == 1

    def test_feedback_has_required_fields(self, state_with_failures):
        result = feedback_processor(state_with_failures)
        record = result["feedback_history"][0]
        assert "round" in record
        assert "feedback_text" in record
        assert "feedback_type" in record
        assert "scope" in record
        assert "target_section" in record
        assert "timestamp" in record

    def test_target_section_extracted(self, state_with_failures):
        result = feedback_processor(state_with_failures)
        local_records = [r for r in result["feedback_history"] if r["scope"] == "local"]
        # At least one local record should have target_section
        assert any(r["target_section"] != "" for r in local_records) or len(local_records) == 0

    def test_node_status_updated(self, state_with_failures):
        result = feedback_processor(state_with_failures)
        assert result["node_status"]["FeedbackProcessor"] == NodeStatus.COMPLETED.value

    def test_preserves_existing_history(self, state_with_failures):
        existing_fb = [{"round": 1, "old": "feedback"}]
        state = factory_state(
            sections=state_with_failures["sections"],
            quality_report=state_with_failures["quality_report"],
            current_round=1,
            feedback_history=existing_fb,
        )
        result = feedback_processor(state)
        assert len(result["feedback_history"]) > 1
        assert result["feedback_history"][0] == existing_fb[0]

    def test_multiple_rounds_accumulate(self, state_with_failures):
        r1 = feedback_processor(state_with_failures)
        assert len(r1["feedback_history"]) >= 3

        # Second round
        state2 = factory_state(
            sections=state_with_failures["sections"],
            quality_report=state_with_failures["quality_report"],
            current_round=1,
            feedback_history=r1["feedback_history"],
        )
        r2 = feedback_processor(state2)
        assert len(r2["feedback_history"]) > len(r1["feedback_history"])
        assert r2["current_round"] == 2
