# 代码审计结论 — 标书制作智能体 vs system-workflow.md (v1.0)

> 审计日期：2026-07-20 | 方法：8 簇并行逐行通读全部源码（排除 .venv），对照规范条款，证据化 file:line
> 审计范围：bid-agent 全部非测试源码（约 17,000 行）；测试文件本轮未深读
> 配套文档：`specs/code-audit/strategy.md`、`specs/code-audit/cluster_C1~C8.md`

---

## 〇、总体结论

| 维度 | 结论 |
|------|------|
| **14 节点编排（graph.py）** | ✅ **正确**。节点顺序、边、9 条条件路由、FAIL+超轮次强制通过、两阶段拆分、快照导出，与 §二/§七 完全一致（C8 已确认） |
| **AgentState 20 字段** | ✅ **齐备**（C1 确认 state.py:66-183） |
| **节点内部实现** | ⚠️ **问题集中区**。8 章生成、校验链、检索RAG、装配、GUI、一致性自进化均存在「规范有、代码缺/错」 |
| **关键风险** | 🔴 **12 项高危缺陷**，其中多项使规范能力**实际失效**（如跨章 high 级检查永不 FAIL、RAG 语义检索为空操作、模板语义匹配用 64 维随机向量） |

> 说明：C1/C2/C4 子代理最初标注「路由函数需人工确认」，但 C8 已确认 `graph.py` 完整实现全部 19 条路由且节点图与 §二 mermaid 一致，故**路由层面无问题**，所有缺陷均在节点函数内部。

---

## 一、已确认正确的关键项（避免误报）

- ① PDF(fitz+pdfminer)/DOCX(python-docx)/DOC(textutil→antiword→olefile) 三级解析与表格结构化提取（评分/资质关键词分类 + `{page,header,rows,type,source}`）✓
- ③ 四方合并优先级、字段提取、forbidden 推断、ProjectContextContract(is_valid/to_dict/from_dict) ✓
- ④ 后端 11 字段 + 两模式 + apply_user_corrections ✓；⑤ 9 类软要求自动通过 + 硬资质归一化子串匹配 ✓
- ⑦ 8 章键名、6 重+3 附加注入、并行 ThreadPoolExecutor、定向修订保留逻辑 ✓
- ⑨ 四类反馈分类 + 作用域 + 一致性自进化触发链路 ✓；⑬ 法律依据/两模式/幂等/驳回转 feedback ✓；⑫ 双模式打分 ✓
- §4.1 DeepSeek 默认 + functools.partial 注入 + 节点级模型覆盖 + 存 ~/.bid-agent/config.json ✓
- §4.3 自进化链路（Extractor→Store→SectionGenerator 注入）连通 ✓；RRF 融合公式 ✓；子类型权重三档表 ✓

---

## 二、高危缺陷（High，12 项，按节点归类）

| # | 节点 | 问题 | 证据 | 影响 |
|---|------|------|------|------|
| H1 | ⑩ CrossReference | 严重程度误判：人员/技术参数/时间节点写 `medium`（规范 high）、拼凑残留写 `high`（规范 CRITICAL）；verdict 仅 high/critical 判 FAIL → **3 类 high 检查永不触发 FAIL** | `cross_reference_checker.py:90,153,287,341,391` | 跨章不一致被静默放过，⑩→⑨ 反馈环对核心一致性失效，违反 §七 路由 |
| H2 | ⑧ QualityChecker | 「每章≥8000字」完整度检查**完全缺失**，仅有 `<50` 字阈值（且归合规度） | `quality_checker.py:103` / 规范 `:335` | 极短章节可 PASS 完整度 |
| H3 | ⑥/§4.2 检索 | 元数据过滤 `if metadata_filter: pass` 纯占位 | `pipeline.py:125-128` | RAG 结果无法按 bid_type/subtype 裁剪 |
| H4 | ⑥ TemplateMatcher | 模板库用 `MockEmbedder(dim=64)` 而非 OpenAI 1536 维 | `template_library.py:211,215` | 模板语义匹配实质失效 + 与真实 1536 维向量维度不兼容（直接报错风险） |
| H5 | ⑦ RAG | 语义检索为空操作：取得 doc_id 仅 debug 日志，注释「doc store isn't wired yet」→ 必回退关键词 | `reference_retriever.py:204-217` | 规范 ⑦「FAISS 检索历史中标方案注入」未落地 |
| H6 | ⑥ 子类型检索 | 权重表 `{subtype,shared,neighbor}` 已计算但**无任何调用方消费**，冷启动「最相似子类型」未实现 | `subtype_router.py:256-275` + `reference_retriever` | 规范⑥ 三档动态权重「算而不用」 |
| H7 | ⑭ 装配 | **连续页码缺失**（全文无页码域）；**页眉页脚自定义缺失**（不读 format_rules）；**Mermaid 用树形布局非力导向**；**目录页码硬编码"第{i}页"** | `doc_assembler.py`（全文 / `:383-404` / `:1189`） | 产出物不符合政府采购格式硬要求 |
| H8 | ④ GUI | 前端校验表单 `_FIELDS` 仅 10 字段，**漏 `bid_subtype`**（后端 11 字段含之） | `info_verification.py:18-29` vs `info_verification_gate.py:50` | 用户无法核对/修正子类型，子类型分区检索输入断链 |
| H9 | ⑦ 失败隔离 | 失败占位符 `f"【生成失败：{e}】"`（异常信息），规范要 `【生成失败：章节名 — 请手动补充】`；已算 `failed_section` 未用 | `section_generator.py:808-812` | 占位符格式错误 + 暴露内部异常 |
| H10 | ⑦ 定向修订 | 主节点 `section_generator` 跳过已有章节且**不读 feedback_history**；定向修订只在 `generate_all_sections_parallel` 实现（需确认 graph 绑定哪个） | `section_generator.py:672-679` | 若 graph 绑定主函数，规范「定向修订」在主链路失效 |
| H11 | §4.3 自进化 | CrossReference 产出的 `contract_forbidden_institution/industry`、`tech_parameter_mismatch` 三类**未映射**，全落默认 `mutual_exclusion`；且缺对应规则模板 | `extractor.py:196-205` vs `cross_reference_checker.py:267,280,334` | CRITICAL 契约偏离被错误归类，自进化经验语义错误 |
| H12 | §4.1 LLM | Anthropic/Claude 工厂不存在；`config.yaml` 的 `task_routing` 是死配置；强行启用 `create_llm("anthropic")` 抛 ValueError | `providers.py:87-93`、`config.yaml:11-14` | §4.1「质检专用 Claude」实效 |

