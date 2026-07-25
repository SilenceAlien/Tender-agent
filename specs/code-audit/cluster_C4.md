# 簇 C4 — 审计发现

> 审计对象：节点 ⑧⑨⑩⑪⑫⑬ 六个源码文件 vs `system-workflow.md`
> 审计日期：2026-07-20 | 方法：逐行通读源码 + 逐条对照规范

## 1. 文件清单（路径+行数+职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/nodes/quality_checker.py` | 607 | ⑧ 质量检查：评分项覆盖、违禁词、公司名一致性、可选 LLM 深度检查 |
| `bid-agent/core/nodes/feedback_processor.py` | 214 | ⑨ 反馈处理：问题分类、作用域识别、构建 feedback_history、触发自进化 |
| `bid-agent/core/nodes/cross_reference_checker.py` | 410 | ⑩ 跨章一致性：人员/金额/时间/拼凑/契约偏离/技术参数 |
| `bid-agent/core/nodes/compliance_checker.py` | 227 | ⑪ 合规审查：格式合规(声明级) + 法律合规(绝对化用语/虚假业绩) |
| `bid-agent/core/nodes/score_simulator.py` | 281 | ⑫ 评分模拟：LLM 逐项打分 / 无 LLM 中文 2-gram 启发式 |
| `bid-agent/core/nodes/human_review_gate.py` | 181 | ⑬ 人工审核卡点：pending/approved/rejected + resume_after_review 幂等 |

## 2. 节点实现对照表

### ⑧ QualityChecker

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 评分项覆盖率检查 | `quality_checker.py:28-74` | ✓ | `_check_completeness` 逐条比对，含 60% 关键词兜底 |
| 每章≥8000字完整度 | `quality_checker.py:103` | 缺失 | 仅检查 `len(content) < 50` 且属 compliance 检查；规范要求的每章 8000 字完整度检查完全未实现（见 §3 H2） |
| 三类问题清单(完整度/合规度/一致度) | `quality_checker.py:537-592` | ✓ | completeness/compliance/consistency 三类齐全 |
| LLM 非JSON不再静默PASS | `quality_checker.py:478-496` | ✓ | 解析失败返回 FAIL（非 PASS） |
| 三重容错→重试1次→FAIL | `quality_checker.py:467-479` | 错误(轻微) | 实现三重容错(代码块/花括号)，但**无重试**，单次失败即 FAIL（见 §3 L1） |
| 无LLM启发式兜底 | `quality_checker.py:546-581` | ✓ | llm_fn=None 时走 local_score 启发式 |
| 路由 PASS→⑩/FAIL+未超轮→⑨/FAIL+超轮→强制⑩ | （路由函数在 graph 构建文件，不在本文件） | 需人工确认 | 见 §6 |

### ⑨ FeedbackProcessor

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 四类反馈 content_fix/style_adjust/structure/score_align | `feedback_processor.py:54-80` | ✓ | `_classify_issue` 完整覆盖 |
| 作用域 global/local | `feedback_processor.py:62-80,186` | ✓ | 分类与 target_section 处理正确 |
| 三层反馈历史(global_constraints/compressed_history/context_window_size=3) | （全文无相关代码） | 缺失 | 仅维护扁平 `feedback_history` + `current_round`，三层历史管理完全未实现（见 §3 M1） |
| 一致性自进化触发(ConsistencyLessonExtractor→data/consistency_lessons.json) | `feedback_processor.py:23-49,178` | ✓ | 调用 `ConsistencyLessonExtractor.learn_from_state` |
| 始终返回 SectionGenerator | （路由在 graph 文件） | 需人工确认 | 节点产出 feedback_history+current_round |
| 读取 cross_ref/compliance 问题（防死循环 BUG-02） | `feedback_processor.py:138-160` | ✓ | 已读取 cross_ref_report / compliance_report |

