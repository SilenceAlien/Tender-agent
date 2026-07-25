# 簇 C3 — 审计发现

> 审计对象：节点 ⑦ SectionGenerator
> 对比基准：`system-workflow.md` §三-⑦ 及 §四 横切关注点
> 审计日期：2026-07-20

## 1. 文件清单（路径+行数+职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `/Users/alanchris/Desktop/标书agent02/bid-agent/core/nodes/section_generator.py` | 832 | SectionGenerator 节点实现：8 章 prompt 模板、6 重+附加注入策略、`ThreadPoolExecutor` 并行生成、基于 `feedback_history` 的定向修订、契约/合同摘要构建 |
| `/Users/alanchris/Desktop/标书agent02/bid-agent/core/nodes/optimized_prompts.py` | 435 | 劳务管理服务类/劳务外包类优化章节集（9 节）与对应 prompt，含 `OLD_TO_NEW_SECTION_MAP` 旧→新映射 |
| `/Users/alanchris/Desktop/标书agent02/system-workflow.md` | 716 | 系统规范（仅作为核对基准，非源码） |

## 2. 节点实现对照表

| 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|----------|---------------------|------|------|
| 8 章标准结构 ch1–ch8 键名 | `section_generator.py:76-85` | ✓ | `DEFAULT_SECTIONS` 键名与规范完全一致 |
| 契约注入(ProjectContextContract) | `section_generator.py:288-313, 442-445, 479-482` | ✓ | 通过 `ProjectContextContract.from_dict().summary()` 构建并前置于 prompt |
| RAG 注入(FAISS 检索) | `section_generator.py:486-503, 428-431, 465-467` | ✓ | `_retrieve_reference` 调用 `get_reference_retriever()`，通用与优化分支均注入 |
| 评分对齐 | `section_generator.py:248-272, 450-454` | 错误 | 仅含 `{requirements_context}` 占位符的章节被注入；`ch2_authorization`/`ch7_schedule`/`ch8_after_sales` 模板无占位符（见 `155-164, 221-230, 232-241`），规范称"每章" |
| 类型适配(劳务 7 子节) | `optimized_prompts.py:30-40, 248-355` | ✓ | `OPTIMIZED_SECTIONS` + `sec7_service_plan` 含 7 子节 |
| 防虚构指令 | `section_generator.py:32-72, 437, 457` | ✓ | `NO_FABRICATION_DIRECTIVE` 含 8 类真实数据，通用与优化分支均注入 |
| 互斥一致性指令 | `section_generator.py:60-71` | ✓ | 含保函/转账、总价/单价、质保金/函、合同类型等互斥场景 |
| Mermaid 图表指令 | `section_generator.py:95-137, 461-462` | ✓ | `_DIAGRAM_SECTIONS={ch3_service,ch5_staffing,ch7_schedule}` 注入；优化 sec7/sec8 内置 Mermaid |
| 表格模板注入(patterns/) | `section_generator.py:347-405, 432-435, 470-472` | ✓ | 仅 `sec6_qualification`/`ch6_qualifications` 触发，`type∈{qualification,unknown}` |
| 一致性经验注入(ConsistencyLessonStore) | `section_generator.py:331-344, 438-441, 474-477` | ✓ | `_get_consistency_lesson_directive` 调用 `store.build_directive_block` |
| 并行 ThreadPoolExecutor | `section_generator.py:719-831, 803-814` | ✓ | `generate_all_sections_parallel` 用 `ThreadPoolExecutor(max_workers≤8)` |
| 单章失败隔离 | `section_generator.py:808-814` | **错误** | 占位符为 `f"【生成失败：{e}】"`，规范要求是 `【生成失败：章节名 — 请手动补充】`；且已计算的 `failed_section`（:810）未被使用 |
| 定向修订(读 feedback_history) | `section_generator.py:506-541, 756-790, 764` | 错误 | 仅 `generate_all_sections_parallel` 实现；主节点 `section_generator`（`672-679`）跳过已有章节且不读 feedback |
| 定向修订保留未反馈章 | `section_generator.py:764` | ✓ | `if name in existing and existing[name] and not feedback_texts: return` |
| 多轮迭代默认 3 轮(1-5) | — | 缺失/跨节点 | SectionGenerator 内无 `max_rounds`/`current_round` 处理，规范列为本节点策略但实现在 FeedbackProcessor/路由层 |
| 并行生成"8 章"语义 | `section_generator.py:670-671` | 错误 | 主函数 docstring 称"In production, only generates ONE section per call"，与规范"并行 8 章"矛盾 |

## 3. 错误/缺陷明细（高/中/低）

### 高
- **H1 失败占位符格式不符且变量冗余**：`section_generator.py:808-812`。规范要求失败章输出 `【生成失败：章节名 — 请手动补充】`，实际代码 `f"【生成失败：{e}】"` 直接暴露异常信息 `e`，且已正确计算的 `failed_section = future_to_section[future]["name"]`（:810）未被拼接到占位符中。属规范矛盾 + 明显 bug。

