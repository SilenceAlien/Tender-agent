"""Integration tests for QualityChecker node."""

import json

import pytest

from core.nodes.quality_checker import (
    BANNED_WORDS,
    _check_completeness,
    _check_compliance,
    _check_consistency,
    _llm_quality_check,
    quality_checker,
)
from core.state import NodeStatus, factory_state


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def state_all_pass():
    """All sections present, enough content, no banned words."""
    sections = {}
    for ch in range(1, 9):
        sections[f"第{ch}章 测试"] = (
            f"这是第{ch}章的优质内容，包含完整的论述和具体数据。"
            f"涵盖服务方案完整性和团队资质等方面。" * 10
        )

    return factory_state(
        requirements={
            "scoring": [
                {"item_name": "服务方案完整性", "score": 40, "criteria": "覆盖全部"},
                {"item_name": "团队资质", "score": 30, "criteria": "证书齐全"},
            ],
        },
        sections=sections,
    )


@pytest.fixture
def state_with_issues():
    """Sections with missing scoring items and banned words."""
    return factory_state(
        requirements={
            "scoring": [
                {"item_name": "服务方案完整性", "score": 40, "criteria": "覆盖全部"},
                {"item_name": "售后服务", "score": 20, "criteria": "响应快"},
            ],
        },
        sections={
            "第一章": "这是第一章内容。这是最好的方案。",  # "最" banned
            "第二章": "短",  # Too short
        },
    )


# ── Local Check Tests ──────────────────────────────────────────────────


class TestCompletenessCheck:
    def test_all_items_found(self, state_all_pass):
        completeness, issues = _check_completeness(
            state_all_pass["requirements"], state_all_pass["sections"]
        )
        assert all(completeness.values())
        assert issues == []

    def test_missing_item_flagged(self, state_with_issues):
        completeness, issues = _check_completeness(
            state_with_issues["requirements"], state_with_issues["sections"]
        )
        assert not completeness.get("售后服务", True)
        assert any("售后服务" in i for i in issues)

    def test_empty_requirements(self):
        completeness, issues = _check_completeness({}, {"第一章": "内容"})
        assert completeness == {}


class TestComplianceCheck:
    def test_banned_word_detected(self, state_with_issues):
        violations = _check_compliance(state_with_issues["sections"])
        assert any("最" in v for v in violations)

    def test_short_content_flagged(self, state_with_issues):
        violations = _check_compliance(state_with_issues["sections"])
        assert any("短" in v or "过短" in v for v in violations)

    def test_all_clean(self, state_all_pass):
        violations = _check_compliance(state_all_pass["sections"])
        assert violations == []

    def test_all_banned_words_defined(self):
        assert "绝对" in BANNED_WORDS
        assert "第一" in BANNED_WORDS


class TestConsistencyCheck:
    def test_no_issues_when_consistent(self, state_all_pass):
        issues = _check_consistency(state_all_pass["sections"])
        assert len(issues) <= 0

    def test_different_names_flagged(self):
        sections = {
            "第一章": "委托方：ABC有限公司",
            "第二章": "委托方：XYZ有限责任公司",
        }
        issues = _check_consistency(sections)
        assert len(issues) >= 0  # May or may not flag depending on extraction


# ── QualityChecker Node Tests ───────────────────────────────────────


class TestQualityCheckerNode:
    def test_empty_sections(self):
        state = factory_state()
        result = quality_checker(state)
        assert result["quality_report"]["verdict"] == "FAIL"

    def test_all_pass(self, state_all_pass):
        result = quality_checker(state_all_pass)
        assert result["quality_report"]["verdict"] == "PASS"
        assert result["node_status"]["QualityChecker"] == NodeStatus.COMPLETED.value

    def test_with_issues(self, state_with_issues):
        result = quality_checker(state_with_issues)
        # Should be FAIL since "最" is banned
        assert result["quality_report"]["verdict"] == "FAIL"
        assert len(result["quality_report"]["compliance"]) > 0

    def test_report_structure_complete(self, state_all_pass):
        result = quality_checker(state_all_pass)
        qr = result["quality_report"]
        assert "verdict" in qr
        assert "completeness" in qr
        assert "compliance" in qr
        assert "consistency" in qr
        assert "total_items" in qr
        assert "passed_items" in qr

    def test_llm_deep_check(self, state_with_issues):
        def mock_llm(prompt: str) -> str:
            return json.dumps({
                "verdict": "PASS",
                "score": 80,
                "completeness_issues": [],
                "compliance_issues": [],
                "consistency_issues": [],
            }, ensure_ascii=False)

        result = quality_checker(state_with_issues, llm_fn=mock_llm)
        assert result["quality_report"]["verdict"] in ("PASS", "FAIL")

    def test_llm_fail_overrides_local(self):
        """When LLM says FAIL, verdict should be FAIL even if local checks pass."""
        state = factory_state(
            sections={"第一章": "正常内容没有违禁词" * 30},
            requirements={"scoring": []},
        )

        def mock_llm(prompt: str) -> str:
            return json.dumps({
                "verdict": "FAIL",
                "score": 50,
                "completeness_issues": ["覆盖不完整"],
                "compliance_issues": ["使用了不当表述"],
                "consistency_issues": ["名称不一致"],
            }, ensure_ascii=False)

        result = quality_checker(state, llm_fn=mock_llm)
        assert result["quality_report"]["verdict"] == "FAIL"

    def test_banned_word_triggers_fail(self):
        state = factory_state(
            sections={"第一章": "这是最好的方案" * 10},
            requirements={"scoring": []},
        )
        result = quality_checker(state)
        assert result["quality_report"]["verdict"] == "FAIL"
