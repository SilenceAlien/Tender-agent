# 簇 C3 — 复审计报告（v2）

> 审计对象（同 C3）：`bid-agent/core/nodes/section_generator.py`、`bid-agent/core/nodes/optimized_prompts.py`，对照 `system-workflow.md`（⑦ SectionGenerator：8 章标准结构、6 重 Prompt 注入、附加注入、并行生成与错误隔离、定向修订策略），并额外读取 `bid-agent/core/graph.py` 确认 H10 的 graph 绑定。
> 审计方法：逐行 Read 上述 4 文件，逐项核对 C3 簇 3 个旧问题（H9/H10/M7）是否已修复，整体重核 ⑦ 规范符合度，并查找回归/遗漏。
> 结论概览：**3 项旧问题全部已修复（H9、H10、M7）**；发现 **1 项中危新缺陷 N1（劳务外包优化路径 5 章未注入评分要求）**、**2 项低危 N2/N3**、**1 项需人工确认项**。

## 1. 文件清单（路径 + 行数 + 一句话职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/section_generator.py` | 841 | SectionGenerator 节点：8/9 章 prompt 构建、6 重+3 附加注入、并行生成+错误隔离、定向修订 |
| `bid-agent/core/nodes/optimized_prompts.py` | 435 | 劳务外包类优化 prompt 集（9 章 sec1-sec8+sec5b）及旧→新键映射 |
| `bid-agent/core/graph.py` | 672 | 14 节点 LangGraph 管线；确认 SectionGenerator 绑定哪个函数 |
| `system-workflow.md` | 716 | 系统工作流规范（对照基准，重点 ⑦） |

## 2. 旧问题修复核对表（C3 簇）

| 旧问题 | 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|--------|----------|---------------------|------|------|
| **H9** 失败占位符暴露异常 `f"【生成失败：{e}】"`（已算 `failed_section` 未用） | ⑦ 并行生成与错误隔离「失败章使用 `【生成失败：章节名 — 请手动补充】`」 | `section_generator.py:819-821` | ✅ **已修复** | 现 `failed_section = future_to_section[future]["name"]` 已使用，占位符为 `f"【生成失败：{failed_section} — 请手动补充】"`，与规范格式完全一致，不再暴露异常信息。异常仅记录于 `logger.error`（`:820`）。 |
| **H10** 主节点 `section_generator` 跳过已有章节且不读 feedback_history；定向修订只在 `generate_all_sections_parallel` 实现 | ⑦ 定向修订策略「读取 feedback_history，仅重生成被反馈章节」 | `graph.py:56`、`section_generator.py:756,768-779,798` | ✅ **已修复** | 关键：`graph.py:56` 将 `generate_all_sections_parallel` 别名导入为 `section_generator`，**graph 实际绑定的是并行函数**（非 `:653` 的旧主节点）。该并行函数在 `:756` 调 `_collect_section_feedback` 读 feedback_history，`pending`（`797-799`）将被反馈章节纳入重生成，`generate_one`（`:773`）在有反馈时强制重生成并注入修订上下文（`:779`）。定向修订已落地。 |
| **M7** ch2_authorization/ch7_schedule/ch8_after_sales 模板无 `{requirements_context}` 占位符 | ⑦ 6 重「评分对齐：每章 prompt 包含评分要求」 | `section_generator.py:165,234,248` | ✅ **已修复** | 三个通用模板均已补 `{requirements_context}`（ch2 `:165`、ch7 `:234`、ch8 `:248`），`_SECTION_PROMPTS` 全部 8 章现均含占位符，`.format(requirements_context=...)`（`:463`）可正确注入评分要求。 |

## 3. 规范整体符合度核对（⑦）

| 规范条款 | 状态 | 证据 / 说明 |
|----------|------|------------|
| ⑦ 8 章标准结构（ch1-ch8 键名） | ✅（通用）/ ⚠️（劳务外包） | 通用 `DEFAULT_SECTIONS`（`section_generator.py:76-85`）键名 ch1-ch8 与规范表一致；劳务外包走 `OPTIMIZED_SECTIONS`（`optimized_prompts.py:30-40`）为 9 章 sec1-sec8+sec5b（真实中标结构），与规范「8 章」表述不同但属设计意图（见 N5）。 |
| ⑦ 6 重 Prompt 注入：契约 / RAG / 评分对齐 / 类型适配 / 防虚构 / 互斥一致性 | ✅（通用） / ⚠️（评分对齐，劳务外包） | 契约 `_build_contract_block`（`:452-454,489-491`）、RAG `_retrieve_reference`（`:438,474`）、类型适配（`:432-435` 走 optimized）、防虚构 `NO_FABRICATION_DIRECTIVE`（`:446,466`）、互斥一致性（嵌于 `NO_FABRICATION_DIRECTIVE` 的「提交形式一致性指令」`:60-72`）均齐备。**但评分对齐在劳务外包路径 5 章缺失（见 N1）。** |
| ⑦ 附加注入：Mermaid / 表格模板 / 一致性经验 | ✅ | Mermaid：通用 `_DIAGRAM_SECTIONS`（`:470-471`），优化章 sec7/sec8 内嵌 Mermaid 示例（`optimized_prompts.py:274-281` 等）；表格模板 `_inject_table_template`（`:442,479`）；一致性经验 `_get_consistency_lesson_directive`（`:448,484`）。三项均注入。 |
| ⑦ 并行 ThreadPoolExecutor + 单章失败隔离 | ✅ | `generate_all_sections_parallel` 用 `ThreadPoolExecutor`（`:812`），`as_completed` 循环 `try/except`（`:814-823`），单章异常仅写 `【生成失败：章节名 — 请手动补充】` 占位符，不影响其他章。 |
| ⑦ 定向修订读 feedback_history 仅重生成被反馈章节 | ✅ | 见 H10，`_collect_section_feedback`（`:515-550`）+ `pending`/`generate_one`（`:773,798`）。 |
| ⑦ 多轮迭代默认 3 轮 | ✅ | `graph.py` 路由统一 `state.get("max_rounds", 3)`（`:90,101,109,135`），默认 3 轮，符合规范。 |

