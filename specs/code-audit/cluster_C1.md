# 簇 C1 — 审计发现

> 审计对象：`bid-agent/core/nodes/doc_parser.py`、`req_extractor.py`、`contract_extractor.py`、`core/contract.py`、`core/state.py` 与 `system-workflow.md`（§ ①/②/③、§七、§八）。
> 审计方法：逐行 Read 上述文件，按规范条款逐条核对；凡本批文件无法证实的条款均标「需人工确认」，不作臆断。

## 1. 文件清单（路径 + 行数 + 一句话职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/doc_parser.py` | 561 | DocumentParser 节点：PDF/DOCX/DOC 三级解析、表格结构化提取、文本分块 |
| `bid-agent/core/nodes/req_extractor.py` | 512 | ReqExtractor 节点：表格优先消费、LLM 提取需求、劳务外包子类型识别 |
| `bid-agent/core/nodes/contract_extractor.py` | 379 | ContractExtractor 节点：四方合并、字段提取改写、禁止项推断 |
| `bid-agent/core/contract.py` | 450 | `ProjectContextContract` 数据结构、序列化与契约校验器 |
| `bid-agent/core/state.py` | 250 | `AgentState` TypedDict 定义与 `factory_state` 初始化 |
| `system-workflow.md` | 716 | 系统工作流规范（对照基准） |

## 2. 节点实现对照表

| 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|----------|---------------------|------|------|
| 【①】PDF: PyMuPDF(fitz)+pdfminer 兜底 | `doc_parser.py:36-65` | ✓ | fitz 异常/缺失均降级到 pdfminer.six |
| 【①】DOCX: python-docx | `doc_parser.py:68-101` | ✓ | 校验 ZIP/OOXML，非 docx 抛错 |
| 【①】DOC: textutil→antiword→olefile 三级降级 | `doc_parser.py:104-203` | ✓ | 三级策略与注释一致 |
| 【①】`page.find_tables()` + 评分/资质关键词分类 | `doc_parser.py:224-286`、`209-221` | ✓ | `_classify_table` 关键词命中分类 |
| 【①】表格输出 `{page,header,rows,type,source}` | `doc_parser.py:274-281` + `:539` 补 source | ✓ | 实际为超集（另含 `row_count`/`col_count`），含全部规范字段 |
| 【①】`chunk_size=1000`/`overlap=200` | `doc_parser.py:26-27`、`332-369` | 部分 | 见缺陷 L1/L2：overlap 仅无换行分支生效且不可配置 |
| 【①】失败标记 FAILED；≥1 成功才 COMPLETED | `doc_parser.py:475`、`542-546` | ✓ | success_count>0 判 COMPLETED |
| 【①】`route_after_parser` 全部失败→END | 不在本批文件 | 需人工确认 | 路由函数应在 graph 层，本批文件未见，无法证实「→END」 |
| 【②】表格优先消费跳过 LLM | `req_extractor.py:353-359`、`453-465` | 部分 | 见缺陷 M1：表格充足时仍调用 LLM，仅以表格结果覆盖 |
| 【②】子类型三层识别 Tier1≥0.8 / Tier2≥0.7(前3000字) / Tier3 Top-3 | `req_extractor.py:479-503` 委托 `subtype_router.detect_subtype` | 需人工确认 | 阈值与 Top-3 逻辑在 `core/retrieval/subtype_router`（未审计），本批无法验证 |
| 【②】6 种子类型 | `state.py:93`（注释枚举） | ✓ | 食堂餐饮/工业生产线/HRO/保安保洁/仓储物流/BPO 与规范一致 |
| 【②】LLM 三重 JSON 容错（直接→```json```→花括号） | `req_extractor.py:88-118` | ✓ | 三层提取顺序与规范一致 |
| 【②】重试 1 次 | `req_extractor.py:420-426` | ✓ | 首次 None 且 llm_fn 存在时重试一次 |
| 【②】`route_after_extraction` FAILED→END | 不在本批文件 | 需人工确认 | 同路由函数，需核对 graph 层 |
| 【③】四方合并 用户确认>招标文件>LLM补充>默认 | `contract_extractor.py:180-273` | ✓ | `_confirmed_or_fallback` 实现「显式清空不回退」语义 |
| 【③】字段提取 bidder_name/project_location/industry/duration/warranty/service_target | `contract_extractor.py:44-53`、`236-265` | ✓ | prompt 与合并字段齐备 |
| 【③】forbidden_institutions/forbidden_industries 推断 | `contract.py:114-134`、`contract_extractor.py:270-271` | ✓ | `infer_forbidden()` 在存在 project_name/bidder_name 时调用 |
| 【③】`ProjectContextContract` is_valid/to_dict/from_dict | `contract.py:64-80` | ✓ | 三者均实现 |
| 【⑧】AgentState 核心字段 20 项全部存在 | `state.py:66-183` | ✓ | 见 §4 详列，全部存在无缺失 |

## 3. 错误/缺陷明细（按高/中/低）

### 高（无）
本次审计未在 5 个文件中发现会导致崩溃或数据错乱的高危缺陷。

### 中
- **M1 — ReqExtractor 未真正「跳过 LLM 调用」（与【②】成本意图偏离）**
  `req_extractor.py:420-421` 在存在文档文本时**总是**调用 LLM；即便评分/资质表已由 `extracted_tables` 完整覆盖，也仅在 `:453-465` 以表格结果覆盖 LLM 输出。规范【②】第 1 条明确「直接从 JSON 解析…跳过 LLM 调用」，意图是省去 LLM 成本，但代码仍发起 LLM 请求（仅结果被覆盖）。功能输出正确，但未达到「跳过」的优化目标。
  - 证据：`req_extractor.py:420-421`（无条件调用）、`:453-465`（覆盖而非跳过）。

