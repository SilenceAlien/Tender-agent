"""Unit tests for PromptRegistry."""

import pytest

from core.evolution.prompt_registry import PromptRegistry


@pytest.fixture
def registry():
    """In-memory registry for testing."""
    return PromptRegistry(db_path=":memory:")


class TestPromptRegistry:
    def test_save_and_count(self, registry):
        assert registry.count() == 0
        rid = registry.save("第三章 服务方案", "服务", "详细描述服务流程...", 0.92)
        assert registry.count() == 1
        assert len(rid) == 36  # UUID

    def test_get_best_by_section_and_type(self, registry):
        registry.save("第三章 服务方案", "服务", "prompt_a: 详细方案", 0.85)
        registry.save("第三章 服务方案", "服务", "prompt_b: 精简方案", 0.95)
        registry.save("第三章 服务方案", "货物", "prompt_c: 货物方案", 0.80)

        # Best for "服务" type
        results = registry.get_best("第三章 服务方案", "服务", min_score=0.7, limit=2)
        assert len(results) == 2
        assert results[0]["completeness_score"] == 0.95  # Highest first
        assert results[0]["prompt_text"] == "prompt_b: 精简方案"

    def test_get_best_any_type(self, registry):
        registry.save("资质", "服务", "资质prompt", 0.88)
        registry.save("资质", "货物", "资质prompt2", 0.91)

        results = registry.get_best("资质", min_score=0.7)
        assert len(results) == 2

    def test_min_score_filter(self, registry):
        registry.save("技术方案", "服务", "低分", 0.5)
        registry.save("技术方案", "服务", "高分", 0.85)

        results = registry.get_best("技术方案", "服务", min_score=0.7)
        assert len(results) == 1
        assert results[0]["prompt_text"] == "高分"

    def test_only_pass_entries_returned(self, registry):
        registry.save("章节", "服务", "pass_prompt", 0.9, quality_verdict="PASS")
        registry.save("章节", "服务", "fail_prompt", 0.5, quality_verdict="FAIL")

        results = registry.get_best("章节", "服务", min_score=0.0)
        assert len(results) == 1
        assert results[0]["prompt_text"] == "pass_prompt"

    def test_mark_used_increments_count(self, registry):
        rid = registry.save("章节X", "服务", "test prompt", 0.9)

        # Verify initial count
        results = registry.get_best("章节X", "服务")
        assert results[0]["used_count"] == 0

        registry.mark_used(rid)
        results = registry.get_best("章节X", "服务")
        assert results[0]["used_count"] == 1

        registry.mark_used(rid)
        results = registry.get_best("章节X", "服务")
        assert results[0]["used_count"] == 2

    def test_mark_used_updates_avg_score(self, registry):
        rid = registry.save("章节Y", "服务", "prompt", 0.8)
        registry.mark_used(rid, new_score=0.9)
        registry.mark_used(rid, new_score=0.7)

        results = registry.get_best("章节Y", "服务")
        # avg = (0.9 + 0.7) / 2 = 0.8
        assert abs(results[0]["avg_score"] - 0.8) < 0.01

    def test_top_scoring_sections(self, registry):
        registry.save("A章节", "服务", "p1", 0.95)
        registry.save("A章节", "服务", "p2", 0.85)
        registry.save("B章节", "货物", "p3", 0.60)

        top = registry.get_top_scoring_sections()
        assert len(top) == 2
        assert top[0]["avg_score"] == pytest.approx(0.90)

    def test_source_context_stored(self, registry):
        ctx = {"doc_type": "PDF", "bid_type": "政府采购"}
        rid = registry.save("章节", "服务", "prompt", 0.9, source_context=ctx)
        results = registry.get_best("章节", "服务")
        assert results[0]["source_context"] is not None

    def test_template_id_stored(self, registry):
        registry.save("章节", "服务", "prompt", 0.9, template_id="tpl_v2")
        results = registry.get_best("章节", "服务")
        assert results[0]["template_id"] == "tpl_v2"
