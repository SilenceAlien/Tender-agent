"""PromptSelector — retrieves and injects the best prompt for a given section.

Integrates with PromptRegistry to find the best prompt variant and
prepares it for injection into SectionGenerator's LLM call.

Config (config/evolution.yaml):
    evolution:
      prompt_evolution:
        enabled: true
        min_score_threshold: 0.7
        max_candidates: 3
        injection_mode: "prepend"  # "prepend" | "replace" | "append"

─── L9 接入状态（code-audit / cluster_C7_v2.md）─────────────────────────
本模块当前【未被任何 pipeline 节点调用】——属于「已实现、未接线」的死代码
（Grep 全仓仅 __init__.py 导出、tests 实例化，无节点 import/调用本类）。
它读取 PromptRegistry 的高分 prompt 变体，但 Registry 从未被写入（无 .save 调用），
故 inject_into_prompt() 当前恒返回 base_prompt（空候选 → 降级）。

与 ConsistencyLessonStore 注入路径的关系：并行、非重叠。ConsistencyLessonStore
注入「结构化指令规则」(H11 已修并接通)；本 Selector 注入「整段最优 prompt 变体」，
机制不同、具备独特价值，但本版本未接线。

若未来需接入（建议【不要在此版本接线】，避免与 Store 注入冲突/引入风险）：
  · 读取点：在 core/nodes/section_generator.py 的 _get_section_prompt 末尾、
    ConsistencyLessonStore 注入之后调用 inject_into_prompt(...)。
  · 必须确保 injection_mode != "replace"：replace 会整体覆盖已接好的
    契约/经验/RAG/反编造 结构化 prompt，风险高；prepend/append 更安全。
  · 同时必须给 PromptRegistry 接 .save() 写入路径（见 prompt_registry.py 顶部
    L9 说明），否则 Selector 永远空候选、接线也仅是空操作。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default config — can be overridden via evolution.yaml
DEFAULT_CONFIG = {
    "enabled": True,
    "min_score_threshold": 0.7,
    "max_candidates": 3,
    "injection_mode": "prepend",
}


class PromptSelector:
    """Retrieves optimal prompt variants from the registry."""

    def __init__(
        self,
        registry,  # PromptRegistry (avoid circular import)
        config: dict | None = None,
    ):
        self.registry = registry
        self.config = {**DEFAULT_CONFIG, **(config or {})}

    @property
    def enabled(self) -> bool:
        return self.config.get("enabled", True)

    def select(
        self,
        section_name: str,
        bid_type: str = "",
    ) -> list[dict]:
        """Select the best prompt candidates for a section.

        Returns list of dicts with keys: id, prompt_text, completeness_score
        Empty if evolution is disabled or no candidate found.
        """
        if not self.enabled:
            return []

        min_score = self.config.get("min_score_threshold", 0.7)
        max_candidates = self.config.get("max_candidates", 3)

        candidates = self.registry.get_best(
            section_name=section_name,
            bid_type=bid_type,
            min_score=min_score,
            limit=max_candidates,
        )

        if not candidates:
            logger.debug(f"No prompt candidates for {section_name}/{bid_type}")
        else:
            best = candidates[0]
            logger.info(
                f"Selected prompt for {section_name}: "
                f"score={best['completeness_score']:.2f}, "
                f"used={best['used_count']}x"
            )
            # Mark as used
            self.registry.mark_used(best["id"])

        return candidates

    def inject_into_prompt(
        self,
        base_prompt: str,
        section_name: str,
        bid_type: str = "",
    ) -> str:
        """Inject the best evolved prompt into the base prompt.

        Args:
            base_prompt: The default prompt template for this section
            section_name: Section name (e.g. "第三章 服务方案")
            bid_type: Bid type (e.g. "服务")

        Returns:
            Enhanced prompt string
        """
        candidates = self.select(section_name=section_name, bid_type=bid_type)

        if not candidates:
            return base_prompt

        mode = self.config.get("injection_mode", "prepend")
        best = candidates[0]

        injection = (
            f"\n\n[已学习的优化策略 — 基于历史高分章节（评分 {best['completeness_score']:.0%}）]\n"
            f"以下策略曾帮助同类章节获得高分，请参考但不拘泥：\n"
            f"{best['prompt_text']}\n"
            f"[优化策略结束]\n"
        )

        if mode == "prepend":
            return injection + "\n" + base_prompt
        elif mode == "append":
            return base_prompt + "\n" + injection
        else:  # replace
            return injection

    def get_evolution_summary(self) -> dict[str, Any]:
        """Get a summary of the evolution state."""
        top = self.registry.get_top_scoring_sections(limit=10)
        total = self.registry.count()

        return {
            "total_prompts": total,
            "top_sections": top,
            "enabled": self.enabled,
            "config": self.config,
        }
