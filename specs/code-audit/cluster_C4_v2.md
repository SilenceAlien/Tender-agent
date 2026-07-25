# 簇 C4 — 复审计报告（_v2）

> 审计对象：节点 ⑧⑨⑩⑪⑫⑬ 六个源码文件 vs `system-workflow.md`
> 复审日期：2026-07-20 | 方法：逐行通读源码 + 逐条对照规范 + 核对上轮 7 项问题修复情况
> 对照基线：`cluster_C4.md`（首轮 C4 簇发现）

## 1. 旧问题修复核对表（7 项逐项）

| 编号 | 问题 | 规范依据 | 修复状态 | 证据 file:line |
|------|------|---------|---------|---------------|
| **H1** | CrossReference severity 误判（人员/技术/时间=medium、拼凑残留=high） | `system-workflow.md:400-405`（high/CRITICAL） | ✅ **已修复** | `cross_reference_checker.py:90`(人员=high)、`:153`(时间=high)、`:341`(技术参数=high)、`:274`(契约偏离=critical)、`:287`(拼凑残留 forbidden_industries=critical)。verdict 改为 `.lower() in ("high","critical")`（`:392-396`），high 与 critical 均触发 FAIL |
| **H2** | QualityChecker 每章≥8000字完整度门槛缺失 | `system-workflow.md:335` | ✅ **已修复** | `quality_checker.py:544-553` 新增 `min_section_chars`(默认8000) 检查，<8000 字章节追加完整度问题（跳过 `【生成失败】` 占位） |
| **M4** | FeedbackProcessor 三层反馈历史缺失 | `system-workflow.md:372-375` | ❌ **未修复** | `feedback_processor.py` 全文仍仅 `feedback_history = list(state.get("feedback_report",[]))`（`:125`）+ 扁平 append（`:207`），无任何 `global_constraints`/`compressed_history`/`context_window_size` 引用 |
| **M5** | ComplianceChecker 缺知识产权(著作权法)/保密信息泄露(保密法) | `system-workflow.md:433-434` | ✅ **已修复** | `compliance_checker.py:164-177`(IP 检查，severity=medium)、`:179-198`(保密泄露，severity=high) |
| **M6** | ComplianceChecker 格式合规仅声明级 | `system-workflow.md:422-425` | ❌ **未修复** | `compliance_checker.py:58-105` 仍仅校验 `format_rules` 是否声明 `page_margin/font/line_spacing`(`:83-94`) 与 `seal_requirement`(`:97-103`)；注释 `:62` 自承无法验证字体/行距/目录/页眉页脚内容级 |
| **L4** | QualityChecker LLM 未按规范"重试 1 次" | `system-workflow.md:340` | ❌ **未修复** | `_llm_quality_check` `quality_checker.py:467` 仅单次 `llm_fn(prompt)`，失败即 FAIL（`:489-496`），无重试 |
| **L6** | ScoreSimulator 截断 6000 vs QualityChecker 8000 | `score_simulator.py:196` vs `quality_checker.py:458` | ❌ **未修复** | `score_simulator.py:196` 仍 `content[:6000]` |

**结论**：7 项中 H1、H2、M5 已修复；M4、M6、L4、L6 仍违反规范（未修复）。

## 2. 节点实现整体复核（对照首轮）

### ⑧ QualityChecker
- 评分项覆盖率 `_check_completeness` `:28-74` ✓；每章8000字 ✓（H2 修复）；三类问题清单 ✓；LLM 非JSON→FAIL ✓；无LLM启发式 ✓。
- **回归/新弱点**：见 §3 N1、N2、N6。

### ⑨ FeedbackProcessor
- 四类反馈 `_classify_issue` `:54-80` ✓；作用域 global/local ✓；读取 cross_ref/compliance 防死循环 ✓(`:138-160`)；自进化触发 `_learn_consistency_lessons` `:23-49,178` ✓。
- **未修复**：三层反馈历史 M4（§1）。

### ⑩ CrossReferenceChecker
- 人员/金额/时间/技术参数/文档组成 = high ✓；契约偏离/拼凑残留 = critical ✓；verdict 正确 ✓。
- **遗留**：severity 枚举值用小写 "critical" 不符规范大写 "CRITICAL"（见 §3 N5，仅修辞）。

### ⑪ ComplianceChecker
- 格式声明级 ✓；法律绝对化用语(含上下文正则) ✓；虚假业绩 ✓；知识产权 ✓（M5 修复）；保密泄露 ✓（M5 修复）。
- **未修复**：格式内容级校验 M6（§1）。
- **新弱点**：见 §3 N3（正则过宽导致误报 FAIL）。

### ⑫ ScoreSimulator
- 双模式 LLM/启发式 ✓；中文2-gram `_tokenize_cn` ✓；输出逐项/gap/总分/排名 ✓；非JSON→启发式 ✓。
- **未修复**：截断 6000 vs 8000 不一致 L6（§1）。

### ⑬ HumanReviewGate
- 两模式 pending/auto_approve ✓；幂等 `:94-104` ✓；rejected→feedback_history 转换 `:166-179` ✓。
- **新弱点**：见 §3 N4（rejected 路径双重喂入 + 轮次未+1）。

