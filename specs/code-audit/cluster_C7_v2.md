# 簇 C7 — 复审计报告（v2）

> 审计对象（同 C7）：`bid-agent/core/evolution/` 下 `prompt_selector.py`、`prompt_registry.py`、`consistency_lesson_store.py`、`consistency_lesson_extractor.py`、`metrics_tracker.py`，并跨文件核对 `core/nodes/cross_reference_checker.py`、`core/nodes/feedback_processor.py`、`core/nodes/section_generator.py`，对照 `system-workflow.md`（⑨ FeedbackProcessor 自进化触发、§4.3 一致性自进化系统、⑥ 多重 Prompt 注入）。
> 审计方法：逐行 Read 上述 evolution 5 文件 + 规范，并 Grep 全仓确认调用方与跨文件映射；逐项核对 C7 簇 4 个旧问题是否已修复，并查找回归/遗漏。
> 结论概览：**4 项旧问题中 2 项已修复（H11、M11），2 项未修复（M12 去重分支仍在、L9 PromptSelector/PromptRegistry 仍无节点调用）；新发现 MetricsTracker 同样无节点调用（L9 扩展）、get_lessons_for_section 关键词兜底过注入（N1）、quality 兜底仍落 mutual_exclusion（N2）。**

## 1. 文件清单（路径 + 行数 + 一句话职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/evolution/prompt_selector.py` | 130 | PromptSelector：从 PromptRegistry 取最优 prompt 变体并注入 SectionGenerator |
| `bid-agent/core/evolution/prompt_registry.py` | 233 | PromptRegistry：高分 prompt 变体 SQLite 持久化（自进化） |
| `bid-agent/core/evolution/consistency_lesson_store.py` | 190 | ConsistencyLessonStore：一致性经验 JSON 持久化、去重、合并、查询/注入 |
| `bid-agent/core/evolution/consistency_lesson_extractor.py` | 357 | ConsistencyLessonExtractor：问题→经验（Type 映射+规则模板+LLM） |
| `bid-agent/core/evolution/metrics_tracker.py` | 327 | MetricsTracker：分章节质量趋势追踪（SQLite） |
| `system-workflow.md` | 716 | 系统工作流规范（对照基准） |

## 2. 旧问题修复核对表（C7 簇）

| 旧问题 | 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|--------|----------|---------------------|------|------|
| **H11** CrossReference 三类未映射+缺模板 | ⑩ 检查项契约偏离/技术参数；§4.3 | `consistency_lesson_extractor.py:217-234`（`_map_cross_ref_type`）、`:58-72`（模板）、`cross_reference_checker.py:267,280,334` | ✅ **已修复** | 映射表已补全 `tech_parameter_mismatch→tech_parameter_mismatch`、`contract_forbidden_institution→contract_deviation`、`contract_forbidden_industry→contract_deviation`（`:229,231,232`），与 checker 实际产出 type 完全一致；`_RULE_TEMPLATES` 新增 `tech_parameter_mismatch`（`:58-64`）与 `contract_deviation`（`:65-72`），`_extract_with_template` 可命中模板，`contract_deviation` 严重性映射为 high（`:27`）。 |
| **M11** quality 经验大多无法注入 | §4.3 查询按章节/bid_type 筛选 | `consistency_lesson_extractor.py:175`（`_extract_sections_from_text`）、`consistency_lesson_store.py:149-163`（`get_lessons_for_section`） | ✅ **已修复（含补偿）** | `extract_from_quality` 现用 `_extract_sections_from_text` 从问题文本抽关联章节（`:175`）；`get_lessons_for_section` 新增关键词兜底 `any(kw in section_name …)`（`:160`）。经验现能被检索命中。⚠️ 但该关键词兜底引入过度注入风险，见 N1。 |
| **M12** 去重偏离规范（sim≥0.6 无章节重叠也合并） | §4.3 去重=Jaccard+章节重叠 | `consistency_lesson_store.py:79-81` | ❌ **未修复** | 原问题分支仍在：`if sim >= 0.6: return lesson`（`:80-81`），即关键词 Jaccard≥0.6 时**即使无章节重叠也合并**。规范要求是 Jaccard 与章节重叠 AND，此分支违背规范，仍可能误并不同问题（同 issue_type 因关键词模板相同 sim=1.0 必然被合并，跨章节丢失针对性）。 |
| **L9** PromptSelector/PromptRegistry 无节点调用 | ⑥ 多重 Prompt 注入（潜在自进化闭环） | Grep 全仓（见 §4） | ❌ **未修复** | Grep 确认 `PromptSelector`/`PromptRegistry` 仅出现在 evolution 模块自身与测试，无任何 pipeline 节点 import/调用；`inject_into_prompt` 无调用方。prompt 自进化闭环未接线。MetricsTracker 同理（见 N3）。 |

## 3. 规范整体符合度核对