---

## 三、中危缺陷（Medium，17 项）

| # | 节点 | 问题 | 证据 |
|---|------|------|------|
| M1 | ⑥ TemplateMatcher | 缺文件系统关键词兜底（search_fn=None 直接返回空） | `template_matcher.py:127-135` |
| M2 | ⑥ TemplateMatcher | 未消费 `bid_subtype`、未实现分区检索权重表与 `_shared` 准入 | `template_matcher.py` 全文件 |
| M3 | ⑤ Eligibility | WARNING 语义偏差：任何缺失硬资质一律 FAIL，WARNING 仅库空时产生（规范要「部分缺失有替代→WARNING 继续」） | `eligibility_checker.py:396-406` |
| M4 | ⑨ Feedback | 三层反馈历史（global_constraints/compressed_history/context_window_size=3）完全缺失，仅扁平列表 | `feedback_processor.py` 全文 |
| M5 | ⑪ Compliance | 法律合规缺「知识产权(著作权法)」「保密信息泄露(保密法)」两项 | `compliance_checker.py:111-164` |
| M6 | ⑪ Compliance | 格式合规仅声明级（仅校验 format_rules 是否声明字段），未做字体/行距/目录/页眉页脚内容级校验 | `compliance_checker.py:58-105` |
| M7 | ⑦ 评分对齐 | ch2_authorization/ch7_schedule/ch8_after_sales 模板无 `{requirements_context}` 占位符（规范「每章」） | `section_generator.py:155-164,221-230,232-241` |
| M8 | ⑭ 装配 | 页边距默认值冲突：代码 `下3.5/右2.6` vs 规范 `上下3.7/左右2.8` | `doc_assembler.py:26` / `:511` |
| M9 | ⑭ 装配 | 导出文件名用时间戳，违背契约 `{project_id}_标书_v{round}`（且 AgentState 无 project_id 字段） | `doc_assembler.py:1468` / 契约 `:11` |
| M10 | ⑭ 装配 | 占位符正则 `(【待填写[^】]*】)` 未强制冒号且允许空内容，比规范宽松 | `doc_assembler.py:1220` |
| M11 | §4.3 自进化 | quality 一致性经验大多无法注入：applicable_sections 仅当章节名原文字面出现才填，否则永不被检索命中 | `extractor.py:160,187-194` + `store.py:149-162` |
| M12 | §4.3 自进化 | 去重偏离规范：额外 `sim>=0.6 无章节重叠也合并` 分支，可能误并不同问题 | `store.py:80-81` |
| M13 | GUI 进度 | 进度组件 `ALL_NODES` 仅 13 项，缺 InfoVerificationGate | `progress.py:5-19` |
| M14 | GUI 结构 | 仅 4 Tab，规范 §一列 5 组件（信息校验为嵌入组件非独立面板） | `app.py:70-84` |
| M15 | GUI 交互 | rejected 重跑整段 Phase2（从 EligibilityChecker 重跑），非规范 §七 最小回路 ⑨→⑦ | `review_panel.py:405-418` |
| M16 | §4.1 默认 | `router.py` 默认质检模型 openai/gpt-4o-mini，与规范 DeepSeek 默认冲突（未被 pipeline_runner 采用，属误导） | `router.py:17-22` |
| M17 | ④ 来源标注 | 提取来源仅 4 类，缺规范「LLM」类别（LLM 提取字段被并入「补充说明」） | `info_verification_gate.py:71-74` + `info_verification.py:32-37` |

