# 代码复审计结论 v2 — 标书制作智能体 vs system-workflow.md (v1.0)

> 复审计日期：2026-07-20 下午 | 方法：沿用 `strategy.md` 的 8 簇并行逐行通读（排除 .venv），对照规范条款，证据化 file:line
> 背景：用户称已用其他模型修复上一轮（findings.md）全部问题。本轮目标 = 核实修复 + 找回归/新问题
> 配套文档：`specs/code-audit/cluster_C1_v2.md` ~ `cluster_C8_v2.md`（8 份逐条复核证据）

---

## 〇、总览：修复进展与遗留

| 维度 | 上轮 | 本轮状态 |
|------|------|---------|
| **14 节点编排（graph.py）** | ✅ 正确 | ✅ 仍正确（C8 复核确认，修复未破坏节点图与 19 条路由） |
| **AgentState 20 字段** | ✅ 齐备 | ✅ 齐备 |
| **12 项高危** | 全待修 | **7 项已修复** / 2 项部分修复（H4、H12）/ **3 项未修**（H3、H6、H7） |
| **17 项中危** | 全待修 | **8 项已修复**（含 M14 撤销为符合规范）/ 1 项部分（M17）/ **8 项未修** |
| **9 项低危** | 全待修 | 1 项已修复（L1）/ 其余基本未修（影响小） |
| **🔴 新引入回归** | — | **1 项高危（C6 N1）+ 多项中低危回归**，最严重为关键词检索路径崩溃 |

> 结论：**核心校验逻辑（H1/H2/H5/H8/H9/H10/H11）已实质修复**，跨章一致性环、自检完整度、RAG 接通、失败隔离、定向修订、自进化映射均已落地。但**检索 RAG 仍有 3 项未修 + 1 项致命回归**；装配节点 ⑭ 几乎原封未动；若干修复引入了新回归需立刻处理。

---

## 一、上轮 12 高危 — 修复核对表

| # | 节点 | 问题 | 状态 | 证据 |
|---|------|------|------|------|
| H1 | ⑩ CrossReference | severity 误判（high 写 medium） | ✅ **已修复** | `cross_reference_checker.py:90,153,341,274,287,392-396`（enum 改为 high/critical，verdict `.lower() in ("high","critical")`） |
| H2 | ⑧ QualityChecker | 缺 8000 字完整度门槛 | ✅ **已修复** | `quality_checker.py:544-553`（新增每章≥8000 字检查） |
| H3 | ⑥/§4.2 检索 | 元数据过滤占位 | ❌ **未修复** | `pipeline.py:125-133`（仍为 `if metadata_filter: pass`） |
| H4 | ⑥ TemplateMatcher | MockEmbedder 64 维 | ⚠️ **部分修复** | `template_library.py:211,215`（维度改 1536，但仍 `MockEmbedder` 随机向量，未接 OpenAI） |
| H5 | ⑦ RAG | 语义检索空操作 | ✅ **已修复** | `reference_retriever.py:121-148`（`_load_doc_by_id` 接通 doc store） |
| H6 | ⑥ 子类型检索 | 权重表死代码 | ❌ **未修复** | `subtype_router.py:256-275`（Grep 全仓无任何调用方；冷启动最近邻未进检索融合） |
| H7 | ⑭ 装配 | 页码/页眉页脚/Mermaid/目录 | ❌ **未修复** | `doc_assembler.py`（`:1119-1121` 注释掉、`Mermaid` 仍树形 `:263/381-447`、`目录` 硬编码 `:1199`） |
| H8 | ④ GUI | 漏 bid_subtype 字段 | ✅ **已修复** | `info_verification.py:30` / `info_verification_gate.py:50`（前后端均 11 字段） |
| H9 | ⑦ 失败隔离 | 占位符暴露异常 | ✅ **已修复** | `section_generator.py:819-821`（用 `failed_section` 生成规范格式） |
| H10 | ⑦ 定向修订 | 主节点不读 feedback | ✅ **已修复** | `graph.py:56` 绑定 `generate_all_sections_parallel`；该函数读 `feedback_history` `:756`、仅重生成被反馈章节 `:773,798` |
| H11 | §4.3 自进化 | CrossReference 三类未映射 | ✅ **已修复** | `extractor.py:217-234`（映射补全 + 新增规则模板 `:58-72`） |
| H12 | §4.1 LLM | Anthropic 工厂不存在 | ⚠️ **部分修复** | `providers.py:85-109`（工厂已补），但 `config.yaml:11-14` 死配置仍在、端到端不通、依赖未声明 |

---

## 二、上轮 17 中危 — 修复核对表

