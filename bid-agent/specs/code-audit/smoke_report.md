# 端到端冒烟测试报告 — bid-agent

> 测试专家（端测测）执行。范围：**只读排查 + 运行，不动手修复**。
> 生成时间：2026-07-20 | 环境：macOS, Python 3.13.12 (bid-agent/.venv)

## 一、测试范围与方法

### 1.1 权威集成测试套件（pytest）
- 完整运行 `tests/integration`（20 个文件）：**227 passed / 3 failed**
- 重点 E2E：`tests/integration/test_pipeline_e2e.py` → **8/8 passed**

### 1.2 真实招标文件冒烟（自写 harness `scripts/smoke_real_doc.py`）
将两份**真实招标文件 PDF** 直接喂进 `run_pipeline`（headless），观察 14 节点状态、解析表数、生成章节数、导出文件：
- Doc1：`/…/2026年广州铁道车辆有限公司劳务管理服务采购（技术标）v2.pdf`（96 页）
- Doc2：`/…/knowledge_base/劳务外包类/招标文件/厦门税务局食堂外包_招标文件.pdf`（114 页）

### 1.3 ⚠️ 环境关键限制（影响结论边界）
- **环境变量无 API Key**（OPENAI/DEEPSEEK/ANTHROPIC 均空）→ 所有 LLM 节点使用 **mock（返回 None）**。
- 因此：**章节内容生成质量未被验证**（无真实 LLM，mock 下只产出结构壳）。本次冒烟验证的是：节点连通性、编排路由、解析/抽取、资质路由、装配输出，**不验证生成文本正确性**。
- 要验证真实内容生成，需配置有效 DeepSeek/OpenAI Key 后重跑。

## 二、已确认通过（无 bug）

| 项 | 证据 |
|----|------|
| E2E 全链路（mock PDF） | `test_pipeline_e2e.py` 8/8：解析→提取→匹配→生成→校验→装配→导出全绿 |
| 14 节点图构建 + 19 条路由 | `test_two_phase_pipeline.py` / `test_graph.py` 通过 |
| 表格提取函数本身 | 直接调用 `_extract_tables_pdf`：厦门 PDF **48 张**、广州 PDF **14 张**全部正确提取 |
| 文档装配格式（页边距 3.7/2.8、行距 28pt、目录、签章、占位符红粗） | `test_doc_assembler.py` 相关用例通过 |
| Doc1 完整跑通 | 14 节点全 COMPLETED，9 章节，导出 docx 成功 |

## 三、发现的 Bug（按严重度）

### 🔴 F3 — 资质 FAIL 导致整条流水线静默死路（HIGH，最严重）
- **位置**：`core/graph.py:191-197`（`route_after_eligibility`）
- **现象**：`EligibilityChecker` 返回 `FAIL` 时，路由直接 `return "__end__"`，TemplateMatcher 及下游全部**不执行**，流水线在无异常、零输出下结束。
- **冒烟证据**：Doc2 资质缺失 17 项 → verdict=`FAIL` → **sections=0，export_path=False，下游节点全 `pending`**；仅日志一行 `EligibilityChecker FAIL … Terminating pipeline.` 作为唯一信号。
- **影响**：真实招标文件的企业资质（`company_quals.json`）几乎不可能 100% 覆盖招标硬要求，尤其无 LLM 填充时必然 FAIL。**headless 模式下任何真实标书都会零产出、且无用户可见报错**——这是一个"能跑但永远不出东西"的静默死路。
- **规范关系**：节点 ⑤ 原文写"FAIL→终止"，故代码**符合规范字面**；但实践上对真实输入不可用，且失败信号未上抛给用户。建议：（a）FAIL 时仍产出"草稿版"标书并显式标注未过资质；（b）或在 `run_pipeline` 返回态中置 `eligibility_report` 并给出用户可见提示，而非静默 `__end__`。