---

## 四、低危 / 轻微（节选）

- L1 检索距离度量用 L2 非余弦（OpenAI 向量未归一化）— `faiss_index.py:54` + `embeddings.py:86`
- L2 ReqExtractor 表格充足时仍调用 LLM（未真正「跳过」）— `req_extractor.py:420-421`
- L3 chunk_overlap 仅无换行分支生效且不可配置 — `doc_parser.py:347-363`
- L4 QualityChecker LLM 未按规范「重试 1 次」— `quality_checker.py:467-479`
- L5 ComplianceChecker「第一」用负向前行（规范示例负向后行），边界语义需确认 — `compliance_checker.py:46`
- L6 ScoreSimulator 截断 6000 vs QualityChecker 8000 不一致 — `score_simulator.py:196`
- L7 graph `route_after_score_sim` 死代码（从未调用）— `graph.py:114-121`
- L8 database.py 仅测试引用，未接入运行时 — `database.py`
- L9 PromptRegistry/PromptSelector 无任何节点调用（prompt 自进化闭环未接线）— evolution 模块

---

## 五、跨节点主题（根因）

1. **检索/RAG 整体未落地（最严重集成问题）**：模板库 64 维 Mock、RAG 语义检索未接线、权重表死代码、元数据过滤占位、L2≠余弦 —— 规范⑥/§4.2/§4.4 的语义检索能力基本停留在「框架代码」阶段，运行时实退化为关键词/随机向量。
2. **严重度/分级口径错误连锁**：⑩ severity 误判（H1）直接破坏 §七 路由；④ 前后端字段漂移（H8/M17）；⑭ 默认值与规范自相矛盾。
3. **规范内部不一致引发的实现分歧**：⑭ 页边距默认值（代码 docstring 与 system-workflow 冲突）、API Key「不落盘又落盘」自相矛盾、AgentState 无 project_id 却要求文件名含之。
4. **自进化链路脆弱**：Extractor 类型映射不全（H11）+ 经验注入匹配脆弱（M11）+ PromptRegistry 未接线（L9），自进化闭环未真正闭环。

---

## 六、仍需人工确认（无法仅凭静态代码判定）

1. `graph.py` 中 SectionGenerator 节点绑定的是 `section_generator` 还是 `generate_all_sections_parallel`？（决定 H10 是否线上生效）
2. `knowledge_base/` 物理路径在 `标书agent02/` 还是 `bid-agent/` 下？（影响 M7 表格注入与 §4.4 是否真找得到）
3. EligibilityChecker WARNING 是否刻意简化为「仅库空触发」？
4. `VectorIndexManager.search` 返回欧氏 L2 还是平方 L2？（决定 `template_matcher.py:187` 相似度换算是否双重平方）
5. API Key「不落盘」是否确为诉求（规范自相矛盾）？当前落盘实现是否可接受？
6. database.py 是否计划接入生产路径？与 config_persistence 职责边界如何划分？
7. `ch2/ch7/ch8` 是否确属「不应注入评分要求」章节（决定 M7 是否按 bug 处理）？
8. Mermaid 节点 ID 是否含中文（影响 C5「树形布局」是否触发失败回退）？

---

## 七、建议修复优先级

**P0（规范能力实际失效，先修）：**
- H1 跨章 severity 修正为规范 high/CRITICAL
- H4+H5+H6+H3 检索/RAG：接真实 OpenAI 1536 维 embedder、接线 doc store、消费权重表、实现元数据过滤
- H7 装配：页码/页眉页脚/真实目录/Mermaid 力导向
- H11 自进化类型映射补全

**P1（明显 bug / 规范偏离）：**
- H8/H9/H10 ⑦ 与 ④ GUI 字段/占位符/定向修订
- H2 ⑧ 8000 字完整度门槛
- M3/M5/M6 ⑤⑨⑪ 分级与合规检查补齐
- H12 §4.1 Anthropic 落地或清理死配置

**P2（一致性/文档）：**
- M8/M9/M10/M17 默认值与规范对齐、来源标签、project_id 字段补充
- 规范内部矛盾项先统一规范再改代码（页边距、API Key、project_id）

---

> 详细逐条证据见 `specs/code-audit/cluster_C1.md` ~ `cluster_C8.md`。
