# 修复记录 — M4（FeedbackProcessor 三层反馈历史缺失）

> 修复日期：2026-07-20
> 修改文件（唯一）：`bid-agent/core/nodes/feedback_processor.py`
> 审计依据：`specs/code-audit/cluster_C4_v2.md` M4 项
> 规范依据：`system-workflow.md` 节点 ⑨「三层反馈历史管理（L1 global_constraints / L2 compressed_history / L3 context_window_size=3）」

## 问题根因
`AgentState` 已声明 `global_constraints / compressed_history / context_window_size` 三个字段（默认值 `[]` / `""` / `3`），但 `feedback_processor` 仅维护扁平 `feedback_history` 列表，从未填充这三个字段 → 三层语义落空，违反规范 ⑨。

## 修复方案（仅改 feedback_processor.py）
1. 新增模块常量 `DEFAULT_CONTEXT_WINDOW_SIZE = 3`。
2. 新增三层派生函数（纯函数，不破坏状态）：
   - `build_global_constraints(history, existing)` — Layer 1：收集 `scope=global` 的反馈文本，跨轮次累积、去重、永不丢弃。
   - `compress_feedback_history(history)` — Layer 2：按 `(scope, target_section, feedback_type, 归一化问题)` 去重/合并，保留最新一条（last-write-wins）。
   - `format_compressed_history(compressed)` — 将压缩结果渲染为摘要字符串，写入 `state.compressed_history`。
   - `get_recent_context_window(history, size)` — Layer 3：仅返回最近 `size` 轮，供下游构造 prompt 上下文。
   - `build_feedback_layers(history, size)` — 一次性导出三层结构（含 `recent_context`）。
3. `feedback_processor` 节点：
   - 入口读取 `context_window_size / global_constraints / compressed_history`（带默认值，兼容旧状态）。
   - 主返回分支计算并回写 `global_constraints`、`compressed_history`、`context_window_size` 三层字段；
   - 无 issues 早返回分支同样携带三层字段，避免误清空；
   - **`feedback_history` 保持扁平 `list[dict]` 不变**（SectionGenerator 的 `_collect_section_feedback` 直接消费该列表，向后兼容）。

## 兼容性确认
- `feedback_history` 扁平列表契约未变 → SectionGenerator / GUI 多个面板 / 现有测试（test_feedback_processor 等）均不 KeyError。
- 四类反馈（content_fix/style_adjust/structure/score_align）、作用域（global/local）、一致性自进化触发（`_learn_consistency_lessons`）逻辑完全未改动。
- 回写的三个字段本就是 `AgentState` 已声明 channel，无类型契约破坏。

## 验证结果
- `python3 -m py_compile core/nodes/feedback_processor.py`：COMPILE_OK，无语法错误。
- 临时脚本（已删除）覆盖场景：多轮、global/local 混合、重复问题；
  - Layer 1：`global_constraints` 正确累积去重（字体/字号/结构层级）；
  - Layer 2：重复「字体」问题压缩后仅 1 条；
  - Layer 3：`get_recent_context_window(...,3)` 仅含最近轮次；
  - 向后兼容：导出仍含「字体」全局约束且 `feedback_history` 仍为扁平 `list[dict]`；
  - 结果 `ALL_M4_CHECKS_PASSED`。

## 结论
M4 已修复：feedback_processor 现按规范 ⑨ 维护三层反馈历史（global_constraints / compressed_history / context_window_size=3），且对下游 SectionGenerator 完全向后兼容。
