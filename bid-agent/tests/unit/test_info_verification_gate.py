"""Unit tests for InfoVerificationGate node."""

import pytest

from core.state import AgentState, NodeStatus, factory_state
from core.nodes.info_verification_gate import (
    info_verification_gate,
    apply_user_corrections,
    _build_info_summary,
    _annotate_field_sources,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def extraction_state() -> AgentState:
    """A state that has been through DocumentParser → ReqExtractor → ContractExtractor."""
    return factory_state(
        requirements={
            "project_name": "XX大学物业服务采购项目",
            "bid_number": "GD2026-001",
            "tenderer_name": "XX大学",
            "package_number": "包1",
            "scoring": [
                {"item_name": "技术方案", "score": 40, "criteria": "方案完整性"},
                {"item_name": "商务报价", "score": 30, "criteria": "价格竞争力"},
            ],
            "qualifications": ["ISO9001认证", "物业管理资质"],
            "tech_specs": [],
            "format_rules": {},
        },
        project_contract={
            "project_name": "XX大学物业服务采购项目",
            "project_code": "GD2026-001",
            "package_number": "包1",
            "bidder_name": "广州华南人力有限公司",
            "tenderer_name": "XX大学",
            "project_location": "广州",
            "industry": "物业服务",
            "duration": "12个月",
            "warranty": "",
            "service_target": "XX大学",
            "notes": "",
            "forbidden_institutions": [],
            "forbidden_industries": [],
        },
        bid_type="服务类",
    )


# ── _annotate_field_sources Tests ──────────────────────────────────────


class TestAnnotateFieldSources:
    def test_source_from_requirements(self, extraction_state):
        sources = _annotate_field_sources(
            extraction_state["requirements"],
            extraction_state["project_contract"],
            {},
        )
        # project_name comes from requirements (招标文件)
        assert sources["project_name"] == "招标文件"
        assert sources["bid_number"] == "招标文件"
        assert sources["tenderer_name"] == "招标文件"

    def test_source_from_contract(self, extraction_state):
        sources = _annotate_field_sources(
            extraction_state["requirements"],
            extraction_state["project_contract"],
            {},
        )
        # bidder_name only in contract (LLM extracted)
        assert sources["bidder_name"] == "LLM"
        assert sources["project_location"] == "LLM"
        assert sources["duration"] == "LLM"

    def test_source_from_user_fields(self, extraction_state):
        user_fields = {"bidder_name": "广州华南人力有限公司"}
        sources = _annotate_field_sources(
            extraction_state["requirements"],
            extraction_state["project_contract"],
            user_fields,
        )
        assert sources["bidder_name"] == "用户填写"

    def test_source_missing(self):
        """Fields with no value anywhere are marked as 缺失."""
        sources = _annotate_field_sources(
            requirements={"project_name": "", "bid_number": "", "tenderer_name": ""},
            project_contract={"bidder_name": "", "project_location": "", "warranty": ""},
            user_fields={},
        )
        assert sources["project_name"] == "缺失"
        assert sources["bidder_name"] == "缺失"
        assert sources["warranty"] == "缺失"


# ── _build_info_summary Tests ──────────────────────────────────────────


class TestBuildInfoSummary:
    def test_summary_contains_all_key_fields(self, extraction_state):
        summary = _build_info_summary(extraction_state)
        for field in (
            "project_name", "bid_number", "tenderer_name", "package_number",
            "bidder_name", "project_location", "industry", "duration",
            "warranty", "service_target", "bid_type",
        ):
            assert field in summary, f"Missing field in summary: {field}"

    def test_summary_contains_counts(self, extraction_state):
        summary = _build_info_summary(extraction_state)
        assert summary["scoring_count"] == 2
        assert summary["qualification_count"] == 2

    def test_summary_contains_field_sources(self, extraction_state):
        summary = _build_info_summary(extraction_state)
        assert "field_sources" in summary
        assert isinstance(summary["field_sources"], dict)
        assert "project_name" in summary["field_sources"]


# ── info_verification_gate Tests ───────────────────────────────────────


class TestInfoVerificationGate:
    def test_pending_in_interactive_mode(self, extraction_state):
        """Default mode: gate pauses pipeline with status=pending."""
        result = info_verification_gate(extraction_state)
        assert result["info_verification_status"] == "pending"
        assert "info_summary" in result
        # RUNNING (not COMPLETED) because the gate is paused waiting for user input
        assert result["node_status"]["InfoVerificationGate"] == NodeStatus.RUNNING.value

    def test_auto_confirm_mode(self, extraction_state):
        """auto_confirm=True: gate passes immediately."""
        result = info_verification_gate(extraction_state, auto_confirm=True)
        assert result["info_verification_status"] == "confirmed"
        assert result["node_status"]["InfoVerificationGate"] == NodeStatus.COMPLETED.value

    def test_respects_existing_confirmed(self, extraction_state):
        """If state already has confirmed status, respect it (idempotent)."""
        state = {**extraction_state, "info_verification_status": "confirmed"}
        result = info_verification_gate(state)
        assert result["info_verification_status"] == "confirmed"

    def test_summary_has_correct_values(self, extraction_state):
        result = info_verification_gate(extraction_state)
        summary = result["info_summary"]
        assert summary["project_name"] == "XX大学物业服务采购项目"
        assert summary["bid_number"] == "GD2026-001"
        assert summary["bidder_name"] == "广州华南人力有限公司"

    def test_empty_state_auto_rejects(self):
        """No requirements or contract → gate still runs but summary is empty."""
        state = factory_state()
        result = info_verification_gate(state)
        assert result["info_verification_status"] == "pending"
        assert result["info_summary"]["project_name"] == ""
        assert result["info_summary"]["field_sources"]["project_name"] == "缺失"


# ── apply_user_corrections Tests ───────────────────────────────────────


class TestApplyUserCorrections:
    def test_confirms_status(self, extraction_state):
        result = apply_user_corrections(
            extraction_state,
            corrected_fields={"project_name": "XX大学物业管理服务采购项目"},
        )
        assert result["info_verification_status"] == "confirmed"

    def test_stores_user_confirmed_fields(self, extraction_state):
        corrections = {
            "project_name": "XX大学物业管理服务采购项目",
            "bidder_name": "广州华南人力有限公司",
        }
        result = apply_user_corrections(extraction_state, corrected_fields=corrections)
        assert result["user_confirmed_fields"] == corrections

    def test_empty_corrections_still_confirms(self, extraction_state):
        """User confirms without changes — just confirms with empty dict."""
        result = apply_user_corrections(extraction_state, corrected_fields={})
        assert result["info_verification_status"] == "confirmed"
        assert result["user_confirmed_fields"] == {}

    def test_partial_corrections_only_store_changed(self, extraction_state):
        """Only the fields the user changed are stored."""
        result = apply_user_corrections(
            extraction_state,
            corrected_fields={"bidder_name": "新公司名称"},
        )
        assert result["user_confirmed_fields"] == {"bidder_name": "新公司名称"}
