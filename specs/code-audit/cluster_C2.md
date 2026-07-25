# 簇 C2 — 审计发现

> 审计对象：节点④ InfoVerificationGate、⑤ EligibilityChecker、⑥ TemplateMatcher 及其 GUI 组件
> 对照规范：`system-workflow.md`（v1.0，2026-07-14）
> 审计方式：逐行读取 4 个源码文件 + 规范对应章节

## 1. 文件清单（路径+行数+职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/info_verification_gate.py` | 355 | 节点④：提取后人工校验门；构建 11 字段摘要、来源标注、两种模式（pending/auto_confirm）、`apply_user_corrections()` 注入修正 |
| `bid-agent/gui/components/info_verification.py` | 146 | 节点④ GUI 组件：渲染可编辑校验表单、来源标签、必填校验、确认按钮 |
| `bid-agent/core/nodes/eligibility_checker.py` | 427 | 节点⑤：资质门槛 Go/No-Go；9 类软要求过滤 + 硬资质归一化子串匹配；返回 verdict |
| `bid-agent/core/nodes/template_matcher.py` | 213 | 节点⑥：模板匹配；FAISS 语义检索 + bid_type 精确过滤 + 相似度阈值 Top-3 |

## 2. 节点实现对照表

### 节点④ InfoVerificationGate

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 11 字段可编辑表单 | `info_verification_gate.py:38-51` | ✓ | 后端 `_VERIFICATION_FIELDS` 含 11 字段（含 `bid_subtype`） |
| 11 字段表单（GUI） | `info_verification.py:18-29` | **缺失** | GUI `_FIELDS` 仅 10 字段，**缺 `bid_subtype`（子类型）**，与后端不一致 |
| 每字段标注提取来源（5 类：招标文件/补充说明/LLM/用户填写/缺失） | `info_verification_gate.py:71-74`、`info_verification.py:32-37` | **错误** | 仅返回 4 类（用户填写/招标文件/补充说明/缺失），**无 "LLM" 来源**；docstring 写"推断"但代码从不返回该标签 |
| 前门+后门双重校验定位 | `info_verification_gate.py:1-24` | ✓ | 注释明确与末端 HumanReviewGate 形成双校验（架构层面） |
| 冷启动状态提示 | `info_verification_gate.py:171-186` | ✓（后端） | 后端计算 `subtype_status/hint/chunk_count/nearest_subtypes`；但 GUI 未渲染 |
| 交互模式 pending→暂停 | `info_verification_gate.py:256-279` | ✓ | 设 `pending` + `NodeStatus.RUNNING` |
| 无头模式 auto_confirm=True 自动通过 | `info_verification_gate.py:229-239` | ✓ | |
| apply_user_corrections() 注入修正值 | `info_verification_gate.py:282-354` | ✓ | 重新合并契约、写 `user_confirmed_fields`、重建 `global_constraints` |

### 节点⑤ EligibilityChecker

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 9 类软要求自动通过 | `eligibility_checker.py:51-213` | ✓ | 9 类全部实现（通用法定/业绩/文档流程/状态声明/通用能力/财务/法人证件/兜底提交/发票） |
| 硬资质归一化去除后缀 | `eligibility_checker.py:273-284` | **错误(低)** | 仅单次去除一个后缀（循环条件 `len(text) > len(suffix) + 2`），嵌套后缀如"ISO9001认证证书"只去掉"证书"残留"认证" |
| 归一化子串模糊匹配 | `eligibility_checker.py:287-298` | ✓ | `req in avail or avail in req` |
| 企业资质库 data/company_quals.json | `eligibility_checker.py:243` | ✓ | 路径解析正确（bid-agent/data/company_quals.json） |
| 门禁路由 FAIL→终止/WARNING→继续/PASS→TemplateMatcher | 节点返回 verdict `eligibility_checker.py:347-426` | ✓（需人工确认路由函数） | 节点返回正确；`route_after_eligibility` 不在本文件，见 §5/§6 |
| WARNING=部分缺失有替代继续 | `eligibility_checker.py:396-406` | **错误/需确认** | 代码对**任何缺失硬资质一律 FAIL**；WARNING 仅在"资质库为空"时产生，未实现"部分缺失有替代→WARNING"语义 |

### 节点⑥ TemplateMatcher

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 双模式检索（FAISS 语义 + 文件系统关键词兜底） | `template_matcher.py:110-135` | **缺失** | 仅 FAISS `search_fn`；`search_fn is None` 时直接返回空列表，**无文件系统关键词兜底** |
| 子类型分区检索权重表（冷启动0/不足1-4/正常≥5） | `template_matcher.py`（全文件） | **缺失** | 无子类型分区、`_shared`/最近邻权重逻辑；仅按 `bid_type` 顶层类型过滤 |
| 输入含 bid_subtype 并分区检索 | `template_matcher.py:110-212` | **缺失** | 全程未读取 `bid_subtype`，分区检索未实现 |
| _shared 严格准入（仅格式固定型章节） | `template_matcher.py`（全文件） | **缺失** | 未在节点内实现（属检索层架构，但节点未消费） |
| 输出 matched_templates Top-3 + selected_template_id | `template_matcher.py:192-207` | ✓ | `[:3]` + 写 `selected_template_id` |
| 相似度阈值 0.3 | `template_matcher.py:23,188` | ✓ | |
| 相似度换算（FAISS L2→cosine） | `template_matcher.py:187` | **错误/需确认** | `1 - distance²/2` 假设 `distance` 为欧氏 L2；若 `VectorIndexManager.search` 返回**平方 L2**（FAISS 默认），则二次平方导致相似度计算错误 |