### ⑩ CrossReferenceChecker

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 人员数量一致(规范:high) | `cross_reference_checker.py:47-93,90` | 错误 | severity 写死为 `"medium"`，应为 high（见 §3 H1） |
| 技术参数一致(规范:high) | `cross_reference_checker.py:307-344,341` | 错误 | severity `"medium"`，应为 high |
| 时间节点一致(规范:high) | `cross_reference_checker.py:131-156,153` | 错误 | severity `"medium"`，应为 high |
| 金额一致(规范:high) | `cross_reference_checker.py:96-128,125` | ✓ | severity "high" 正确 |
| 契约偏离(规范:CRITICAL) | `cross_reference_checker.py:249-290,274` | ✓ | severity "critical" 正确 |
| 拼凑残留(规范:CRITICAL) | `cross_reference_checker.py:277-288,287` | 错误 | forbidden_industries 实为 `"high"`，应为 CRITICAL |
| 路由 PASS→⑪/FAIL+未超轮→⑨ | `cross_reference_checker.py:391-392` + 路由函数 | 错误/需确认 | 因上面 severity 误判，人员/技术/时间 medium 永不触发 FAIL（见 §3 H1、§6） |
| 无LLM纯规则 | `cross_reference_checker.py:350-409` | ✓ | 纯正则，llm_fn 形参未使用 |

### ⑪ ComplianceChecker

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 格式合规:页边距/字体/行距/目录/页眉页脚 | `compliance_checker.py:58-105` | 缺失(部分) | 仅校验 `format_rules` 是否**声明**字段(缺失报警)；实际内容级字体/行距/目录/页眉页脚校验未实现（见 §3 M3） |
| 格式合规:seal_requirement | `compliance_checker.py:97-103` | ✓ | 缺失则报警 |
| 法律:绝对化用语上下文敏感正则 | `compliance_checker.py:44-53,111-148` | ✓(近似) | 实现上下文正则，但"第一"用负向先行而非规范 `(?<!候选人)第一` 负向后行（见 §3 L3） |
| 法律:虚假业绩 | `compliance_checker.py:150-162` | ✓ | fabricated_patterns 实现 |
| 法律:知识产权(著作权法) | （无） | 缺失 | 未授权使用他人案例/图片检查完全缺失（见 §3 M2） |
| 法律:保密信息泄露(保密法) | （无） | 缺失 | 客户名称脱敏/保密泄露检查完全缺失（见 §3 M2） |
| 路由 PASS→⑫/FAIL+未超轮→⑨ | 路由函数在 graph 文件 | 需人工确认 | 节点内 verdict 仅 legal high → FAIL（合规） |
| 无LLM正则 | `compliance_checker.py:170-227` | ✓ | 纯正则，llm_fn 未使用 |

### ⑫ ScoreSimulator

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 双模式 LLM逐项打分 | `score_simulator.py:185-222` | ✓ | `_llm_score` 严格 JSON |
| 双模式 无LLM启发式(中文2-gram+篇幅) | `score_simulator.py:63-179` | ✓ | `_tokenize_cn` 2-gram + 篇幅估算 |
| 输出 逐项得分/gap/改进建议/总分/排名预估 | `score_simulator.py:154-178,267-279` | ✓ | scores/total(rank_estimate)/method 齐全 |
| LLM非JSON→启发式兜底 | `score_simulator.py:220-222` | ✓ | 回退 `_heuristic_score` |

### ⑬ HumanReviewGate

| 规范条款 | 代码位置 | 状态 | 说明 |
|---------|---------|------|------|
| 法律依据 招投标法27条 | `human_review_gate.py:8-9,478`(docstring) | ✓ | 文档注明第二十七条 |
| 两模式 auto_approve=False→pending / True→自动通过 | `human_review_gate.py:80-89` | ✓ | 实现正确 |
| 幂等(已有verdict不覆盖) | `human_review_gate.py:94-104` | ✓ | 已有 approved/rejected 直接复用 |
| rejected→feedback_history 自动转条目 | `human_review_gate.py:166-179` | ✓ | resume_after_review 转换 |
| 路由 approved→⑭/rejected+未超轮→⑨/rejected+超轮→强制⑭/pending→END | 路由函数在 graph 文件 | 需人工确认 | "超轮→强制⑭"的轮次判断在路由函数，未在本文件验证（见 §6） |

## 3. 错误/缺陷明细（高/中/低）

### 高（High）