| # | 节点 | 问题 | 状态 | 证据 |
|---|------|------|------|------|
| M1 | ⑥ TemplateMatcher | 缺关键词兜底 | ✅ 已修复 | `template_matcher.py:110-150,181-192`（`_keyword_fallback_match`） |
| M2 | ⑥ TemplateMatcher | 未消费 bid_subtype/分区 | ❌ 未修复 | `template_matcher.py:153-268` |
| M3 | ⑤ Eligibility | WARNING 语义偏差 | ❌ 未修复 | `eligibility_checker.py:396-406,365-383`（缺失硬资质一律 FAIL） |
| M4 | ⑨ Feedback | 三层反馈历史缺失 | ❌ 未修复 | `feedback_processor.py:125,207`（仍扁平列表） |
| M5 | ⑪ Compliance | 缺知识产权/保密 | ✅ 已修复 | `compliance_checker.py:164-198` |
| M6 | ⑪ Compliance | 格式合规仅声明级 | ❌ 未修复 | `compliance_checker.py:58-105` |
| M7 | ⑦ 评分对齐 | ch2/ch7/ch8 缺注入 | ✅ 已修复 | `section_generator.py:165,234,248`（8 章全含） |
| M8 | ⑭ 装配 | 页边距默认值冲突 | ✅ 已修复 | `doc_assembler.py:27,46`（改上下3.7/左右2.8） |
| M9 | ⑭ 装配 | 文件名时间戳 | ❌ 未修复 | `doc_assembler.py:1475-1490`（`state.py` 无 `project_id`） |
| M10 | ⑭ 装配 | 占位符正则宽松 | ❌ 未修复 | `doc_assembler.py:1230`（`[^】]*` 无冒号强制） |
| M11 | §4.3 自进化 | 经验注入匹配脆弱 | ✅ 已修复 | `extractor.py:175` + `store.py:160`（抽章节+关键词兜底） |
| M12 | §4.3 自进化 | 去重偏离规范 | ❌ 未修复 | `store.py:79-81`（`sim>=0.6` 无章节重叠也合并） |
| M13 | GUI 进度 | 缺 InfoVerificationGate | ✅ 已修复 | `progress.py:10,28`（现 14 项） |
| M14 | GUI 结构 | 仅 4 Tab | ✅ 确认符合规范 | `review_panel.py:204-220`（信息校验为嵌入组件，非面板） |
| M15 | GUI 交互 | rejected 重跑整段 Phase2 | ❌ 未修复 | `review_panel.py:405-418` + `graph.py:519` |
| M16 | §4.1 默认 | router 默认模型冲突 | ✅ 已修复 | `router.py:17-23`（全 deepseek） |
| M17 | ④ 来源标注 | 缺 LLM 类别 | ⚠️ 部分修复+回归 | `info_verification_gate.py:118-119`（加枚举但"补充说明"成死代码、bid_subtype 无来源、次级源分支死） |

---

## 三、上轮 9 低危 — 修复核对表

| # | 问题 | 状态 | 证据 |
|---|------|------|------|
| L1 | 检索 L2 非余弦 | ✅ 已修复 | `faiss_index.py:54-58`（改 `METRIC_INNER_PRODUCT` + 归一化） |
| L2 | ReqExtractor 表格充足仍调 LLM | ❌ 未修复 | `req_extractor.py:421`（仍无条件调 LLM，疑似未实质改动） |
| L4 | QualityChecker LLM 无重试 | ❌ 未修复 | `quality_checker.py:467` |
| L6 | ScoreSimulator 截断 6000 vs 8000 | ❌ 未修复 | `score_simulator.py:196` |
| L7 | graph `route_after_score_sim` 死代码 | ❌ 未修复 | `graph.py:114-121` |
| L8 | database.py 仅测试引用 | ❌ 未修复 | `database.py` |
| L9 | PromptRegistry/Selector 未接线 | ❌ 未修复 | Grep 全仓仅测试引用（evolution 模块） |
| L3/L5 | chunk_overlap / "第一"负向前行 | 未触及 / 维持原判定 | 见各簇 v2 报告 |

---

## 四、🔴 新引入回归（最优先处理）

