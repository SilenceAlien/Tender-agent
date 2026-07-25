"""Integration tests for TemplateMatcher node."""

import pytest

from core.nodes.template_matcher import (
    MIN_SIMILARITY,
    _build_query_text,
    template_matcher,
)
from core.state import NodeStatus, factory_state


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def state_with_requirements():
    return factory_state(requirements={
        "scoring": [
            {"item_name": "技术方案", "score": 40, "criteria": "方案的完整性和创新性"},
            {"item_name": "公司资质", "score": 20, "criteria": "ISO9001等认证"},
        ],
        "qualifications": ["独立法人资格", "注册资本>=500万"],
        "tech_specs": [
            {"spec_name": "人员配置", "requirement": ">=10名技术人员", "mandatory": True},
        ],
        "format_rules": {},
    })


@pytest.fixture
def mock_search_good():
    """Returns top-3 templates with high similarity."""
    def fn(query: str, k: int) -> list[tuple[str, float]]:
        return [
            ("tpl_service_v2", 0.001),   # ~cosine_sim = 0.999
            ("tpl_service_v1", 0.1),     # ~cosine_sim = 0.995
            ("tpl_goods", 0.5),          # ~cosine_sim = 0.875
            ("tpl_integration", 1.0),    # ~cosine_sim = 0.5
        ]
    return fn


@pytest.fixture
def mock_search_below_threshold():
    """Returns results all below similarity threshold."""
    def fn(query: str, k: int) -> list[tuple[str, float]]:
        return [
            ("tpl_a", 2.0),   # L2=2.0 → cos=0.0 (far)
            ("tpl_b", 1.5),   # L2=1.5 → cos≈0.0
        ]
    return fn


@pytest.fixture
def mock_search_empty():
    """Returns no results."""
    def fn(query: str, k: int) -> list[tuple[str, float]]:
        return []
    return fn


# ── Query Building Tests ───────────────────────────────────────────────


class TestBuildQueryText:
    def test_full_requirements(self, state_with_requirements):
        text = _build_query_text(state_with_requirements["requirements"])
        assert "技术方案" in text
        assert "ISO9001" in text
        assert "独立法人" in text
        assert "人员配置" in text

    def test_empty_requirements(self):
        text = _build_query_text({})
        assert text == ""

    def test_only_scoring(self):
        reqs = {"scoring": [{"item_name": "价格", "criteria": "最低价"}]}
        text = _build_query_text(reqs)
        assert "价格" in text
        assert "最低价" in text


# ── Template Matcher Node Tests ──────────────────────────────────────


class TestTemplateMatcherNode:
    def test_no_search_fn(self, state_with_requirements):
        result = template_matcher(state_with_requirements)
        assert result["matched_templates"] == []
        assert result["node_status"]["TemplateMatcher"] == NodeStatus.COMPLETED.value

    def test_empty_documents(self):
        state = factory_state()
        result = template_matcher(state)
        assert result["matched_templates"] == []
        assert result["node_status"]["TemplateMatcher"] == NodeStatus.COMPLETED.value

    def test_good_match(self, state_with_requirements, mock_search_good):
        result = template_matcher(state_with_requirements, search_fn=mock_search_good)
        matched = result["matched_templates"]
        assert len(matched) >= 1
        assert matched[0].startswith("tpl_")

    def test_top_3_limit(self, state_with_requirements, mock_search_good):
        result = template_matcher(state_with_requirements, search_fn=mock_search_good)
        assert len(result["matched_templates"]) <= 3

    def test_below_threshold_returns_empty(self, state_with_requirements, mock_search_below_threshold):
        result = template_matcher(state_with_requirements, search_fn=mock_search_below_threshold)
        assert result["matched_templates"] == []

    def test_empty_results(self, state_with_requirements, mock_search_empty):
        result = template_matcher(state_with_requirements, search_fn=mock_search_empty)
        assert result["matched_templates"] == []
        assert result["node_status"]["TemplateMatcher"] == NodeStatus.COMPLETED.value

    def test_similarity_threshold_boundary(self, state_with_requirements):
        """Cosine distance 0.5 → cosine_sim = 0.5, which > 0.3 threshold."""
        def search_fn(query: str, k: int):
            return [("tpl_test", 0.5)]  # distance 0.5 → similarity 0.5 > 0.3
        result = template_matcher(state_with_requirements, search_fn=search_fn)
        assert len(result["matched_templates"]) == 1

    def test_exact_match_high_similarity(self, state_with_requirements, mock_search_good):
        result = template_matcher(state_with_requirements, search_fn=mock_search_good)
        # Best match should be first
        assert result["matched_templates"][0] == "tpl_service_v2"

    def test_preserves_other_state(self, state_with_requirements, mock_search_good):
        result = template_matcher(state_with_requirements, search_fn=mock_search_good)
        # node_status should still have other nodes
        assert "DocumentParser" in result["node_status"]
