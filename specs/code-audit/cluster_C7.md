# 簇 C7 — 审计发现

> 审计对象：bid-agent/core/evolution 一致性自进化模块（spec/007）vs system-workflow.md §4.3 / §⑨
> 审计日期：2026-07-20

## 1. 文件清单（路径+行数+职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| core/evolution/consistency_lesson_store.py | 190 | 经验持久化（JSON，data/consistency_lessons.json）、Jaccard+章节重叠去重、occurrence_count 累加、按章节/bid_type 查询与 prompt 注入文本构建 |
| core/evolution/consistency_lesson_extractor.py | 328 | 从 cross_ref/compliance/quality 三类报告提取结构化经验；规则模板 + 可选 LLM；learn_from_state 聚合 |
| core/evolution/prompt_selector.py | 130 | 从 PromptRegistry 选最优 prompt 变体并注入 SectionGenerator prompt（prompt 自进化，非一致性自进化） |
| core/evolution/prompt_registry.py | 233 | SQLite 存储高分 prompt 变体（prompt 自进化） |
| core/evolution/metrics_tracker.py | 327 | SQLite 记录每章节质量指标趋势（成功率/修订轮次/失败原因/趋势） |
| core/nodes/feedback_processor.py | 214 | §⑨ 反馈处理；内含 `_learn_consistency_lessons` 调用 Extractor（跨文件协作） |
| core/nodes/section_generator.py | 832 | §⑦ 章节生成；`_get_section_prompt()` 注入 ConsistencyLessonStore 经验（跨文件协作） |

## 2. 模块实现对照表

| 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|----------|---------------------|------|------|
| §⑨ FeedbackProcessor 处理一致性问题时调用 ConsistencyLessonExtractor | feedback_processor.py:38,45,178 | ✓ | `_learn_consistency_lessons` 在 issues 非空时调用 `extractor.learn_from_state(state, bid_type)` |
| §4.3 Extractor→Store→SectionGenerator 链路 | feedback_processor.py:178 → extractor.py:297 → store.py:84 → section_generator.py:439,475 | ✓ | 链路连通 |
| §4.3 持久化 data/consistency_lessons.json | store.py:15-16,54-57 | ✓ | 基于 `__file__` 上溯 3 级得 `bid-agent/data/consistency_lessons.json`（文件已存在，路径自洽） |
| §4.3 去重：Jaccard + 章节重叠 | store.py:63,68-82,104 | 错误(轻) | 规范为"AND"；代码额外增加 `sim>=0.6` 无章节重叠也合并的分支(store.py:80-81)，可能误并不同问题 |
| §4.3 经验合并 keywords + occurrence_count 递增 | store.py:106,108 | ✓ | 命中相似项时 `occurrence_count+1` 且 keywords 取并集 |
| §4.3 查询按章节和 bid_type 筛选、occurrence_count 降序 | store.py:149-163,179,189 | ✓ | `get_lessons_for_section` 按章节名+bid_type 过滤并 `occurrence_count` 降序 |
| §4.3 SectionGenerator._get_section_prompt() 注入经验 | section_generator.py:331-344,408,439,475 | ✓ | `_get_consistency_lesson_directive`→`build_directive_block` 拼接注入（generic 与 optimized 两支均注入） |
| §⑩ CrossReferenceChecker 跨章问题 → Extractor | extractor.py:196-205 vs cross_reference_checker.py:267,280,334 | 缺失 | 见 §3-高 |
| §4.3 查询按 bid_type 独立筛选 | store.py:165-167(get_lessons_by_type) | 缺失(轻) | 仅支持按 issue_type 查，无"仅按 bid_type"查询方法；规范"按章节和 bid_type"可由 get_lessons_for_section 满足 |

## 3. 错误/缺陷明细（高/中/低）

### 高
- **C7-H1：CrossReferenceChecker 三类不一致类型未被映射，落入默认 mutual_exclusion（错误分类）**
  - 证据：extractor.py:196-205 `_map_cross_ref_type` 仅映射 `number/amount/date/document_composition_mismatch` 4 种，默认返回 `mutual_exclusion`。
  - 但 cross_reference_checker.py 实际产出 `contract_forbidden_institution`(line 267)、`contract_forbidden_industry`(line 280)、`tech_parameter_mismatch`(line 334) 三种。
  - 后果：契约偏离/拼凑残留（规范 §⑩ 标记为 CRITICAL）与技术参数不一致被错误归类为 mutual_exclusion，注入"互斥提交方式"规则而非对应的契约/技术规则，经验语义错误，且规范未提供 contract_deviation 规则模板（即便正确映射也会因无模板而 `return None`，见 extractor.py:237-239）。
  - 位置：consistency_lesson_extractor.py:196-205

