# Fix 记录 — L9：PromptRegistry / PromptSelector 运行时接入评估

> 审计对象：`bid-agent/core/evolution/prompt_registry.py`、`prompt_selector.py`
> 对照：`specs/code-audit/cluster_C7_v2.md` §2 L9 行、`system-workflow.md` §4.3 / 节点 ⑨
> 处置方式：**未改代码，仅补模块级文档说明**（符合任务第 3 条）

## 评估结论

1. **功能重叠判断**：Registry/Selector 与已修通的 ConsistencyLessonStore 注入**非重叠**，属并行机制。
   - ConsistencyLessonStore：问题 → 结构化「指令规则」(directive)，且已接通（FeedbackProcessor→Extractor→Store→SectionGenerator，H11 已修）。
   - PromptRegistry/Selector：保存「整段高分 prompt 变体」并按历史表现回灌，机制不同、具备 Store 没有的独特价值（按历史表现选最优 prompt 变体）。故**非冗余设计**。

2. **是否存在清晰、<20 行、低风险的接入点**：**否**。关键障碍：
   - Grep 全仓确认：两模块仅出现在 `__init__.py` 导出、tests 实例化、database.py 注释，**无任何 pipeline 节点 import/调用**；且无任何 `.save()` 写入路径 → Registry 恒为空库。
   - 因此即便把 `Selector.inject_into_prompt()` 接入 SectionGenerator，也会因空候选**永久降级为原 base_prompt**（即空操作、无收益），必须**同时**接入 `.save()` 写入路径才有意义——而写入需明确 `prompt_text` 来源（`_get_section_prompt` 当前不回传它），改动 >20 行且需 QualityChecker 评分联动，超出「低风险」范围。
   - `injection_mode="replace"` 会整体覆盖已接好的 契约/经验/RAG/反编造 结构化 prompt，与 Store 注入路径存在**冲突/风险**。

3. **决策**：依据任务第 3 条，**不动代码**，避免破坏已正确工作的 Store→SectionGenerator 注入（H11）。仅在两文件顶部补 L9 状态 docstring，说明：未接线事实、与 Store 路径关系、未来接入建议位置与注意事项（禁止 replace、需同时接 save 写入）。

## 是否改动代码

**否。** 仅新增注释/docstring（不影响运行时行为）。

## 验证结果

- `python3 -m py_compile` 对 `core/evolution/prompt_registry.py`、`core/evolution/prompt_selector.py` 均通过（PY_COMPILE_OK）。
- 已正确工作的 Store→SectionGenerator 注入路径（H11）**未被触碰**，无回归风险。

## 后续建议（非本次范围）

若后续要启用 prompt 自进化闭环，应一次性接：① QualityChecker PASS+高分章节 → `PromptRegistry.save(prompt_text=实际构造的 prompt)`；② `section_generator._get_section_prompt` 末尾（Store 注入之后）调 `Selector.inject_into_prompt`，并强制 `injection_mode` 为 `prepend`/`append`（禁用 `replace`）。建议以独立 PR 评估后再落地。
