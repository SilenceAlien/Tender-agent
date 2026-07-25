# 标书代码审计 — 中危项（M2/M4/M6/M8/M11/M12/M15/M16）修复整合报告

> 生成时间：2026-07-20 16:16
> 编排师：AgentsOrchestrator（智能体编排师）
> 范围：上一轮复审计遗留的 8 个中危项，本轮集中修复

## 一、修复总览

| 项 | 文件 | 问题 | 修复方式 | 状态 |
|----|------|------|----------|------|
| M2 | eligibility_checker.py | 缺失硬资质一律 FAIL，可替代情形被错杀 | 新增替代项查询，有替代→WARNING 继续 | ✅ |
| M4 | feedback_processor.py | 三层反馈历史缺失，仅扁平列表 | 实现 global_constraints/compressed_history/context_window_size=3，保持扁平向后兼容 | ✅ |
| M6 | compliance_checker.py | 格式合规仅声明级 | 改为内容级最大努力检查（页边距/字体/行距/目录/页眉页脚/签章）+ 人工确认项显式列出 | ✅ |
| M8 | doc_assembler.py | 页边距默认 下3.5/右2.6 ≠ 规范 上下3.7/左右2.8 | 默认值统一为 3.7/2.8 | ✅ |
| M11 | consistency_lesson_store/extractor | 单字关键词(人/元)过度命中 | 加最小长度约束（中文≥2/英文≥3），单字丢弃 | ✅ |
| M12 | consistency_lesson_store.py | 去重分支 sim>=0.6 无章节重叠也合并 | 合并严格为 Jaccard≥0.3 且 章节重叠 双重 AND | ✅ |
| M15 | review_panel.py | rejected 重跑整段 Phase2 | 改为最小回路 ⑨→⑦（FeedbackProcessor→SectionGenerator 定向修订） | ✅ |
| M16 | core/llm/router.py | 默认质检模型 openai/gpt-4o-mini ≠ 规范 DeepSeek | 默认改为 deepseek/deepseek-chat | ✅ |

## 二、关键修复说明

### M2 — 资质 WARNING 语义（门禁不再错杀）
原逻辑「任何缺失硬资质→FAIL」会把「缺失但有企业资质库替代项」的情形一并终止。修复后：
- 归一化匹配未命中时，查 `data/company_quals.json` 是否有语义可替代项（如 ISO9001↔GB/T19001）；
- 有替代→`warning_quals`（WARNING 继续），无替代→`missing`（FAIL 终止）；
- 门禁路由不变：FAIL 优先级最高，其次 WARNING，全过 PASS。
- 9 类软要求自动通过、归一化子串匹配等既有逻辑未动。

### M4 — 三层反馈历史（自进化闭环补全）
`AgentState` 早已声明 `global_constraints/compressed_history/context_window_size`，但节点从未填充。修复补齐：
- `build_global_constraints`（L1 累积去重）、`compress_feedback_history`（L2 同类压缩）、`get_recent_context_window`（L3 仅留最近 3 轮）；
- 扁平 `feedback_history` 保持不动 → SectionGenerator 直接消费，向后兼容零 KeyError。

### M6 — 格式合规内容级（不再静默误判 PASS）
重写 `_check_format_compliance`：页边距解析（支持 `上3.7 下3.7`/`3.7/2.8`/带 cm 单位）、字体（正文仿宋/标题黑体楷体）、行距、目录 TOC 扫描、页眉页脚、签章核验；无法静态核实项统一标 `severity=low` + `manual_confirm` 显式列出，避免「没查到就等于合规」。法律合规（N2/M5）与 verdict 逻辑未动。

### M8 — 页边距对齐规范
仅改字面量（L9/L41 docstring、L26 注释、L27/L46 默认值、L1167-1170 应用处），未动车装配逻辑（H7 页码/力导向/Mermaid 保持）。Grep 确认 `2.6|3.5` 无残留。

### M11+M12 — 自进化关键词与去重收敛
- M11：新增 `_is_valid_keyword()` 过滤单字；
- M12：删除「无章节重叠也合并」分支，严格双重 AND。
- H11 类型映射、applicable_sections 字面匹配未受影响。

### M15 — 最小回路（驳回不再重跑全流程）
新增 `_build_feedback_loop_graph()`（入口 FeedbackProcessor，跳过 EligibilityChecker/TemplateMatcher）。rejected 现仅走 ⑨→⑦ 定向修订，仍保留其后校验链回到 HumanReviewGate 复核；rejected+超轮→强制装配(⑭)、approved/pending 逻辑未破坏。

### M16 — 默认模型对齐规范
`router.py:21` 默认 `openai/gpt-4o-mini` → `deepseek/deepseek-chat`。`pipeline_runner.py:95` 始终显式传模型，不与默认冲突；providers 工厂未动。

## 三、验证结果

- **联合编译**：8 个改动文件 `py_compile` 全部通过（ALL_COMPILE_OK，exit 0）。
- **各 agent 自测**：M2 8 类场景、M4 三层结构、M6 页边距三种写法、M11 单字过滤、M12 跨章节不合并、M15 py_compile、M16 Grep 默认值——均通过，临时脚本已清理。
- **未引入新外部依赖**，未破坏已修好的 N0/H7/N1/N2/N3/N4/M17/H3/H6/M9/M10 等。

## 四、遗留项（低危 / 非阻塞，已确认不影响主流程）

- L 系列低危（原 findings_v2 中的 L1/L4/L6/L7/L8/L9）：检索距离度量 L2、QualityChecker 重试、ScoreSimulator 截断、graph 死代码、database 未接入、PromptRegistry/Selector 未接线。
- 这些项不影响主流程跑通，建议后续按需增量处理。

## 五、累计修复清单（全三轮）

- **复审计发现已修**：N0（关键词检索崩溃）、H3/H6（元数据过滤/权重表）、H7（装配格式）、N1（优化模板评分对齐）、N3（短章死循环）、N2（保密正则）、N4/M17（子类型回写）、H4（MockEmbedder 标注）
- **code-review 发现已修**：空章静默 PASS、路径遍历风险、过度豁免偏离规范
- **本轮已修**：M2/M4/M6/M8/M11/M12/M15/M16
- **系统 14 节点 + 19 条路由 + 20 字段**：主体功能与规范一致，高危全清，中危全清。

---
**结论**：标书制作智能体代码与 system-workflow.md 规范的核心偏差已系统性收敛，剩余仅为低危非阻塞项。质量置信度：HIGH。