| 规范条款 | 状态 | 证据 / 说明 |
|----------|------|------------|
| §4.3 Extractor→Store(data/consistency_lessons.json)→SectionGenerator 注入 | ✅ | FeedbackProcessor 调 `ConsistencyLessonExtractor.learn_from_state`（`feedback_processor.py:44-45`）；Store 默认路径 `bid-agent/data/consistency_lessons.json`（`consistency_lesson_store.py:15-16`）；SectionGenerator 调 `build_directive_block`（`section_generator.py:348-350`）。闭环接通。 |
| §4.3 去重 Jaccard+章节重叠 | ⚠️ 部分 | `_find_similar` 主分支 `sim>=0.3 AND section_overlap`（`:77`）符合；但额外 `sim>=0.6` 无重叠分支（`:80-81`）违规（M12 ✗）。 |
| §4.3 经验合并 keywords+occurrence_count | ✅ | `add_lesson` 合并时并 keywords 集、递增 occurrence_count（`:106-110,136`）。 |
| §4.3 查询按章节和 bid_type 筛选、按 occurrence_count 降序 | ⚠️ 部分 | `get_lessons_for_section` 按章节+bid_type 过滤并 `occurrence_count` 降序（`:149-163`）✓；但关键词兜底（`:160`）放宽了章节过滤，造成过注入（N1）。 |
| ⑨ 自进化触发（FeedbackProcessor 调 Extractor） | ✅ | `feedback_processor.py:44-45` 已实现。 |
| ⑥ Prompt 自进化（PromptRegistry/PromptSelector 闭环） | ❌ | 模块实现完整（`prompt_registry.py`/`prompt_selector.py`），但无任何节点消费（L9 ✗）；规范 ⑥ 未强制要求此闭环，属「已实现未接线」可优化项。 |
| MetricsTracker 职责 | ❌ | 实现完整且含 BUG-06/07 修正，但无节点调用（N3）。 |

## 4. 新发现问题（回归 / 遗漏）

### 中
- **N1 — 回归/设计风险：`get_lessons_for_section` 关键词兜底导致经验过注入（false-positive）**
  `consistency_lesson_store.py:160` `section_match = any(kw in section_name for kw in keywords)` 用模板关键词（含单字 `"人"`、`"名"`、`"元"`、`"台"`、`"辆"`、`"不一致"` 等，见 `extractor.py:35,42,49,77,83`）做章节名子串匹配。单字/通用关键词极易在任意章节名中偶然命中（如 `"人"`/`"名"` 易命中含「人员/报名」章节），使与该章节无关的经验被注入 prompt，制造噪声甚至误导约束。M11 修复把「永不命中」推向「过度命中」另一极端。
  - 建议：去掉 `:160` 关键字兜底，或改为「仅当 applicable_sections 经 `_extract_sections_from_text` 已填充时匹配」，避免单字关键词泛匹配。

### 低
- **N2 — `extract_from_quality` 兜底仍落 `mutual_exclusion`**
  `consistency_lesson_extractor.py:206` `_classify_quality_issue` 默认返回 `"mutual_exclusion"`。虽已按关键词智能分类，但凡未命中任何关键词的 quality 一致性问题仍被归为互斥类，与 H11 根因同源（兜底桶）。属低危残留：可能把「金额/数量」类问题错标为互斥。
  - 证据：`:184-206`。建议兜底改为更中性类型或依赖 `_extract_sections_from_text` + 默认 `format_violation` 之外的显式未知桶。

- **N3 — MetricsTracker 同样无任何节点调用（L9 扩展）**
  Grep 全仓确认 `MetricsTracker` 仅在 `tests/unit/test_metrics_tracker.py` 与 `tests/integration/test_pipeline_e2e.py`（仅实例化，未接入 graph）出现，无任何 pipeline 节点 import/调用。`record()` 不被任何检查节点（QualityChecker/ScoreSimulator）调用 → 质量趋势追踪是死代码，§4.3 的「趋势分析」不可达。与 L9 同一性质：evolution 闭环仅「一致性经验」半接通，其余自进化/度量模块未接线。
  - 证据：`metrics_tracker.py:79-119`（`record` 无调用方）；`prompt_registry.py`/`prompt_selector.py` 同状况。

## 5. 修复状态结论

| 项目 | 结论 |
|------|------|
| 已修复 | **H11**（三类映射+模板补全，`extractor.py:217-234,58-72` + `cross_reference_checker.py:267,280,334`）、**M11**（章节提取+关键词兜底，`extractor.py:175` + `store.py:149-163`） |
| 未修复 | **M12**（`sim>=0.6` 无章节重叠合并分支仍在，`store.py:79-81`）、**L9**（PromptSelector/PromptRegistry 无节点调用） |
| 新缺陷 | **N1 中**：`get_lessons_for_section` 关键词兜底过注入（`store.py:160`）；**N2 低**：quality 兜底仍落 mutual_exclusion（`extractor.py:206`）；**N3 低**：MetricsTracker 无节点调用（L9 同性质扩展） |

**优先处理建议**：① 修 M12（删除 `store.py:79-81` 或改为 `sim>=0.6 AND section_overlap` 以符合规范）；② 收紧 N1 关键词兜底避免过注入；③ 将 L9/N3 的 PromptSelector/PromptRegistry/MetricsTracker 接入节点（QualityChecker→MetricsTracker.record、SectionGenerator→PromptSelector.inject_into_prompt）以闭合自进化环，否则标记为「预留未启用」。
