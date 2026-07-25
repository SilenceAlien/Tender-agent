# 簇 C2 — 复审计报告（v2）

> 审计对象（同 C2）：节点④ InfoVerificationGate、⑤ EligibilityChecker、⑥ TemplateMatcher 及其 GUI 组件
> 对照规范：`system-workflow.md`（v1.0，2026-07-14，716 行）
> 方法：逐行 Read 上述 4 个源码文件 + 规范 ④⑤⑥ 与 §七；逐项核对 C2 簇 5 个旧问题修复状态，并查回归/遗漏。
> 结论概览：**H8、M1 已修复；M17 部分修复但引入回归；M2、M3 未修复**。新发现 **3 项中危回归（N1 补充说明源死代码、N2 bid_subtype 未标注、N3 死分支）+ 1 项跨节点回写缺失（N4）**。

## 1. 文件清单（路径 + 行数 + 一句话职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/info_verification_gate.py` | 359 | 节点④：11 字段摘要、来源标注、两模式（pending/auto_confirm）、`apply_user_corrections()` 注入修正 |
| `bid-agent/gui/components/info_verification.py` | 158 | 节点④ GUI：可编辑校验表单、来源标签、必填校验、冷启动提示、确认按钮 |
| `bid-agent/core/nodes/eligibility_checker.py` | 427 | 节点⑤：9 类软要求过滤 + 硬资质归一化子串匹配；返回 verdict |
| `bid-agent/core/nodes/template_matcher.py` | 269 | 节点⑥：FAISS 语义检索 + 关键词兜底 + bid_type 精确过滤 + Top-3 |
| `system-workflow.md` | 716 | 系统工作流规范（对照基准） |

## 2. 旧问题修复核对表（C2 簇）

| 旧问题 | 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|--------|----------|---------------------|------|------|
| **H8**（高）GUI `_FIELDS` 仅 10 字段缺 `bid_subtype` | ④ 11 字段表单 | `info_verification.py:18-31`、`info_verification_gate.py:38-51` | ✅ **已修复** | 前后端均为 11 字段（含 `bid_subtype`，`info_verification.py:30` / `info_verification_gate.py:50`），两端一致；且 `apply_user_corrections` 能收到子类型修正值。 |
| **M1**（中）TemplateMatcher 缺文件系统关键词兜底 | ⑥ 双模式检索 + §6 Fallback | `template_matcher.py:110-150`、`181-192` | ✅ **已修复** | 新增 `_keyword_fallback_match`（`:110-150`）并在 `search_fn is None` 时调用（`:181-192`），FAISS 不可用时返回 Top-3 关键词匹配结果。 |
| **M2**（中）TemplateMatcher 未消费 `bid_subtype` / 无分区权重表 | ⑥ 子类型分区检索权重表 + `_shared` 准入 | `template_matcher.py:153-268` | ❌ **未修复** | 全程仅读取 `state.get("bid_type", "")`（`:183`、`:207`），**未读取 `bid_subtype`**；无冷启动/不足/正常三档权重表、`_shared`/最近邻加权融合逻辑。检索仍退化为全局 FAISS + `bid_type` 精确过滤。（注：检索层 `subtype_router.py` 据 C6_v2 已实现权重表与 `_shared` 准入，但节点⑥本身未消费 `bid_subtype`、未调用之。） |
| **M3**（中）WARNING 语义偏差：任何缺失硬资质一律 FAIL | ⑤ WARNING=部分缺失有替代继续、§七 PASS/WARNING→继续 | `eligibility_checker.py:396-406`、`365-383` | ❌ **未修复** | `missing` 分支无脑 `verdict="FAIL"`（`:400`），WARNING **仅在 `company_quals` 库为空时产生**（`:365-383`）。"部分缺失但有替代→WARNING 继续"语义仍未实现。 |
| **M17**（中）来源标注缺 "LLM" 类别 | ④ 5 类来源含 LLM | `info_verification_gate.py:57-126`（`_annotate_field_sources`）、`info_verification.py:32-40` | ⚠️ **部分修复 + 引入回归** | 枚举与 GUI 颜色已含 "LLM"（`:73`、`:37`）；但标注逻辑**未真正区分**——见 N1/N2/N3。 |

## 3. 规范整体符合度核对

| 规范条款 | 状态 | 证据 / 说明 |
|----------|------|------------|
| ④ 11 字段可编辑表单（前后端一致） | ✅ | `info_verification_gate.py:38-51` 与 `info_verification.py:18-31` 均 11 字段 |
| ④ 每字段标注来源（招标文件/补充说明/LLM/用户填写/缺失 5 类） | ⚠️ 部分 | 枚举含 5 类，但"补充说明"实际永不产出（N1），`bid_subtype` 无来源标注（N2）；"LLM"被滥用覆盖全部 contract 字段（N1） |
| ④ 冷启动状态提示 | ✅ | 后端 `:175-190` 计算 + GUI `:130-137` 渲染 `subtype_status/hint` |
| ④ 两模式（interactive pending / headless auto_confirm） | ✅ | `:232-283` |
| ⑤ 9 类软要求自动通过 | ✅ | `eligibility_checker.py:51-213` 9 类齐全 |
| ⑤ 硬资质归一化子串匹配 | ✅ | `:273-298`（仍单次去后缀，`ISO9001认证证书` 嵌套仅去一层，低危延续） |
| ⑤ WARNING 分级语义 | ❌ | 仅库空触发（`:365-383`），缺"部分缺失有替代"（`:396-406`） |
| ⑥ 双模式检索（FAISS 语义 + 关键词兜底） | ✅ | `template_matcher.py:181-192` 关键词兜底已落地 |
| ⑥ 子类型分区权重表 + `_shared` 准入 + 消费 bid_subtype | ❌ | 节点⑥未读 `bid_subtype`、无权重融合（M2） |
| ⑥ Top-3 + selected_template_id + 阈值 0.3 | ✅ | `:241-268` |