| # | 严重度 | 节点 | 问题 | 证据 | 影响 |
|---|------|------|------|------|------|
| **N0** | 🔴 **高** | ⑥ RAG | **`_keyword_score` 方法定义丢失**：`reference_retriever.py:149-162` 本应是 `def _keyword_score(self, query, text)` 方法体，却被遗留在 `_load_doc_by_id` 的 `return` 之后且**缺 `def` 行**。Grep 全仓无任何 `def _keyword_score`。`:192` 调用时必抛 **AttributeError** | `reference_retriever.py:149-162,192` | **关键词检索（默认/离线主路径 + 语义失败兜底）运行期直接崩溃**，RAG 注入失败。实为修 H5 接 doc store 时引入的回归 |
| N1 | 中 | ⑦ 优化模板 | 修 M7 时漏改：劳务外包优化路径 5 章（sec1/sec2/sec3/sec4/sec5b）模板**仍无 `{requirements_context}`**，`format()` 直接丢弃评分要求 | `optimized_prompts.py:45,78,90,103,144` | 违反 ⑦「每章注入评分要求」，优化模式下评分对齐失效（回归） |
| N2 | 中 | ⑪ Compliance | 保密泄露正则 `\d{16,19}`/`\d{17}[\dXx]` 过宽，易误报 FAIL | `compliance_checker.py:182-198` | 正常文本触发虚假合规 FAIL |
| N3 | 中 | ⑧ Quality | 8000 字门槛误伤格式固定短章 → 虚假 FAIL 循环 | `quality_checker.py:544-553` | 固定短章节无法通过自检，死循环风险 |
| N4 | 中 | ④ 来源标注 | `M17` 修复引入：补充说明源永产出、`bid_subtype` 恒显"缺失"、次级源分支死、`apply_user_corrections` 未回写 `state.bid_subtype`（跨节点） | `info_verification_gate.py:118-119` + `:50` | 子类型分区检索输入断链（与 H8 修复部分抵消） |
| N5 | 中 | §4.3 自进化 | `M11` 修复过度：单字关键词（"人""元"）做章节名子串匹配，过度注入误命中无关章节 | `store.py:160` | 自进化经验噪声上升（从"永不命中"摆向"过度命中"） |
| N6 | 低 | ⑥ RAG | `_load_doc_by_id` 硬编码 query="招标要求" 抽片段，与真实 query 不相关 | `reference_retriever.py:134,144` | 语义 snippet 质量劣化（非崩溃） |
| N7 | 低 | ⑦ 孤儿代码 | 旧主节点 `section_generator`（`:653-720`）成孤儿仍含旧缺陷；并行版未返回 `current_section` | `section_generator.py:653-720` | 维护负担 + 潜在误用 |
| N8 | 低 | ⑭ 装配 | `v{current_round}` 首轮导出为 `v0`；目录为假列表无真实页码；字体环境依赖 | `doc_assembler.py:1475-1490,1199` | 产出物不规范 |
| N9 | 低 | ④/③ | 子类型分支 `llm_fn=None` 时改用真实 LLM（与主流程 mock 不一致）；`forbidden_inustries` 过宽；早期返回 `format_rules:{}` 绕过默认值 | `req_extractor.py:489-493`、`contract_extractor.py` | 行为不一致 / 边界过宽 |
| N10 | 低 | §4.1 | `config.yaml` `llm` 段死配置 + 双配置分裂；Anthropic 依赖 `langchain_anthropic` 未声明于 requirements | `config.yaml` / `providers.py:91` | H12 部分修复遗留，部署风险 |

---

## 五、仍需人工确认（静态无法判定）

1. `knowledge_base/` 索引的**构建/灌装入口**是否存在？doc_id 是否按"知识库相对路径"命名？（决定 H5 语义检索是否真生效，C6 N4）
2. `feedback_history.target_section` 是否存章节名？（决定 H10 定向修订线上是否真生效，C3 A1）
3. 8 章 vs 规范 9 章表述差异是否为笔误（C3 A2）
4. EligibilityChecker WARNING 是否刻意简化为"仅库空触发"（M3）
5. `VectorIndexManager.search` 返回欧氏 L2 还是平方 L2（影响 H4 相似度换算）
6. API Key"不落盘"诉求是否仍成立（规范自相矛盾，H12/L8 相关）
7. `database.py` 是否计划接入生产路径（L8）
8. ch2/ch7/ch8 是否确属"不应注入评分要求"章节（M7 已按注入处理，需确认意图）

---

## 六、修复优先级建议（v2）

**P0 — 立刻修（含回归）：**
- **N0**：恢复 `reference_retriever.py:149-162` 的 `def _keyword_score` 方法（移出 `_load_doc_by_id`、补 `def` 行、4 空格缩进）。这是本轮最致命项，会直接让 RAG 关键词路径崩溃。
- H7：⑭ 装配——页码/页眉页脚/真实目录/Mermaid 力导向（政府采购格式硬要求）。
- H3 + H6：检索——实现元数据过滤、消费权重表（否则分区检索仍是摆设）。

**P1 — 明显 bug / 规范偏离：**
- N1：优化模板补 `{requirements_context}`（回归，修 M7 漏改）。
- N3：8000 字门槛加"格式固定短章豁免"或按章节类型判定。
- N2 + M6：Compliance 正则收敛 + 格式合规内容级校验。
- N4 + M2 + M17：④ 来源标注与子类型分区输入链对齐。
- H4：将 `MockEmbedder` 切真实 `OpenAIEmbedder`（或显式声明离线策略）。

**P2 — 一致性 / 死代码清理：**
- M9/M10/M12/M15/L7/L8/L9/N5/N7/N10：文件名契约、占位符正则、去重偏离、rejected 最小回路、graph 死代码、database/PromptRegistry 接线、过度注入收敛、孤儿代码、双配置清理。

---

> 详细逐条证据见 `specs/code-audit/cluster_C1_v2.md` ~ `cluster_C8_v2.md`。
> 与 v1 对比：`findings.md`（初版）+ 8 份 `cluster_Cx.md`（初版证据）。
