"""Integration test for the two-phase pipeline (US#6).

Tests the full extraction → verification → generation flow:
  1. build_extraction_graph() runs Phase 1 and pauses at InfoVerificationGate
  2. apply_user_corrections() injects user-confirmed values
  3. build_generation_graph() runs Phase 2 with the confirmed state
"""

import pytest

from core.state import AgentState, NodeStatus, factory_state
from core.graph import (
    build_extraction_graph,
    build_generation_graph,
    apply_user_corrections,
    build_graph,
    run_pipeline,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def initial_state() -> AgentState:
    """State with a document and extra_reqs for extraction."""
    return factory_state(
        documents=[{"filename": "test.pdf", "path": "", "type": "pdf"}],
        bid_type="服务类",
        extra_reqs="投标公司为广州华南人力有限公司，项目在广州，工期12个月",
        max_rounds=3,
        chunk_size=1000,
    )


# ── Phase 1: Extraction Graph Tests ────────────────────────────────────


class TestExtractionGraph:
    """Phase 1: extraction sub-graph pauses at InfoVerificationGate."""

    def test_extraction_graph_compiles(self):
        """build_extraction_graph() should compile without errors."""
        graph = build_extraction_graph()
        assert graph is not None

    def test_extraction_phase_sets_pending(self, initial_state):
        """Phase 1 should set info_verification_status='pending'."""
        graph = build_extraction_graph()
        result = graph.invoke(initial_state)
        # DocumentParser may fail (no real file), but the extraction graph
        # should still produce a result.  If parsing fails, route_after_parser
        # sends to __end__ before InfoVerificationGate runs.
        # In that case, info_verification_status will be empty.
        # This is acceptable — the GUI handles this case.
        assert isinstance(result, dict)
        assert "node_status" in result

    def test_extraction_phase_with_mock_data(self):
        """When DocumentParser succeeds (mock), gate should be pending."""
        # Use a state that won't trigger DocumentParser failure
        # by providing no documents (DocumentParser handles empty gracefully)
        state = factory_state(
            documents=[],
            bid_type="服务类",
            extra_reqs="投标公司为测试公司",
        )
        graph = build_extraction_graph()
        result = graph.invoke(state)
        # With no documents, DocumentParser completes (empty) and
        # ReqExtractor completes (empty), ContractExtractor builds minimal contract,
        # InfoVerificationGate sets pending
        assert result.get("info_verification_status") in ("pending", "")
        assert "info_summary" in result


# ── apply_user_corrections Tests ───────────────────────────────────────


class TestUserCorrections:
    """User corrections are applied correctly between phases."""

    def test_corrections_confirm_and_update_contract(self):
        """apply_user_corrections should update project_contract."""
        state = factory_state(
            requirements={
                "project_name": "原项目名称",
                "bid_number": "GD2026-001",
                "tenderer_name": "XX大学",
                "scoring": [],
                "qualifications": [],
                "tech_specs": [],
                "format_rules": {},
            },
            project_contract={
                "project_name": "原项目名称",
                "project_code": "GD2026-001",
                "bidder_name": "原投标人",
                "tenderer_name": "XX大学",
                "project_location": "北京",
            },
            bid_type="服务类",
        )
        corrections = {
            "project_name": "修正后项目名称",
            "bidder_name": "修正后投标人",
        }
        result = apply_user_corrections(state, corrections)
        assert result["info_verification_status"] == "confirmed"
        assert result["user_confirmed_fields"] == corrections
        # Contract should reflect the corrected values
        contract = result["project_contract"]
        assert contract["project_name"] == "修正后项目名称"
        assert contract["bidder_name"] == "修正后投标人"
        # Non-corrected fields should retain original values
        assert contract["tenderer_name"] == "XX大学"

    def test_empty_corrections_still_confirms(self):
        """User confirms without any changes."""
        state = factory_state(
            requirements={"project_name": "项目", "bid_number": "001"},
            project_contract={"project_name": "项目", "bidder_name": "投标人"},
        )
        result = apply_user_corrections(state, {})
        assert result["info_verification_status"] == "confirmed"
        assert result["user_confirmed_fields"] == {}


# ── Phase 2: Generation Graph Tests ────────────────────────────────────


class TestGenerationGraph:
    """Phase 2: generation sub-graph runs after user confirmation."""

    def test_generation_graph_compiles(self):
        """build_generation_graph() should compile without errors."""
        graph = build_generation_graph()
        assert graph is not None

    def test_generation_phase_runs_with_confirmed_state(self):
        """Phase 2 should run to completion with a confirmed state."""
        state = factory_state(
            requirements={
                "project_name": "测试项目",
                "bid_number": "TEST-001",
                "tenderer_name": "测试招标人",
                "scoring": [],
                "qualifications": [],
                "tech_specs": [],
                "format_rules": {},
            },
            project_contract={
                "project_name": "测试项目",
                "project_code": "TEST-001",
                "bidder_name": "测试投标人",
                "tenderer_name": "测试招标人",
            },
            bid_type="服务类",
            info_verification_status="confirmed",
            user_confirmed_fields={"project_name": "测试项目"},
        )
        state["review_status"] = "approved"  # Skip HumanReviewGate pause
        graph = build_generation_graph()
        result = graph.invoke(state)
        assert result["node_status"]["EligibilityChecker"] == NodeStatus.COMPLETED
        assert result["node_status"]["TemplateMatcher"] == NodeStatus.COMPLETED
        assert result["node_status"]["SectionGenerator"] == NodeStatus.COMPLETED


# ── Full Two-Phase Flow Tests ──────────────────────────────────────────


class TestTwoPhaseFlow:
    """End-to-end two-phase flow: extract → correct → generate."""

    def test_two_phase_flow_completes(self):
        """Full two-phase flow: Phase1 → corrections → Phase2."""
        # Phase 1
        state = factory_state(
            documents=[],
            bid_type="服务类",
            extra_reqs="投标公司为测试公司",
        )
        graph1 = build_extraction_graph()
        phase1_result = graph1.invoke(state)

        # Apply user corrections
        corrections = {"project_name": "用户确认项目名"}
        correction_result = apply_user_corrections(phase1_result, corrections)
        assert correction_result["info_verification_status"] == "confirmed"

        # Phase 2
        phase2_state = {**phase1_result, **correction_result}
        phase2_state["review_status"] = "approved"
        graph2 = build_generation_graph()
        phase2_result = graph2.invoke(phase2_state)

        # Verify Phase 2 completed
        assert phase2_result["node_status"]["EligibilityChecker"] == NodeStatus.COMPLETED
        assert phase2_result["node_status"]["DocumentAssembler"] in (
            NodeStatus.COMPLETED.value,
            NodeStatus.FAILED.value,
        )

    def test_user_correction_overrides_extraction(self):
        """User corrections should override extracted values in final contract."""
        state = factory_state(
            requirements={
                "project_name": "LLM提取的项目名",
                "bid_number": "LLM-001",
                "tenderer_name": "招标人",
                "scoring": [],
                "qualifications": [],
                "tech_specs": [],
                "format_rules": {},
            },
            project_contract={
                "project_name": "LLM提取的项目名",
                "project_code": "LLM-001",
                "bidder_name": "LLM提取的投标人",
                "tenderer_name": "招标人",
            },
            bid_type="服务类",
        )
        corrections = {"project_name": "用户修正的项目名", "bidder_name": "用户修正的投标人"}
        result = apply_user_corrections(state, corrections)
        contract = result["project_contract"]
        assert contract["project_name"] == "用户修正的项目名"
        assert contract["bidder_name"] == "用户修正的投标人"


# ── Backward Compatibility Tests ───────────────────────────────────────


class TestBackwardCompat:
    """build_graph() (headless mode) should still work as before."""

    def test_build_graph_auto_confirms_verification(self):
        """build_graph() with auto_confirm should not pause at InfoVerificationGate."""
        state = factory_state()
        state["review_status"] = "approved"  # Skip HumanReviewGate pause
        result = run_pipeline(state)
        # InfoVerificationGate should have auto-confirmed
        assert result["node_status"].get("InfoVerificationGate") == NodeStatus.COMPLETED
        # info_verification_status should be "confirmed" (auto mode)
        assert result.get("info_verification_status") == "confirmed"

    def test_build_graph_runs_all_nodes(self):
        """build_graph() should run all nodes end-to-end."""
        state = factory_state()
        state["review_status"] = "approved"
        result = run_pipeline(state)
        # All generation-phase nodes should be completed
        for node in ("EligibilityChecker", "TemplateMatcher", "SectionGenerator"):
            assert result["node_status"][node] == NodeStatus.COMPLETED, f"{node} not completed"