- **H2 主节点未实现定向修订**：`section_generator.py:672-679`。管线主函数 `section_generator` 以 `if section_name in existing_sections and existing_sections[section_name]: continue` 跳过所有已生成章节，完全不读取 `feedback_history`；定向修订逻辑只存在于 `generate_all_sections_parallel`（:756-790）。若 LangGraph 实际注册的是主函数（需人工确认 `core/graph.py`），则规范"定向修订策略"在主链路不生效。

### 中
- **M1 评分对齐在 3 个通用章节缺失**：`section_generator.py:155-164`(ch2)、`221-230`(ch7)、`232-241`(ch8) 的 prompt 模板不含 `{requirements_context}` 占位符，`_build_requirements_context` 结果无法注入。规范 §6 重注入明确"每章 prompt 包含招标文件评分要求"。优化章节中 sec1/sec2/sec3/sec4/sec5b 同样无占位符（见 `optimized_prompts.py` 对应段），但部分章节（授权委托书）或本无需评分，需人工确认。

- **M2 knowledge_base 物理路径需确认**：`section_generator.py:365`。`kb_root = Path(__file__).parent.parent.parent.parent / "knowledge_base"` 解析为 `标书agent02/knowledge_base`。规范 §4.4 仅以树形展示 `knowledge_base/`，未声明其相对 `bid-agent/` 的位置；若实际位于 `bid-agent/knowledge_base`，则路径少了一层 parent，表格模板注入将静默失败（异常被 `except` 吞掉，:403-405）。

### 低
- **L1 "并行 8 章"与主节点"单章调用"注释矛盾**：`section_generator.py:670-671` docstring 与规范 §③-⑦"并行 8 章"描述冲突，暗示存在两套未统一的设计意图。
- **L2 OLD_TO_NEW_SECTION_MAP 疑似死代码**：`optimized_prompts.py:425-434` 定义 ch*→sec* 映射，但在 `section_generator.py` 与 `optimized_prompts.py` 内均未见引用，需确认是否被其他模块使用。
- **L3 轮次管理不在本节点**：规范将"支持多轮迭代（默认 3 轮，可配置 1-5 轮）"列于本节点，但代码无任何 `max_rounds`/`current_round` 处理，依赖 FeedbackProcessor/路由层，属跨节点职责划分，不视为功能缺失。

## 4. 遗漏功能（规范有、代码无）

经逐条核对，6 重 Prompt 注入、3 项附加注入、并行生成、定向修订在 `generate_all_sections_parallel` 中均有对应实现，**无整项功能彻底缺失**。仅在以下方面为"部分缺失/弱化"：
- 评分对齐注入在 `ch2/ch7/ch8`（及部分优化章节）缺失占位符（见 M1）。
- "并行 8 章"在主节点函数未落地（见 L1/H2）。
- 多轮迭代的"可配置 1-5 轮"在 SectionGenerator 内不可见（见 L3，实现在路由层）。

## 5. 跨节点/横切问题

- **双实现分歧**：`section_generator`（串行、:644）与 `generate_all_sections_parallel`（并行+反馈，:719）功能重叠但行为不一致。规范描述的"并行 8 章 + 错误隔离 + 定向修订"全部只在后者实现；前者缺少反馈读取与并行。需确认 `core/graph.py` 中 SectionGenerator 节点绑定的是哪个函数，否则规范能力可能未接入。
- **轮次策略跨节点**：`max_rounds`/`current_round` 由 FeedbackProcessor（§⑨）与路由函数管理，SectionGenerator 仅被动按 `feedback_history` 重生成，符合解耦设计但需在文档层面与规范 §⑦ 对齐措辞。
- **一致性自进化链路**：规范 §4.3 要求 `ConsistencyLessonStore` 经验注入 `_get_section_prompt`，代码已实现（:331-344, 474-477）；提取端 `ConsistencyLessonExtractor` 是否在 FeedbackProcessor 调用不属本审计范围，需另查。
- **RAG/表格路径一致性**：`_inject_table_template`（:365）与 `_retrieve_reference` 的 KB 根路径需与 `core.retrieval.reference_retriever` 保持一致，避免一侧找到、一侧找不到（见 M2）。

## 6. 需人工确认项

1. `core/graph.py` 中 SectionGenerator 节点实际注册的是 `section_generator` 还是 `generate_all_sections_parallel`？（决定 H2/L1 是否为线上生效缺陷）
2. `knowledge_base/` 的物理绝对路径（位于 `标书agent02/` 还是 `bid-agent/` 下）？（决定 M2 路径是否正确）
3. `ch2_authorization`/`ch7_schedule`/`ch8_after_sales` 是否确属"不应注入评分要求"的章节？（决定 M1 是否按 bug 处理）
4. `OLD_TO_NEW_SECTION_MAP`（`optimized_prompts.py:425-434`）是否被 `core/` 其他模块引用？（决定 L2 是否死代码）
5. 多轮迭代的 1-5 轮可配置是否在路由/FeedbackProcessor 层完整实现并暴露给用户配置？（规范 §⑦ 与 §⑧ AgentState 均提及 `max_rounds` 默认 3）