## 3. 错误/缺陷明细（高/中/低）

- **【高】GUI 漏渲染"子类型"字段** — `info_verification.py:18-29` 的 `_FIELDS` 缺少 `bid_subtype`，而规范④要求 11 字段且子类型可由用户确认（ReqExtractor Tier3）。后果：用户无法在前端核对/修正子类型，`apply_user_corrections` 也收不到子类型修正值，子类型分区检索输入缺失。后端 `_VERIFICATION_FIELDS`（`info_verification_gate.py:50`）已含该字段，两端不一致。

- **【中】来源标注缺 "LLM" 类别** — `info_verification_gate.py:71-74` 与 `info_verification.py:32-37` 仅 4 类来源，缺少规范的 "LLM"。LLM 提取字段被归为"补充说明"，无法区分来源，影响可解释性与审计追溯。

- **【中】TemplateMatcher 缺文件系统关键词兜底** — `template_matcher.py:127-135`：`search_fn is None` 直接返回空匹配。规范⑥及 §6 LLM 依赖汇总明确 Fallback=文件系统关键词检索，当前缺失，FAISS 不可用时检索完全失效。

- **【中】TemplateMatcher 未消费 bid_subtype / 无分区权重表** — `template_matcher.py:110-212`：规范⑥"子类型分区检索权重表（冷启动 0/不足 1-4/正常≥5）"与 `_shared` 准入完全未实现，检索退化为全局 FAISS + `bid_type` 精确过滤，子类型差异化检索未落地。

- **【中/需确认】WARNING 语义与规范不符** — `eligibility_checker.py:396-406`：任何缺失硬资质即 FAIL；"部分缺失有替代→WARNING 继续"未实现，WARNING 仅出现在资质库为空。需确认是否设计为简化版。

- **【低】归一化仅去单后缀** — `eligibility_checker.py:282-283`：循环对单一后缀只剥离一次，"ISO9001认证证书"类嵌套后缀无法完全归一，可能漏匹配。

- **【低/需确认】相似度换算可能双重平方** — `template_matcher.py:187`：若检索返回平方 L2，则 `distance**2` 错误。需核对 `VectorIndexManager.search` 返回类型。

## 4. 遗漏功能（规范有、代码无）

1. GUI 子类型字段 + 冷启动提示渲染（`info_verification.py` 未展示 `subtype_status/hint`）。
2. "LLM" 提取来源标签（后端 + GUI 均无）。
3. TemplateMatcher 文件系统关键词兜底检索。
4. TemplateMatcher 子类型分区检索权重表（冷启动/不足/正常三档 + `_shared`/最近邻权重）。
5. TemplateMatcher 对 `bid_subtype` 的读取与分区检索。
6. `_shared` 严格准入逻辑在模板匹配节点内的落地。
7. EligibilityChecker "部分缺失有替代→WARNING" 分级语义（仅库空触发）。

## 5. 跨节点/横切问题

- **节点④前后端字段不同步**：后端 `_VERIFICATION_FIELDS`（11）≠ GUI `_FIELDS`（10）。同一节点的字段契约在 backend/gui 两处各自维护，易漂移，应抽取为共享常量。
- **路由函数不可见**：`route_after_verification` / `route_after_eligibility`（规范§七）不在本次审计的 4 个文件内，无法验证 FAIL→END、pending→END、PASS/WARNING→TemplateMatcher 是否落实，需核对 graph builder。
- **bid_subtype 链路断点**：节点④能计算子类型冷启动信息，节点⑥却不消费 `bid_subtype`，子类型差异化生成链路在检索侧中断。
- **相似度换算依赖检索层契约**：`template_matcher.py:187` 的余弦近似依赖 `search` 返回值语义，属节点与检索层的隐式耦合，建议明确契约。

## 6. 需人工确认项

1. `route_after_verification` / `route_after_eligibility` 路由函数是否按规范§七实现（FAIL→END、pending→END、PASS/WARNING→TemplateMatcher）？（不在审计文件内）
2. EligibilityChecker 的 WARNING 是否刻意简化为"仅库空触发"，还是应实现"部分缺失→WARNING"？
3. `VectorIndexManager.search` 返回的是欧氏 L2 还是平方 L2？决定 `template_matcher.py:187` 相似度换算是否正确。
4. 子类型分区检索权重表与 `_shared` 准入是否在 `core/retrieval/subtype_router.py` 等检索层实现（节点⑥本身未含）；若已另实现，节点⑥是否调用？
5. GUI 的"子类型"字段缺失是临时还是遗漏？缺失是否因子类型改由别处（如上传面板）确认？
6. "LLM" 来源标签是否确有需求，还是规范描述与实现（归并到"补充说明"）有意分歧？
