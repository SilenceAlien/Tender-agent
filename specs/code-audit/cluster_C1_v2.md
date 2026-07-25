# 簇 C1 复审计 — 审计发现 (v2)

> 复审计对象：`bid-agent/core/nodes/doc_parser.py`、`req_extractor.py`、`contract_extractor.py`、`core/contract.py`、`core/state.py` 与 `system-workflow.md`（§ ①/②/③、§七、§八）。
> 方法：逐行 Read 五个文件 + 规范，对照上一轮（C1）结论逐项核实修复，并查找回归/新问题。
> 重要前提：当前 `req_extractor.py:420-426` 与上一轮证据行号**完全一致**，强烈表明该文件未被实质改动——上一轮标记的"表格充足仍调 LLM"问题在代码层面仍然存在。

## 1. 文件清单（路径 + 行数 + 职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/doc_parser.py` | 561 | DocumentParser：PDF/DOCX/DOC 三级解析、表格结构化提取、文本分块 |
| `bid-agent/core/nodes/req_extractor.py` | 512 | ReqExtractor：表格优先消费、LLM 需求提取、劳务外包子类型识别 |
| `bid-agent/core/nodes/contract_extractor.py` | 379 | ContractExtractor：四方合并、字段提取改写、禁止项推断 |
| `bid-agent/core/contract.py` | 450 | `ProjectContextContract` 数据结构、序列化与契约校验器 |
| `bid-agent/core/state.py` | 256 | `AgentState` TypedDict 定义与 `factory_state` 初始化 |
| `system-workflow.md` | 716 | 系统工作流规范（对照基准） |

## 2. 上一轮问题核对表

| 旧问题 | 状态 | 证据 file:line | 说明 |
|--------|------|----------------|------|
| **ReqExtractor 表格充足时仍调用 LLM，未真正"跳过"**（任务称低危 L2；原 `cluster_C1.md` 记 M1 中危；原证据 `req_extractor.py:420-421`） | **未修复** | `req_extractor.py:421`（无条件 `raw = _call_llm_for_extraction`） | 当前文件行号与上一轮证据完全一致，该文件未见实质修改。表格结果仅在 `:454-465` 以"覆盖"方式使用，LLM 调用仍无条件发起；只有"无文本"场景（`:387-402`）才真正跳过。规范【②】第 1 条"跳过 LLM 调用"的优化目标未达成。 |
| chunk_overlap=200 未按字面严格生效（原 L1） | 未修复 | `doc_parser.py:347-363` | "找到换行"分支以换行切分、`start=nl_pos+1`，相邻块无重叠；仅无换行分支才 `start=end-chunk_overlap`。行为同上一轮。 |
| overlap 不可配置、chunk_size 无 500-2000 校验（原 L2） | 未修复 | `doc_parser.py:26-27`、`530` | `CHUNK_OVERLAP=200` 硬编码；`document_parser` 取 `state.get("chunk_size")` 但无范围校验。 |
| 空 documents 时返回 COMPLETED 而非 FAILED（原 L3） | 未修复 | `doc_parser.py:517-523` | 空输入直接返回 `DocumentParser: COMPLETED`，绕开 `success_count` 判定。 |
| project_code 合并与 project_name 语义不对称（原 L4） | **非缺陷（重新评估）** | `contract_extractor.py:221-230` | 复读发现：内联写法 `confirmed["bid_number"] if "bid_number" in confirmed` 与 `_confirmed_or_fallback` 的"键存在即返回（含空串）"语义**一致**，原 L4 为误判，撤销。 |
| route_after_parser / route_after_extraction 不在本批文件，无法证实→END（原 M2） | 仍 **需人工确认** | 规范 §七 `:669-672` | 路由函数在 graph 层，本批未改、未读；节点 `node_status` 置位（`:542-546`、req `:381/447`）与路由语义吻合。 |

## 3. 节点实现对照表

