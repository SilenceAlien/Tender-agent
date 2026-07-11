"""Unit tests for AgentState data classes and enums."""

import json

import pytest

from core.state import (
    AgentState,
    ALL_NODES,
    FeedbackScope,
    FeedbackType,
    NodeStatus,
    factory_state,
)


# ── Enum Tests ─────────────────────────────────────────────────────────


class TestNodeStatus:
    def test_all_values_exist(self):
        assert NodeStatus.PENDING == "pending"
        assert NodeStatus.RUNNING == "running"
        assert NodeStatus.COMPLETED == "completed"
        assert NodeStatus.FAILED == "failed"

    def test_serialization(self):
        """Enums must be JSON-serializable (they are str subclasses)."""
        assert json.dumps(NodeStatus.COMPLETED) == '"completed"'


class TestFeedbackType:
    def test_all_values_exist(self):
        assert FeedbackType.CONTENT_FIX == "content_fix"
        assert FeedbackType.STYLE_ADJUST == "style_adjust"
        assert FeedbackType.STRUCTURE_OPTIMIZE == "structure"
        assert FeedbackType.SCORE_ALIGN == "score_align"

    def test_serialization(self):
        assert json.dumps(FeedbackType.SCORE_ALIGN) == '"score_align"'


class TestFeedbackScope:
    def test_all_values_exist(self):
        assert FeedbackScope.GLOBAL == "global"
        assert FeedbackScope.LOCAL == "local"

    def test_serialization(self):
        assert json.dumps(FeedbackScope.GLOBAL) == '"global"'


# ── AgentState Tests ───────────────────────────────────────────────────


class TestAgentStateDefaults:
    """Verify all fields exist with correct defaults."""

    def test_default_state_has_all_fields(self, default_state):
        required_fields = [
            "documents",
            "requirements",
            "info_verification_status",
            "info_summary",
            "user_confirmed_fields",
            "matched_templates",
            "selected_template_id",
            "sections",
            "current_section",
            "quality_report",
            "feedback_history",
            "global_constraints",
            "compressed_history",
            "context_window_size",
            "current_round",
            "max_rounds",
            "node_status",
            "messages",
        ]
        for field in required_fields:
            assert field in default_state, f"Missing field: {field}"

    def test_documents_defaults_to_empty_list(self, default_state):
        assert default_state["documents"] == []

    def test_requirements_has_all_sub_keys(self, default_state):
        req = default_state["requirements"]
        assert "scoring" in req
        assert "qualifications" in req
        assert "tech_specs" in req
        assert "format_rules" in req

    def test_node_status_initialized_for_all_nodes(self, default_state):
        for node_name in ALL_NODES:
            assert node_name in default_state["node_status"]
            assert default_state["node_status"][node_name] == NodeStatus.PENDING

    def test_current_round_starts_at_zero(self, default_state):
        assert default_state["current_round"] == 0

    def test_max_rounds_defaults_to_three(self, default_state):
        assert default_state["max_rounds"] == 3

    def test_context_window_size_defaults_to_three(self, default_state):
        assert default_state["context_window_size"] == 3

    def test_messages_defaults_to_empty_list(self, default_state):
        assert default_state["messages"] == []

    def test_quality_report_default_structure(self, default_state):
        qr = default_state["quality_report"]
        assert qr["verdict"] == "PASS"
        assert qr["completeness"] == {}
        assert qr["compliance"] == []
        assert qr["consistency"] == []
        assert qr["total_items"] == 0
        assert qr["passed_items"] == 0


class TestAgentStateCustomization:
    """Verify override behavior."""

    def test_override_documents(self):
        docs = [{"filename": "test.pdf", "content": "hello", "type": "pdf"}]
        state = factory_state(documents=docs)
        assert state["documents"] == docs

    def test_override_requirements(self):
        req = {"scoring": [{"item_name": "价格", "score": 30}], "qualifications": [], "tech_specs": [], "format_rules": {}}
        state = factory_state(requirements=req)
        assert state["requirements"]["scoring"] == req["scoring"]

    def test_override_node_status(self):
        status = {"DocumentParser": NodeStatus.COMPLETED}
        state = factory_state(node_status=status)
        assert state["node_status"]["DocumentParser"] == NodeStatus.COMPLETED
        # Other nodes unchanged
        assert state["node_status"]["ReqExtractor"] == NodeStatus.PENDING

    def test_override_current_round(self):
        state = factory_state(current_round=2)
        assert state["current_round"] == 2

    def test_override_feedback_history(self):
        fb = [{"round": 1, "feedback_text": "改语气", "feedback_type": "style_adjust"}]
        state = factory_state(feedback_history=fb)
        assert len(state["feedback_history"]) == 1


class TestAllNodesList:
    def test_exactly_fourteen_nodes(self):
        """InfoVerificationGate added: pipeline now has 14 nodes."""
        assert len(ALL_NODES) == 14, "Pipeline must have exactly 14 nodes"

    def test_nodes_in_correct_order(self):
        expected = [
            "DocumentParser",
            "ReqExtractor",
            "ContractExtractor",
            "InfoVerificationGate",
            "EligibilityChecker",
            "TemplateMatcher",
            "SectionGenerator",
            "QualityChecker",
            "FeedbackProcessor",
            "CrossReferenceChecker",
            "ComplianceChecker",
            "ScoreSimulator",
            "HumanReviewGate",
            "DocumentAssembler",
        ]
        assert ALL_NODES == expected


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def default_state() -> AgentState:
    return factory_state()