## 3. 新发现问题 / 回归

### 中（Medium）

- **N1 — [回归] 8000字门槛对"格式固定型"短章节一刀切，可能触发虚假 FAIL/修订循环**
  H2 修复在 `quality_checker.py:544-553` 对**所有**章节统一要求 ≥8000 字，但未豁免规范明确为"格式固定型"的章节（投标函 `ch1_letter`、授权委托书 `ch2_authorization`，见 `system-workflow.md:287-288`）。这两章天然仅数百字，必触发完整度 FAIL → 路由 ⑨ → 强制重生成，形成无意义修订循环或掩盖真实问题。
  - 证据：`quality_checker.py:544-553`（无 format-fixed 豁免分支）

- **N2 — [回归/缺陷] `_check_completeness` 中文关键词兜底失效**
  `quality_checker.py:62` `name_lower.replace("，"," ").split()` 对中文（无空格/无逗号）不产生分词，`tokens` 退化为整条评分项名单一 token；`:65` 的 60% 关键词重叠兜底对中文**完全失效**（仅英文空格分词可用）。改写后的评分项（如"人员配置方案"被"人员/配置"覆盖）会误判为"未覆盖"，产生虚假完整度问题。同簇 ScoreSimulator 已用 `_tokenize_cn` 2-gram 修复（`score_simulator.py:63-92`），QualityChecker 漏改，两节点口径不一致。
  - 证据：`quality_checker.py:62-67`

- **N3 — [新缺陷] 保密信息泄露正则过宽，造成误报 FAIL**
  `compliance_checker.py:184` `bank_account_pattern = r"\d{16,19}"` 会匹配任何 16–19 位连续数字（合同编号、纯数字项目编号、拼接日期等）；`:183` `id_card_pattern = r"\d{17}[\dXx]"` 匹配任何 18 位数字序列。标书中常见长数字串，将误报 high 级"保密信息泄露"→ verdict FAIL（`:242-243`），阻断管道。应加上下文约束或白名单（如"账号""卡号"前缀）。
  - 证据：`compliance_checker.py:182-198`

### 低（Low）

- **N4 — [脆弱耦合] 人工驳回路径反馈双重喂入且轮次未+1**
  `human_review_gate.py:166-179` 在 `resume_after_review` 将审核意见写入 `feedback_history`（round=`current_round` 未+1）；但 ⑨ `feedback_processor.py:128-160` **不读取 `feedback_history`**，仅从 quality/compliance/cross 报告重建反馈。审核意见实际只能靠 SectionGenerator 直接读 `feedback_history` 才生效，耦合脆弱；且 round 与后续 FeedbackProcessor 的 `current_round+1` 错位。
  - 证据：`human_review_gate.py:166-179`；`feedback_processor.py:128-160`

- **N5 — [修辞] severity 枚举大小写与规范不一致**
  `cross_reference_checker.py:274,287` 输出 "critical"（小写），规范 `system-workflow.md:404-405` 用 "CRITICAL"（大写）。当前 verdict 用 `.lower()` 归一（`:393`）故功能正常，但任何精确匹配 "CRITICAL" 的下游消费者会漏判。
  - 证据：`cross_reference_checker.py:274,287,392-396`

- **N6 — [指标不一致] coverage_rate/score 未计入 8000 字完整度失败**
  `quality_checker.py:551-558,589-591`：`total_items`/`passed_items`/`coverage_rate` 仅基于 `completeness` dict 与 compliance/consistency 计数，未包含新增的 8000 字完整度失败。结果：章节因过短被判 FAIL（verdict），但 `coverage_rate` 仍显示 1.0、`score` 虚高。
  - 证据：`quality_checker.py:551-558,589-591`

## 4. 跨节点/横切（延续首轮）

- **路由函数不可见**：`route_after_quality_check / route_after_cross_ref / route_after_compliance / route_after_review` 位于 graph 构建文件（非本簇 6 文件），"超轮次强制通过""pending→END""rejected+超轮→强制⑭"仍须人工确认（同首轮 §6）。
- **H1 已修复的连锁收益**：⑩ 现能正确将人员/技术/时间不一致路由至 ⑨，与 §七 路由表一致。
- **N1 与路由耦合**：8000 字门槛未豁免格式固定章节，会与"FAIL+未超轮次→⑨→重生成"形成无意义循环（见 N1）。

## 5. 复审结论

- **已修复（3）**：H1、H2、M5 — severity 分级与 verdict 逻辑、每章8000字门槛、知识产权/保密法两项均已落实，核心 H1 已精确核对枚举值与判定逻辑，确认修正。
- **未修复（4）**：M4（三层反馈历史）、M6（格式内容级校验）、L4（LLM 重试1次）、L6（截断6000 vs 8000）。
- **新发现（6）**：N1（8000字门槛误伤格式固定短章）、N2（中文关键词兜底失效）、N3（保密泄露正则过宽误报 FAIL）、N4（驳回反馈双重喂入）、N5（severity 大小写）、N6（coverage 指标不一致）。
- 建议优先处理 N1、N3（均会直接制造虚假 FAIL/阻断管道），其次补齐 M4/M6/L4/L6。
