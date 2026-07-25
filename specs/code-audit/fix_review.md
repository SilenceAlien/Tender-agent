# 代码审查（code-review）— 修复轮次结论

> 触发：用户要求审查上轮 9 文件修复是否合理/正确。
> 方法：按 code-review 技能，3 个并行审查 Agent（Bug / 安全 / 规范），置信度评分 ≥80 才保留。
> 结果：发现 **3 个 ≥80 问题**（置信度均 85），全部经主代理亲自核验代码证据后确认属实并修复。

## 审查发现（3 项 ≥80）

| # | 分类 | 文件:行 | 问题 | 置信度 | 严重度 |
|---|------|---------|------|--------|--------|
| 1 | bug | quality_checker.py:567-568 | 短章豁免 `continue` 跳过非空校验 → 真空章节静默判 PASS | 85 | 中高 |
| 2 | security | reference_retriever.py:88/122 | `bid_subtype`/`neighbor`/`kb_type` 拼路径未清洗 → 目录遍历/注入风险 | 85 | 高 |
| 3 | spec | quality_checker.py:33/567 | 豁免集合含 ch7/ch8，与 spec ⑦/⑧ 冲突（spec ⑦ 仅 ch2 为格式固定型） | 85 | 中 |

## 修复（Pro 模型直接 edit）

### F1 — 安全：路径组件清洗（reference_retriever.py）
- 新增模块级 `_safe_path_component(name)`：去 `..`、截断路径分隔符、仅保留 `\w`/CJK/连字符，返回单段安全目录名。
- 应用于 `kb_type`（`:82` 后）、`bid_subtype`（`:88`）、`neighbor`（`:122`）。
- 验证：`../../../etc`→`etc`、`C:\x\y`→`y`，均无 `..`/分隔符，拼到 `kb_root/` 下仍在知识库内，遍历消除。py_compile 通过。

### F2 — Bug + 规范：收窄豁免 + 非空守卫（quality_checker.py）
- `FORMAT_FIXED_SHORT_CHAPTERS` 由 `{ch2,ch7,ch8}` 收窄为 `{ch2_authorization}`（与 spec ⑦ 一致；ch7/ch8 改回走 spec ⑧ 的 8000 字门槛 + 超轮次强制通过，无死循环）。
- 在豁免 `continue` 之前新增非空守卫：真空/纯空白章节无论是否豁免都判完整度 FAIL，杜绝"空章静默 PASS"。
- 验证：空 ch2→报空；非空 ch2→通过；短 ch7→报不完整；达标 ch7→通过。py_compile 通过。

## 结论
- 本轮修复（前一轮 N0/H7/N1/N2/N3/N4/M17 等）**主体正确**，但引入/残留 3 项 ≥80 缺陷，现已全部修复并单测验证。
- 前一轮未被修改的文件、以及本轮 3 项之外的修复点，审查 Agent 均确认无 ≥80 问题。
- 仍未处理（低优先，非阻塞）：M2/M4/M6/M8/M11/M12/M15/M16 及 L 系列低危，见 fix_summary.md。

## 交付
- review_bug.md / review_security.md / review_spec.md（3 份专项审查证据）
- fix_review.md（本报告）
- 修复文件：reference_retriever.py、quality_checker.py（已 py_compile + 单测通过）