### 🔴 F4 — ReqExtractor 硬性截断 30,000 字，真实标书丢 2/3 需求（HIGH，数据丢失）
- **位置**：`core/nodes/req_extractor.py:191`（`MAX_INPUT_CHARS = 30000`）、`:409-417`
- **现象**：合并后的输入超 30,000 字即**硬截断**（在最近的 `\n` 处切，无则硬切）。
- **冒烟证据**：Doc1 日志 `ReqExtractor: input 94324 chars exceeds 30000 — truncating to avoid OOM` → 真实 94k 字标书仅前 30k 进入抽取，**约 68% 文本（含后置的评分标准/资质要求）被静默丢弃**。代码注释（:186-188）自己承认"truncation caused loss of scoring/qualification info from later windows"。
- **影响**：即便 Doc1 走了 PASS 路径，其需求抽取也是基于被截掉 2/3 的半成品，下游匹配/生成质量必然受损。与规范"chunk_size=1000/overlap=200 分块处理全文"的设计意图相矛盾（设计是分块吃全文档，而非拼成一团后砍 30k）。
- **建议**：改为按 chunk 分批抽取并合并，而非单 blob 截断；或提升上限 + 保证评分/资质段优先保留。

### 🟡 F1 — 导出文件名回退串重复"标书"（LOW，真实 cosmetic bug）+ 测试过时
- **位置**：`core/nodes/doc_assembler.py` 文件名契约（M9/N8 修复处）
- **现象**：`AgentState` 无 `project_id`、且 `bidder_name` 等为空时，回退串拼接成 **`标书_标书_v1.docx`**（"标书"重复）。
- **测试证据**：`tests/integration/test_doc_assembler.py:325` `assert 'v0' in '标书_标书_v1.docx'` 失败。
- **说明**：
  - "标书_标书"重复是**真实缺陷**，回退应得 `标书_v1.docx` 或用源文件名词干。
  - `v0`→`v1` 是 M9/N8 修复的**预期行为**（避免 v0），该断言本身已过时；测试需更新为 `'v1'`。
  - 二者叠加导致该用例失败。

### 🟡 F2 — Markdown 导出目录 2 用例失败（**测试过时，非产品 bug**）
- **位置**：`tests/integration/test_markdown_export.py:584`（`test_toc_uses_tab_not_dots`）、`:614`（`test_toc_has_tab_stops`）
- **根因**：H7 修复把目录页码从字面"第X页"文本改为 **PAGEREF 字段**（真实页码，符合规范）。`doc_assembler.py:1265-1275` **已正确**添加右对齐 tab stop + 点引线 + PAGEREF。但这两个旧测试用 `"页" in p.text` 启发式找目录条目——字段不含字面"页"，故检测不到 → 断言失败。
- **结论**：**产品行为正确（真实页码、tab 制表位、点引线均到位），是测试断言未随 H7 同步更新**。需改测试断言（按 tab stop / PAGEREF 字段判定），而非改产品。
- ⚠️ 注意：PAGEREF 字段的页码需 Word 打开/打印时更新才显示；若被不更新字段的工具消费，页码可能显示为 0/空——这是 PAGEREF 方案的固有局限，非本次 bug。

## 四、撤回的误报（重要）
- ❌ **"真实 PDF `tables=0`"**：初版冒烟脚本读错字段（`d.get('extracted_tables')` 是 per-doc 未设键，真实数据在 `d['tables']` 与顶层 `extracted_tables`）。修正后确认广州 14 张、厦门 48 张表全部正确提取。**非 bug，撤回**。

## 五、结论与建议优先级

| 优先级 | 项 | 处置 |
|--------|----|------|
| P0 | **F3** 资质 FAIL 静默死路 | 改路由/产物：FAIL 仍出草稿 + 用户可见提示；或至少 `run_pipeline` 显式返回 eligibility 失败 |
| P0 | **F4** 30k 硬截断丢需求 | 改成分块抽取合并，去掉单 blob 截断 |
| P1 | **F1** 文件名"标书_标书"重复 | 修正回退串；同步更新 `test_filename_includes_round` 断言(v0→v1) |
| P2 | **F2** TOC 测试过时 | 仅更新 2 个测试断言（产品无需改） |

**整体质量判断**：节点连通性、编排路由、解析/表格提取、装配格式均健康（集成套件 227 通过）。**真正的阻塞点是 F3/F4 两个"对真实输入不可用"的缺陷**——它们只在真实招标文件 + 无 LLM/无预填资质时暴露，mock 小 PDF 的 E2E 测试完全盖不住。建议 P0 两项修复后，再配真实 API Key 跑一次内容生成验证。

> 注：本次未修改任何源码，仅新增 `scripts/smoke_real_doc.py` 作为冒烟 harness（非修复）。
