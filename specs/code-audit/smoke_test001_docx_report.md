# 逐节点功能全流程测试报告 — test001（SHEIN 质检业务外包 docx）

> 在上一轮（广州铁道 PDF）确认 F3/F4 修复生效的基础上，本次用**真实 .docx 招标文件**逐节点验证每个节点的功能是否正常。

## 一、测试场景

| 项 | 值 |
|----|----|
| 输入招标文件 | `附件一：招标文件--2026年度SHEIN质检业务外包项目.docx`（劳务外包/质检外包，真实 docx） |
| 投标人 | 广州华南人力 |
| 标号 | test001 |
| 标书类型 | 劳务外包类 |
| 运行方式 | headless `run_pipeline`，无 API Key（LLM / embedding 节点 mock） |
| 注入方式 | `project_contract` + `requirements` + `extra_reqs` 模拟人工确认信息 |

## 二、总览结论

✅ **14 个节点全部 completed，0 失败节点**（FeedbackProcessor=pending 属正常）。
✅ **F3 / F4 修复在本轮 docx 上同样生效**（资质 FAIL 不再 dead-end；无 30k 截断崩溃）。
✅ **docx  ingestion 正常**（14 chunks + 7 tables）、**子类型识别正确**（detect→`BPO`）、**投标人贯通文件名**。
⚠️ 所有"空产出 / 占位内容"均源于 **mock 模式无 LLM**，非节点功能缺陷。

## 三、逐节点功能核查

| # | 节点 | 状态 | 功能核查结果 |
|---|------|------|-------------|
| 1 | DocumentParser | completed | docx 解析 **14 chunks + 7 tables**；正文存入 chunks（顶层 content 字段为空属正常，文本在 chunks 中，子类型识别已证明文本非空） |
| 2 | ReqExtractor | completed | `bid_subtype='BPO'`（质检外包→BPO 识别正确 ✅）；`scoring/qual/tech_specs=0`（mock 无 LLM；7 表未被归类为 scoring/qual 类型，故表驱动抽取 0 项）；`format_rules` 6 项已填充；`bid_number` 被覆盖（预期） |
| 3 | ContractExtractor | completed | `bidder_name='广州华南人力'` 贯通 ✅；`industry='人力资源'`（劳务外包类映射正确 ✅）；`project_name/project_code` 空（mock 无 LLM，预期）；`is_valid=False` 仅因 project_name 空（mock） |
| 4 | InfoVerificationGate | completed | `info_verification_status='confirmed'`；`info_summary` 含 10 个关键字段 ✅ |
| 5 | EligibilityChecker | completed | `verdict='PASS'`（mock 无资质可查，vacuous PASS）；路由正常（F3 修复点） |
| 6 | TemplateMatcher | completed | ⚠️ `matched_templates=[]`：mock 下 requirements 空 → query 空 → "No query text — returning empty matches"（template_matcher.py:171）。SectionGenerator 有兜底，仍产出 9 章节。**需真实 LLM+embeddings 验证模板匹配** |
| 7 | SectionGenerator | completed | **9 章节全生成** ✅；内容为 mock 占位（75–87 字/章，非真实内容，预期） |
| 8 | QualityChecker | completed | `verdict='PASS'` 正确（无实际 all_issues）；`total=1/passed=0` 为 `max(1, total_items)` 强制最小计数（quality_checker.py:653），非缺陷 |
| 9 | FeedbackProcessor | pending | 正常（max_rounds=1，无反馈回路触发） |
| 10 | CrossReferenceChecker | completed | report 结构完整（`verdict/inconsistencies/summary`），0 inconsistencies（mock 内容无跨章冲突） |
| 11 | ComplianceChecker | completed | `verdict='PASS'`，`format_issues=0 / legal_issues=0`（mock 内容无违规词） |
| 12 | ScoreSimulator | completed | 结构完整；`items=0` → `total={'predicted':0,'max':0,'rank_estimate':'无法评估'}`（mock 无 scoring 项，诚实返回） |
| 13 | HumanReviewGate | completed | `review_status='approved'`（预置） |
| 14 | DocumentAssembler | completed | 导出 `广州华南人力_标书_v1.docx`（40 段落，0 表格，mock 内容）；投标人正确落入文件名 ✅ |

**失败节点：无。**

## 四、功能性发现（按严重度）

### 🟢 无节点功能缺陷（全部节点在 mock 下行为正确）
本轮未发现任何节点崩溃、异常分支、错误路由或结构缺失。F3/F4 修复在 docx 输入下复验通过。

### 🟡 需真实环境验证的能力（mock 模式限制，非 bug）
1. **TemplateMatcher 空匹配**：根因是 mock 下 `requirements` 为空 → 查询文本为空 → 无匹配。生产环境（真实 LLM 抽取需求 + 真实 embedding/FAISS）才会触发匹配逻辑。当前无法在 mock 下验证模板匹配质量。
2. **ReqExtractor 需求抽取为 0**：mock 无 LLM，且本 docx 的 7 个表格未被 `_classify_table` 归类为 scoring/qualification 类型 → 表驱动抽取 0 项。docx 表格**已正确分类**（`doc_parser.py:325` 调用 `_classify_table`），故非 docx 解析缺陷；真实 LLM 会从正文抽取需求。
3. **正文为 mock 占位**：9 章节均为通用占位模板，未含真实投标人/标号/项目内容。内容正确性、跨章一致性、评分模拟需配真实 Key 重跑。

### 🟡 微小计数显示项（非功能缺陷，可选优化）
- `QualityChecker.passed_items=0` 而 `verdict=PASS`：`total_items` 被 `max(1, …)` 强制最小为 1（quality_checker.py:653），但无实际检查项可"通过"。建议：当真实检查项数为 0 时不强行 clamp 到 1，避免 PASS 与 passed=0 并存的观感歧义。

## 五、导出物
- `/Users/alanchris/Desktop/标书agent02/bid-agent/data/exports/广州华南人力_标书_v1.docx`（40 段落，mock 内容；注：与上一轮 PDF 运行同名，已被本轮覆盖）

## 六、建议
1. **配置真实 API Key（含 embedding）重跑**，重点验证：① docx 需求抽取（scoring/qual 是否从正文/表格抽到）；② TemplateMatcher 是否匹配到劳务外包模板；③ 9 章节正文内容质量与跨章一致性；④ ScoreSimulator 是否产出有效评分。
2. 可选：优化 `QualityChecker` 在 0 检查项时的计数展示。
3. 标号 test001 仍未进产物（同上一轮架构限制）：mock 无 LLM 抽不出 `bid_number`，且文件名 `safe_id` 仅取 `requirements.bid_number` 或 `project_contract.bidder_name`（不含 `project_code`）。需 GUI 表单字段写入 `requirements.bid_number` 方可带入。

---
*测试脚本：`bid-agent/scripts/smoke_labor_test001_docx.py`（新增，仅运行，未改任何源码）*