## 4. 新发现问题（回归 / 遗漏）

### 中
- **N1 — 劳务外包优化路径 5 章未注入评分要求（评分对齐缺失，违反 ⑦「每章」）**
  `optimized_prompts.py` 的优化模板中，**仅 sec5_deviation / sec6_qualification / sec7_service_plan / sec8_emergency 含 `{requirements_context}` 占位符**（`optimized_prompts.py:190,244,353,403`）；而 **sec1_bid_letter（`:45`）、sec2_legal_rep（`:78`）、sec3_authorization（`:90`）、sec4_deposit（`:103`）、sec5b_price_table（`:144`）这 5 章模板不含该占位符**。
  - 机制：`_get_section_prompt` 优化分支调 `get_optimized_section_prompt(key, requirements_context)`（`section_generator.py:436` → `optimized_prompts.py:417-420`），其内部 `template.format(requirements_context=requirements_context)` 对**无占位符的模板直接丢弃**该参数。结果：劳务外包（primary 优化路径）的投标函/身份证明/授权委托书/保证金/分项报价表 5 章 prompt **完全不含招标文件评分要求**。
  - 影响：与通用路径（M7 已修复、8 章全含）不一致，且违反规范 ⑦「每章 prompt 包含评分要求，确保逐条响应评分项」。属上一轮修 M7 时只修了通用模板、漏修优化模板的回归/遗漏。
  - 修复：在该 5 个优化模板中补 `{requirements_context}`（或改 `get_optimized_section_prompt` 在 `.format` 后追加 requirements 块）。建议统一在优化分支显式注入评分要求块，避免依赖模板是否含占位符。

### 低 / 维护风险
- **N2 — 旧主节点 `section_generator`（`:653-720`）成为孤儿代码且仍保留旧缺陷**
  `graph.py:56` 已将并行函数别名覆盖为 `section_generator`，故 `:653` 的 `section_generator(state, …)` 函数已**不被 graph 调用**。但其仍保留「`if section_name in existing_sections and …: continue`（`:686-688）跳过已有章节 + 不读 feedback_history + 无单章 try/except 错误隔离」的旧行为。若任何测试/脚本直接 import 并调用该函数（而非并行版本），会重现 H9/H10 旧缺陷。建议删除或加 `@deprecated` 标注，统一收敛到并行实现。
- **N3 — `generate_all_sections_parallel` 不返回 `current_section`**
  旧主节点在 `:711` 返回 `current_section`，而并行版本（`:834-840`）的返回 dict 仅含 `sections` 与 `node_status`，**未含 `current_section`**。若下游（如审阅/日志）依赖 `state.current_section` 追踪进度，并行路径会使其保持旧值或空。低风险，建议补齐以保持契约一致。

### 需人工确认项
- **A1 — feedback_history 的 `target_section` 存储值是否为「章节名」**
  `_collect_section_feedback`（`section_generator.py:534`）以 `record.get("target_section")` 为键，匹配时与 section **NAME** 比对（`:768` `section_feedback.get(name)`）。需确认 `FeedbackProcessor`/`QualityChecker` 写入 `feedback_history` 时 `target_section` 存的是**章节名（如「第三章 服务方案」/「八、服务方案」）而非键（如 `ch3_service`/`sec7_service_plan`）**。若存键，则定向修订永远匹配不到 → H10 实际部分回退。建议读取 `feedback_processor.py` 与质检节点确认写入格式。
- **A2 — 劳务外包 9 章与规范「并行 8 章」表述差异**
  规范要求 ⑦ 表为 8 章（ch1-ch8），`optimized_prompts.py` 为 9 章（含 sec5b_price_table）。属真实中标结构的有意扩展，非缺陷；但若某些下游/测试硬编码「8 章」计数，可能不一致。建议确认无 8 章硬编码假设。

## 5. 修复状态结论

| 项目 | 结论 |
|------|------|
| 已修复 | **H9**（占位符不再暴露异常，`section_generator.py:819-821`）、**H10**（graph 绑定并行函数并读 feedback_history，`graph.py:56`+`section_generator.py:756,773,798`）、**M7**（通用 3 章补 `{requirements_context}`，`:165,234,248`） |
| 新缺陷 | **N1（中）**：劳务外包优化路径 5 章（sec1/sec2/sec3/sec4/sec5b）模板无 `{requirements_context}`，评分要求被静默丢弃（`optimized_prompts.py:45,78,90,103,144`） |
| 维护风险 | **N2**：旧主节点 `:653-720` 成孤儿且保留旧缺陷；**N3**：并行版本未返回 `current_section` |
| 需人工确认 | **A1** feedback `target_section` 是否按章节名存储（定向修订匹配正确性）；**A2** 9 章 vs 规范 8 章表述差异 |

**优先处理建议**：① 修 N1（在 5 个优化模板补 `{requirements_context}`，或改 `get_optimized_section_prompt` 统一注入评分块）；② 确认 A1 的 `target_section` 写入格式以确保 H10 定向修订真正生效；③ 清理 N2 孤儿旧主节点、补齐 N3 `current_section`。
