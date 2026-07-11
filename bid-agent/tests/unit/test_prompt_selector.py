"""Unit tests for PromptSelector."""

import pytest

from core.evolution.prompt_registry import PromptRegistry
from core.evolution.prompt_selector import PromptSelector


@pytest.fixture
def registry():
    return PromptRegistry(db_path=":memory:")


@pytest.fixture
def populated_registry(registry):
    registry.save("第三章 服务方案", "服务", "高分策略：详细描述人员配置", 0.95)
    registry.save("第三章 服务方案", "服务", "中等策略：突出公司资质", 0.80)
    registry.save("第四章 技术方案", "货物", "技术方案策略", 0.72)
    return registry


class TestPromptSelector:
    def test_disabled_returns_empty(self, registry):
        selector = PromptSelector(registry, config={"enabled": False})
        candidates = selector.select("第三章 服务方案", "服务")
        assert candidates == []

    def test_select_returns_candidates(self, populated_registry):
        selector = PromptSelector(populated_registry)
        candidates = selector.select("第三章 服务方案", "服务")
        assert len(candidates) >= 1
        assert candidates[0]["completeness_score"] == 0.95

    def test_select_respects_max_candidates(self, populated_registry):
        selector = PromptSelector(populated_registry, config={"max_candidates": 1})
        candidates = selector.select("第三章 服务方案", "服务")
        assert len(candidates) == 1

    def test_select_respects_min_score(self, populated_registry):
        selector = PromptSelector(populated_registry, config={"min_score_threshold": 0.9})
        candidates = selector.select("第四章 技术方案", "货物")
        assert candidates == []  # Score 0.72 < 0.9

    def test_select_increments_used_count(self, populated_registry):
        selector = PromptSelector(populated_registry)
        # First select
        candidates_before = selector.select("第三章 服务方案", "服务")
        # Second select
        candidates_after = populated_registry.get_best("第三章 服务方案", "服务")
        assert candidates_after[0]["used_count"] >= 1

    def test_inject_prepend(self, populated_registry):
        selector = PromptSelector(populated_registry, config={"injection_mode": "prepend"})
        base = "请撰写第三章 服务方案的内容。"
        result = selector.inject_into_prompt(base, "第三章 服务方案", "服务")
        # Injection should come before base
        assert "已学习的优化策略" in result
        assert result.index("已学习的优化策略") < result.index("请撰写第三章")

    def test_inject_append(self, populated_registry):
        selector = PromptSelector(populated_registry, config={"injection_mode": "append"})
        base = "请撰写第三章 服务方案的内容。"
        result = selector.inject_into_prompt(base, "第三章 服务方案", "服务")
        assert result.index("请撰写第三章") < result.index("已学习的优化策略")

    def test_inject_replace(self, populated_registry):
        selector = PromptSelector(populated_registry, config={"injection_mode": "replace"})
        base = "请撰写第三章 服务方案的内容。"
        result = selector.inject_into_prompt(base, "第三章 服务方案", "服务")
        assert "请撰写第三章" not in result

    def test_no_candidates_returns_base(self, registry):
        selector = PromptSelector(registry)
        base = "原始 prompt"
        result = selector.inject_into_prompt(base, "不存在的章节", "")
        assert result == base

    def test_evolution_summary(self, populated_registry):
        selector = PromptSelector(populated_registry)
        summary = selector.get_evolution_summary()
        assert summary["total_prompts"] == 3
        assert summary["enabled"] is True
        assert len(summary["top_sections"]) >= 1