### 中
- **C7-M1：quality_report 一致性经验大多无法被注入（匹配脆弱）**
  - 证据：extractor.py:148-164 `extract_from_quality` 用 `_extract_sections_from_text` 仅当章节名原文字面出现在问题文本才写入 `applicable_sections`；否则为空。store.py:149-162 `get_lessons_for_section` 仅当"章节名子串命中"或"keywords 出现在章节名"才返回该经验。
  - 后果：质量检查发现的一致性问题经验，若问题文本未含章节名，则 `applicable_sections=[]` 且模板 keywords（如"互斥""保函""转账"）一般不在章节标题中，导致该经验永远不会被注入任何章节 prompt → 学习到但永不生效。
  - 位置：consistency_lesson_extractor.py:160,187-194；consistency_lesson_store.py:149-162
- **C7-M2：去重逻辑偏离规范（额外 OR 分支）**
  - 证据：store.py:77 为规范所述 `sim>=0.3 AND section_overlap`；store.py:80-81 增加 `sim>=0.6` 时即使无章节重叠也合并。
  - 后果：两条语义不同但关键词重合≥0.6 的问题会被误合并为一条经验，丢失区分度。
  - 位置：consistency_lesson_store.py:80-81

### 低
- **C7-L1：classify 注释与实现不符**
  - 证据：extractor.py:184 注释"默认：通用一致性"，但 extractor.py:185 实际 `return "mutual_exclusion"`，未设通用一致性类型。
  - 位置：consistency_lesson_extractor.py:184-185
- **C7-L2：metrics_tracker schema 声明 node_status 但从不写入**
  - 证据：metrics_tracker.py:19 注释含 `node_status TEXT`，但 record()(metrics_tracker.py:96-102) INSERT 未包含该列，为死字段。
  - 位置：metrics_tracker.py:19,96-102

## 4. 遗漏功能（规范有、代码无）

- **contract_deviation / tech_parameter_mismatch 规则模板缺失**：规范 §⑩ 列出"契约偏离""技术参数跨章一致"检查，但 `_RULE_TEMPLATES`(extractor.py:22-71) 无对应条目；即使修正映射也会在 extractor.py:237-239 因无模板返回 None。
- **仅按 bid_type 查询经验的方法缺失**（轻，规范可由现有方法覆盖）。

## 5. 跨节点/横切问题

- **C7-X1（需人工确认）：PromptRegistry / PromptSelector 未被管道任何节点调用（功能孤立）**
  - 证据：全仓检索 `PromptRegistry`/`prompt_registry` 仅出现在 evolution 模块、tests、specs 文档，**无 node（feedback_processor/section_generator/quality_checker）调用其 `save()`**；`prompt_selector.select/inject_into_prompt` 亦无调用点。模块本身非空壳（SQLite 实现完整），但"QualityChecker PASS+高分→保存 prompt→SectionGenerator 复用"的自进化闭环未接线。
  - 位置：prompt_registry.py 全文；prompt_selector.py 全文
- **C7-X2（需人工确认）：data 目录布局与 knowledge_base 不一致**
  - 证据：`consistency_lessons.json` 落于 `bid-agent/data/`(store.py:15-16 上溯 3 级)；而 `knowledge_base` 由 section_generator.py:365 上溯 4 级定位于项目根 `标书agent02/knowledge_base`。两处数据根目录不同，跨工具共享/备份时可能遗漏。文件实际存在且路径自洽，非运行 bug，但布局不统一。

## 6. 需人工确认项

1. C7-X1：PromptRegistry/PromptSelector 是否本应在 QualityChecker/SectionGenerator 接线？属独立"prompt 自进化"特性还是被遗漏？
2. C7-X2：`data/` 是否应置于项目根（与 knowledge_base 同级）以便统一备份？
3. C7-M1：QualityChecker 产出的 consistency 问题文本是否通常包含章节名？若否，需补充"无章节时回退注入全部章节"策略。
4. quality_checker.py 的 consistency 项结构与 `extract_from_quality`(extractor.py:139-164) 期望的 `list[str]/list[dict]` 是否完全对齐（未逐行读 quality_checker，标"需人工确认"）。