| 规范条款 | 代码位置 | 状态 | 说明 |
|----------|----------|------|------|
| 【①】PDF: fitz + pdfminer 兜底 | `doc_parser.py:36-65` | ✓ | 缺失/异常均降级 |
| 【①】DOCX: python-docx + 非 docx 抛错 | `doc_parser.py:68-101`、`:444-459` | ✓ | 非 docx 回退 `_load_doc` |
| 【①】DOC: textutil→antiword→olefile | `doc_parser.py:104-203` | ✓ | 三级降级 |
| 【①】`find_tables()` + 评分/资质关键词分类 | `doc_parser.py:224-286`、`209-221` | ✓ | 关键词命中分类 |
| 【①】表格输出含 `page/header/rows/type/source` | `doc_parser.py:274-281`、`:539` | ✓ | 含全部规范字段（超集含 row_count/col_count） |
| 【①】chunk_size=1000/overlap=200 | `doc_parser.py:26-27`、`332-369` | 部分 | 见 §2 L1/L2：重叠浮动、不可配置 |
| 【①】失败标记 FAILED；≥1 成功才 COMPLETED | `doc_parser.py:475`、`542-546` | ✓ | success_count>0 判 COMPLETED |
| 【②】表格优先消费**跳过 LLM** | `req_extractor.py:387-402`(仅无文本跳过)、`421` | **错误/未达标** | 仅无文本场景跳过；有文本时 LLM 始终调用（`:421`），仅以表格覆盖（`:454-465`） |
| 【②】子类型三层识别（Tier1≥0.8/Tier2≥0.7/Tier3 Top-3） | `req_extractor.py:479-503` 委托 `subtype_router` | 需人工确认 | 阈值逻辑在 `core/retrieval/subtype_router`（未审计） |
| 【②】6 种子类型枚举 | `state.py:91-95` | ✓ | 与规范一致 |
| 【②】LLM 三重 JSON 容错 | `req_extractor.py:88-118` | ✓ | 顺序与规范一致 |
| 【②】重试 1 次 | `req_extractor.py:420-426` | ✓ | 首次 None 且 llm_fn 存在时重试 |
| 【③】四方合并 用户确认>招标文件>LLM>默认 | `contract_extractor.py:180-273` | ✓ | `_confirmed_or_fallback` 实现"显式清空不回退" |
| 【③】字段提取 bidder_name/location/industry/duration/warranty/service_target | `contract_extractor.py:44-53`、`236-265` | ✓ | 齐备 |
| 【③】forbidden_institutions/industries 推断 | `contract.py:114-134`、`contract_extractor.py:270-271` | 部分 | 见 §4 F3：仅在 `project_name or bidder_name` 时推断 |
| 【③】`ProjectContextContract` is_valid/to_dict/from_dict | `contract.py:64-90` | ✓ | 三者均实现 |
| 【⑧】AgentState 20 核心字段全部存在 | `state.py:66-183` | ✓ | 20 项无缺失（另含 extra_reqs 等扩展字段，合理） |

## 4. 错误/缺陷明细

### 高（无）
未发现崩溃级/数据错乱级缺陷。

### 中
- **M1 — ReqExtractor 仍未真正"跳过 LLM 调用"（与【②】成本意图偏离，上一轮遗留）**
  `req_extractor.py:421` 在存在文档文本时**总是**调用 LLM；即便评分/资质表已由 `extracted_tables` 完整覆盖，也仅在 `:454-465` 以表格结果覆盖 LLM 输出。规范【②】第 1 条明确"直接从 JSON 解析…跳过 LLM 调用"，意图省去 LLM 成本，但代码仍发起 LLM 请求。当前文件行号与上一轮证据完全一致，表明该修复未落实。`tech_specs`/`format_rules` 仍需 LLM 属合理，但"表格充足即跳过整个 LLM 调用"未实现。
  - 证据：`req_extractor.py:421`（无条件调用）、`:454-465`（覆盖而非跳过）、`:387-402`（仅无文本时跳过）。

### 低
- **L1 — 早期返回 / 无文本路径未应用 `format_rules` 默认值（回归/遗漏，上一轮未报）**
  `req_extractor.py:388-397` 与 `:396` 在"仅表格、无文本"分支构建 `requirements` 时直接写 `format_rules: {}`，**绕过了** `_validate_requirements` 中 `DEFAULT_FORMAT_RULES`（`req_extractor.py:124-135`）的默认值合并。下游 `ComplianceChecker` 消费 `requirements.format_rules`，空字典将导致页边距/字体/行距等格式合规检查失去默认值依据，可能误报。应改为走 `_validate_requirements` 或显式填充 `DEFAULT_FORMAT_RULES`。
  - 证据：`req_extractor.py:388-397`（尤其 `:396` `format_rules: {}`）。

- **L2 — 子类型识别在 `llm_fn=None` 时仍可能触发真实 LLM（不一致，上一轮未报）**
  `req_extractor.py:489-493`：主提取在 `llm_fn is None` 时使用空 mock（`:205-217`），但子类型识别分支 `if llm_fn is None: subtype_llm = get_pipeline_llm()`（`:489-491`）会取**真实管道 LLM**。这意味着测试/无头模式下，主流程走 mock，子类型分支却可能发起真实 API 调用，行为不一致且可能污染测试。是否实际触发取决于 `get_pipeline_llm()` 在无 LLM 配置下返回值。
  - 证据：`req_extractor.py:205-217`（主流程 mock）vs `:489-493`（子类型取真实 LLM）。**需人工确认** `get_pipeline_llm()` 在测试上下文的返回值。