- **H1 — CrossReferenceChecker 严重程度误判，导致 3 类 high 检查永不 FAIL**
  `cross_reference_checker.py:90`（人员=medium）、`:153`（时间=medium）、`:341`（技术参数=medium）将规范明确为 **high** 的项写成 `medium`；`:287` 将"拼凑残留"写成 `high`（规范为 **CRITICAL**）。而 verdict 逻辑 `:391` 仅 `severity in ("high","critical")` 才判 FAIL。后果：人员数量/技术参数/时间节点三类跨章不一致**永远不会触发 FAIL**，不会路由到 ⑨ FeedbackProcessor，与规范 §七 `route_after_cross_ref: FAIL+未超轮次→FeedbackProcessor` 直接矛盾。规范的 high 项实际失效。
  - 证据：`cross_reference_checker.py:90,153,287,341,391`

- **H2 — QualityChecker 每章≥8000字完整度检查完全缺失**
  规范 §⑧ `system-workflow.md:335` 明确要求"内容完整度：每章至少检查 8000 字"。代码 `quality_checker.py` 中唯一的长度检查是 `_check_compliance` 的 `len(content) < 50`（`:103`），且归属于**合规度**而非**完整度**；ComplianceChecker 仅检查 `<100`。规范的"每章 8000 字"完整度门槛在 ⑧ 节点无任何实现，章节即使严重过短也可 PASS 完整度。
  - 证据：`quality_checker.py:103`（仅 50 字阈值）、规范 `system-workflow.md:335`

### 中（Medium）

- **M1 — FeedbackProcessor 三层反馈历史管理完全缺失**
  规范 §⑨ `system-workflow.md:372-375` 要求 Layer1 `global_constraints`、Layer2 `compressed_history`、Layer3 `context_window_size`(默认3)。`feedback_processor.py` 全文仅维护扁平 `feedback_history` 列表与递增 `current_round`（`:125,207-208`），无任何三层结构、压缩或窗口裁剪逻辑。长轮次下 feedback_history 无限增长，且全局约束未与回合历史分离。
  - 证据：`feedback_processor.py`（无 global_constraints/compressed_history/context_window_size 引用）

- **M2 — ComplianceChecker 法律合规缺"知识产权"与"保密信息泄露"两项**
  规范 §⑪ 法律合规表列 4 项（绝对化用语/虚假业绩/知识产权/保密信息泄露）。`_check_legal_compliance`（`compliance_checker.py:111-164`）仅实现绝对化用语与虚假业绩，**未实现**知识产权（未授权使用他人案例/图片，著作权法）与保密信息泄露（客户名称脱敏，保密法）检查。此类违规将静默通过。
  - 证据：`compliance_checker.py:111-164`（无 IP/保密分支）

- **M3 — ComplianceChecker 格式合规仅为"声明级"，未做内容级校验**
  规范要求校验页边距/字体/行距/目录/页眉页脚（含内容）。`_check_format_compliance`（`compliance_checker.py:58-105`）仅检查：(a) 章节字数<100；(b) `format_rules` 是否声明 `page_margin/font/line_spacing`；(c) `seal_requirement` 是否声明。代码注释 `:62` 自承"无法验证页边距/字体（需渲染 DOCX）"。即实际格式合规性（字体用错、行距不符、目录缺失等）未被检查。
  - 证据：`compliance_checker.py:58-105`

### 低（Low）

- **L1 — QualityChecker LLM 未按规范"重试 1 次"**
  规范 §⑧ `system-workflow.md:340`："三重 JSON 解析容错 → 重试 1 次仍失败 → 判定 FAIL"。`_llm_quality_check`（`quality_checker.py:467-479`）做了三重容错，但**仅单次调用 llm_fn**，失败即返回 FAIL，无重试。行为结果虽同为 FAIL，但失去规范的"给 LLM 第二次机会"意图，瞬时格式抖动会直接 FAIL。
  - 证据：`quality_checker.py:467-496`

- **L2 — FeedbackProcessor 正则写法冗余**
  `_extract_section_name` `feedback_processor.py:87` 使用 `r"[「「]([^」」]+)[」」]"`，字符类重复（`[「「]`=`[「]`）。功能正常但属笔误式冗余，建议修正。