## 4. 新发现问题（回归 / 遗漏）

### 中
- **N1 — 回归："补充说明"来源类别成为死代码（永不产出）**
  `info_verification_gate.py:118-119` 的 `else` 分支注释明写"contract fields without LLM annotation = 补充说明"，但代码却赋值 `sources[field_name] = "LLM"`。整段函数所有可能赋值中，"补充说明"**从未被赋给任何字段**（其余分支均赋值 "用户填写"/"招标文件"/"LLM"/"缺失"，见 `:109/:113/:116/:119/:122/:124`）。规范④明确列出的 5 类来源中，"补充说明"已无法出现，且前端 `_SOURCE_COLORS["补充说明"]`（`:38`）成为永不被使用的死映射。M17 修复把全部 contract 字段一律标成 "LLM"，丢失了与"补充说明"的区分，属回归。

- **N2 — 回归：`bid_subtype` 未被来源标注，前端恒显"缺失"**
  `info_verification_gate.py:104` `all_fields = {**req_fields, **contract_fields}` **不含 `bid_subtype`**（req/contract 共 10 字段），于是 `:106` 的遍历循环从不处理 `bid_subtype`，`field_sources` 中无该键。GUI `:97` `field_sources.get(field_key, "缺失")` 对子类型恒返回"缺失"——即便其值已由 `:176-179` 正确填入 `info_summary`。后果：用户看到的子类型来源永远"缺失"，误导且破坏来源一致性。

- **N3 — 死分支：`contract_secondary` 的 "LLM" 判定永不可达**
  `info_verification_gate.py:110` 进入 `elif value:`（要求 `value` 为真），而 `:114` 的条件又要求 `and not value`，二者互斥 → 该分支（`:114-116`，本应把"仅 contract 次级源有值"标为 LLM）**恒为 False、永不可达**。说明该分支逻辑写错，应置于 `elif value:` 之外（与 `:120` 合并），否则合约次级来源字段的 LLM 标注只能依赖 `:120-122`，且与 N1 共同导致"补充说明"彻底消失。

### 跨节点 / 回写（需确认）
- **N4 — `apply_user_corrections` 未将表单编辑的 `bid_subtype` 回写 `state.bid_subtype`**
  `info_verification_gate.py:286-358`：表单已含 `bid_subtype`（`info_verification.py:30`），`corrected_fields` 含该键（`:117`），函数把 `corrected_fields` 写入 `user_confirmed_fields`（`:351`）但**未更新顶层 `state["bid_subtype"]`**。`_build_info_summary` 因优先读 `user_fields`（`:176`）故 `info_summary.bid_subtype` 正确；但下游 `SectionGenerator`/`TemplateMatcher` 若读 `state.bid_subtype`（规范⑧/⑥ 输入含之），将使用**编辑前的旧值**。即"前端改了子类型、生成阶段不生效"。属本次新增 `bid_subtype` 字段后引入的跨节点回写缺失。

### 低（延续，非回归）
- **L1 — 归一化仅去单后缀**（同旧 C2 低）：`eligibility_checker.py:281-283` 循环对单一后缀只剥离一次，`ISO9001认证证书` 残留"认证"，潜在漏匹配。
- **L2 — `build_default_search_fn` 维度注释矛盾**：`template_matcher.py:67` 注释写 `MockEmbedder(dim=64)`，实际 `:80` 用 `dim=1536`；且默认仍用随机 `MockEmbedder`，未接 OpenAI（与 C6 簇 H4 同）。

## 5. 修复状态结论

| 项目 | 结论 |
|------|------|
| 已修复 | **H8**（前后端 11 字段一致，`info_verification.py:30`/`info_verification_gate.py:50`）、**M1**（关键词兜底，`template_matcher.py:110-150,181-192`） |
| 部分修复(含回归) | **M17**（枚举加了 LLM，但"补充说明"变死代码 N1、bid_subtype 无标注 N2、次级源分支死 N3） |
| 未修复 | **M2**（节点⑥不消费 `bid_subtype`、无分区权重表与 `_shared` 加权，`template_matcher.py:153-268`）、**M3**（WARNING 仅库空触发，`:396-406`/`:365-383`） |
| 新缺陷 | **N1** 补充说明源死代码（`:118-119`）；**N2** bid_subtype 来源恒缺（`:104,106` vs GUI `:97`）；**N3** contract_secondary 死分支（`:110,114`）；**N4** 跨节点 bid_subtype 回写缺失（`:286-358`） |

## 6. 需人工确认项

1. `route_after_verification` / `route_after_eligibility`（规范§七）是否按 FAIL→END、pending→END、PASS/WARNING→TemplateMatcher 实现？（不在本 4 文件内，需核对 graph builder）
2. M3 的 WARNING 是否刻意简化为"仅库空触发"，还是应实现"部分缺失有替代→WARNING"？（业务决策）
3. "补充说明"与"LLM"两来源应如何区分——contract 字段（经 LLM 从补充说明提取）应标 "LLM" 还是 "补充说明"？当前实现令"补充说明"永久消失（N1）。
4. 节点⑥是否应消费 `bid_subtype` 并调用 `subtype_router.py` 的权重表/`_shared` 准入（M2 / C6_v2 已具备检索层能力）？
5. `apply_user_corrections` 是否需补 `bid_subtype` 回写（`state["bid_subtype"]`），以消除 N4 跨节点不一致？