- **M2 — 路由函数 `route_after_parser` / `route_after_extraction` 无法在本批文件证实**
  规范 §七 要求「全部失败→END」「FAILED→END」，但这两个路由函数不在本次审计的 5 个文件中（应在 `core/graph` 或路由模块）。节点内部 `document_parser` 仅设置 `node_status=FAILED/COMPLETED`（`doc_parser.py:542-546`），到 END 的路由不可见。
  - 证据：`doc_parser.py:542-546`（仅置状态）、规范 §七 `:669-672`。
  - 结论：**需人工确认** graph 层是否实现并正确连接这两个路由。

### 低
- **L1 — `chunk_overlap=200` 未按字面严格生效**
  `doc_parser.py:347-363` 的分块在「找到换行符」分支（`nl_pos!= -1`）以换行切分并 `start=nl_pos+1`，此时相邻块**无重叠**；仅「无合适换行」分支才 `start=end-chunk_overlap`。因此重叠量随文本换行分布浮动，并非固定 200 字符重叠。
  - 证据：`doc_parser.py:353-363`。

- **L2 — overlap 不可配置，chunk_size 无 500-2000 校验**
  规范【①】称 chunk_size「可由上传面板配置 500-2000」；`document_parser` 读取 `state.get("chunk_size", CHUNK_SIZE)`（`doc_parser.py:530`）但未做范围校验，且 `CHUNK_OVERLAP=200` 为硬编码常量（`doc_parser.py:27`），未暴露为可配置项。
  - 证据：`doc_parser.py:26-27`、`530`。

- **L3 — 空 documents 时 `document_parser` 返回 COMPLETED 而非 FAILED**
  `doc_parser.py:517-523`：当 `documents` 为空，直接返回 `DocumentParser: COMPLETED`，不经 `success_count` 判定。规范【①】「至少 1 个文件成功才标记 COMPLETED」；空输入属于边界，可能使 `route_after_parser` 判 SUCCESS 而进入下游空数据流程。
  - 证据：`doc_parser.py:517-523`。

- **L4 — `project_code` 合并与 `project_name` 语义不完全一致（低风险）**
  `contract_extractor.py:226-230` 用裸 `confirmed["bid_number"] if "bid_number" in confirmed` 直接取值，而其余字段用 `_confirmed_or_fallback` 处理「显式清空 vs 缺失」区分。当 `bid_number` 键存在但为空串时返回 `""` 且不回退到 `req_bid_number`，与 `project_name` 的回退逻辑不完全对称（功能上仍属「用户显式清空」语义，风险低）。
  - 证据：`contract_extractor.py:221-230` vs `:236-244`。

## 4. 遗漏功能（规范有、代码无）

- **本批文件未覆盖的节点/路由**：规范 §七 的 `route_after_parser`、`route_after_extraction`（以及 §七 其余路由）不在本次审计的 5 个文件中，无法判定是否实现。属「需人工确认」，不记为代码缺失（大概率在 graph 层）。
- **子类型三层阈值与 Top-3 逻辑**：规范【②】的 Tier1 命中率≥0.8、Tier2 confidence≥0.7、Tier3 返回 Top-3 的具体实现不在此次审计文件内，被 `req_extractor.py:484,494` 委托给 `core.retrieval.subtype_router.detect_subtype`，需审计该模块确认。
- **其余核对项（PDF/DOCX/DOC 解析、表格分类、`{page,header,rows,type,source}` 输出、三重 JSON 容错+重试、四方合并、禁止项推断、契约序列化、§八 全部状态字段）均已在代码中找到对应实现，无功能遗漏。**

## 5. 跨节点/横切问题

- **子类型链路跨文件依赖**：`req_extractor` 仅在 `bid_type` 含「劳务外包」/「劳务管理服务」时触发（`req_extractor.py:482`），并依赖 `core.retrieval.subtype_router` 与 `core.graph.get_pipeline_llm`（`req_extractor.py:484-491`）。该依赖为运行时导入并包裹在 try/except 中，若 `subtype_router` 缺失仅告警而不崩溃——但会导致【②】子类型识别功能静默失效。需结合 `subtype_router` 模块一并审计。
- **状态字段一致性**：`state.py` 已包含 §八 全部 20 字段，且 `extracted_tables` 的 `source` 字段在 `doc_parser.py:539` 补注，与 `state.py:80` 注释 `{page, header, rows, type, source}` 一致。各节点写入的字段名（`requirements`/`project_contract`/`bid_subtype` 等）与 `state.py` 定义一致。
- **`route_after_parser`/`route_after_extraction` 缺失验证**：跨节点路由决定「失败→END」终止行为，若 graph 层未实现，将破坏规范 §七 的失败终止语义（见 M2）。

## 6. 需人工确认项

1. **`route_after_parser` / `route_after_extraction` 是否存在且正确路由到 END**（规范 §七 `system-workflow.md:669-672`）——应核对 `core/graph.py` 或相关路由模块。
2. **`core/retrieval/subtype_router.detect_subtype` 是否实现三层识别及阈值**：Tier1 关键词命中率≥0.8、Tier2 前3000字 + confidence≥0.7、Tier3 返回 Top-3——核对 `subtype_router` 模块（规范【②】`system-workflow.md:132-138`）。
3. **`user_confirmed_fields` 的字段命名**：`contract_extractor.py:226-230` 同时识别 `bid_number` 与 `project_code`，需确认 `InfoVerificationGate`/`apply_user_corrections` 写入的键名是否与之匹配（避免合并失效）。
4. **空 documents 边界**：`doc_parser.py:517-523` 空输入返回 COMPLETED，需确认上游是否在调用前保证 documents 非空，或 graph 路由是否容忍该状态。