- **L3 — `contract_extractor.py:9` 注释写"三方合并"，与规范/函数名"四方合并"不一致（文档缺陷）**
  模块 docstring 第 9 行写"三方合并: 表单字段 > 招标文件提取 > LLM提取"，但函数 `_merge_contract_sources` 注释与规范均为"四方"（含默认/空）。实际合并逻辑为四方，仅为注释笔误。
  - 证据：`contract_extractor.py:9` vs `:180`、`:186`、规范 §③ `:155-159`。

- **L4 — `infer_forbidden` 仅在 `project_name or bidder_name` 非空时才推断禁止项（边界弱化，上一轮记 ✓，补充限制）**
  `contract_extractor.py:270`：`if contract.project_name or contract.bidder_name: contract.infer_forbidden()`。若招标文件仅提取出 `tenderer_name`（招标人）而 `project_name`/`bidder_name` 为空，`forbidden_institutions`/`forbidden_industries` 不被推断，拼凑残留检测被削弱。属边缘场景，按设计可接受但需知晓。
  - 证据：`contract_extractor.py:270`、`contract.py:114-123`。

- **L5 — `forbidden_industries` 推断过宽（设计需确认，上一轮未报）**
  `contract.py:125-134`：`forbidden_industries` = 全部 7 个标准行业 **减去** 契约自身行业。即任何章节提及"其他行业"关键词即判 CRITICAL（`contract.py:335-346`）。当 `industry="人力资源"` 时，章节出现"物业服务/化学研发"等词汇即触发 forbidden_industry。在混合服务场景中可能误报，需确认是否为预期策略。
  - 证据：`contract.py:125-134`、`335-346`。**需人工确认**。

- **L6/L7 — 上一轮 L1/L3 遗留**：`chunk_overlap` 浮动（`doc_parser.py:347-363`）、空 documents 返回 COMPLETED（`doc_parser.py:517-523`），均未修复，见 §2。

## 5. 遗漏功能

- 路由函数 `route_after_parser`/`route_after_extraction`（规范 §七 `:669-672`）不在本批 5 文件内，无法证实"→END"；节点 `node_status` 置位逻辑吻合，但路由连接**需人工确认**（同上一轮 M2）。
- 子类型三层阈值与 Top-3（`core/retrieval/subtype_router.detect_subtype`）不在本批文件，实现正确性**需人工确认**。
- 其余规范条款（PDF/DOCX/DOC 三级解析、表格分类与 `{page,header,rows,type,source}` 输出、三重 JSON 容错+重试、四方合并、契约序列化、§八 全部 20 状态字段）均已找到对应实现，无功能遗漏。

## 6. 跨节点/横切问题

- **子类型链路运行时依赖**：`req_extractor` 仅在 `bid_type` 含"劳务外包"/"劳务管理服务"触发（`req_extractor.py:482`），依赖 `core.retrieval.subtype_router` 与 `core.graph.get_pipeline_llm`（`:484-491`）。导入在 try/except 内，缺失仅告警不崩溃，但会使【②】子类型识别静默失效；且 `llm_fn=None` 时改用真实 LLM（见 L2）带来跨节点不一致。
- **状态字段一致性**：`state.py` 含 §八 全部 20 字段，`extracted_tables` 的 `source` 在 `doc_parser.py:539` 补注，与 `state.py:80` 注释一致；各节点写入键名与 `state.py` 定义一致。无横切不一致。
- **格式默认值跨节点断裂**：`req_extractor` 的 `_validate_requirements` 提供 `DEFAULT_FORMAT_RULES`，但早期返回路径（L1）与"无文本"路径绕过它，使 `format_rules` 默认值的可用性依赖具体返回分支——属跨分支一致性问题。

## 7. 需人工确认项

1. **`route_after_parser` / `route_after_extraction` 是否实现并正确路由到 END**（规范 §七 `:669-672`）——核对 `core/graph.py` 或路由模块。
2. **`core/retrieval/subtype_router.detect_subtype` 三层识别与阈值**（Tier1≥0.8 / Tier2 前3000字+confidence≥0.7 / Tier3 Top-3）——核对该模块。
3. **`get_pipeline_llm()` 在 `llm_fn=None`/测试上下文的返回值**（关联 L2）——若返回真实客户端，则 `req_extractor` 子类型分支会发起未预期的真实 LLM 调用。
4. **`forbidden_industries` 过宽策略是否为预期**（关联 L5）——确认混合行业场景下的误报容忍度。
5. **空 documents 边界**（关联 L6）——确认上游是否在调用前保证 `documents` 非空，或 graph 路由是否容忍 DocumentParser 空输入返回 COMPLETED。
6. **L1 格式默认值**：确认早期返回路径（`:388-397`）是否应填充 `DEFAULT_FORMAT_RULES`，避免下游格式合规检查失去默认值。