- **L3 — ComplianceChecker "第一"正则策略与规范示例不一致**
  规范 §⑪ `system-workflow.md:438` 示例为 `(?<!候选人)第一`（负向后行）。代码 `compliance_checker.py:46` 用 `第[一](?![章节条款次部分批期名次])`（负向前行），排除集含"名"故"第一名投标候选人"近似不误报，但策略不同，边界语义（如"候选人第一"）需确认。
  - 证据：`compliance_checker.py:46`

- **L4 — ScoreSimulator 章节截断 6000 vs QualityChecker 8000 不一致（轻微）**
  `score_simulator.py:196` 截断 `content[:6000]`，而 `quality_checker.py:458` 用 8000。规范未强制，但两节点评审口径不一致，长章节在评分模拟中被截断更多。

## 4. 遗漏功能（规范有、代码无）

1. **⑧ 每章≥8000字完整度门槛** — 规范 `system-workflow.md:335`，代码无（见 H2）。
2. **⑨ 三层反馈历史(global_constraints/compressed_history/context_window_size=3)** — 规范 `:372-375`，代码无（见 M1）。
3. **⑪ 法律合规-知识产权检查** — 规范 `:433`，代码无（见 M2）。
4. **⑪ 法律合规-保密信息泄露检查** — 规范 `:434`，代码无（见 M2）。
5. **⑪ 格式合规内容级校验(字体/行距/目录/页眉页脚)** — 规范 `:422-424`，代码仅声明级（见 M3）。
6. **⑧ LLM 重试 1 次机制** — 规范 `:340`，代码无（见 L1）。
7. **⑧ 节点输入含 `feedback_history`**（规范 `:330`）— 代码 `quality_checker.py` 未读取（影响待确认，可能非必需）。

## 5. 跨节点/横切问题

- **路由函数不可见（关键）**：规范 §七 的 `route_after_quality_check / route_after_cross_ref / route_after_compliance / route_after_feedback / route_after_review` 全部位于 graph 构建文件（非本次审计的 6 个文件）。因此"FAIL+已超轮次→强制通过⑩/⑪/⑭""pending→END""rejected+超轮→强制⑭"等轮次判断逻辑**无法在本簇验证**，必须在 `build_generation_graph` 等处人工确认（见 §6）。
- **H1 的连锁效应**：⑩ severity 误判使 人员/技术/时间 不一致被静默放过，直接影响 ⑨ 反馈循环与最终标书质量；该缺陷与 §七 路由表矛盾，是跨节点一致性问题。
- **自进化落盘确认**：⑨ 调用 `ConsistencyLessonExtractor.learn_from_state`，但"是否真正写入 `data/consistency_lessons.json`"由 extractor 内部实现，不在本簇，需人工确认（规范 §4.3）。
- **LLM 容错策略不统一**：⑧ 非JSON→FAIL（无重试）；⑫ 非JSON→回退启发式。两者均"不静默 PASS"，符合大方向，但具体策略（重试 vs 回退）未对齐规范描述。

## 6. 需人工确认项

1. **路由函数实现**：`route_after_quality_check / route_after_cross_ref / route_after_compliance / route_after_feedback / route_after_review` 是否按规范实现"超轮次强制通过""pending→END""rejected+超轮→强制⑭"？请查 graph 构建文件（如 `pipeline.py`/`graph.py`）。
2. **⑧ QualityChecker 是否应消费 `feedback_history`**（规范 `:330` 列入输入）？当前未读取，是否影响定向修订判定？
3. **⑨ 三层反馈历史是否改在 `AgentState` 或其他模块实现**？本节点确实未实现，但全局约束可能由 state 层托管，需确认归属。
4. **`ConsistencyLessonExtractor.learn_from_state` 是否落盘到 `data/consistency_lessons.json`**（规范 §4.3）？需查 extractor 源码。
5. **⑬ "rejected+超轮→强制⑭"的 `current_round >= max_rounds` 判断**是否在 `route_after_review` 中正确实现（本文件仅 `resume_after_review` 转换历史，未做轮次判断）。
6. **⑩ 文档组成拼凑检查 `_check_document_composition_consistency`（`:183-243`，severity=high）** 属规范 5 类之外的新增强项，是否经规范认可？其 high 判定与规范一致，但"拼凑残留"规范归 CRITICAL，代码将其与 forbidden_industries 同置 high（`:287`），需确认分类口径。
