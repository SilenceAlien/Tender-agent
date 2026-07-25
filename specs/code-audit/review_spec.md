# 规范符合度专项审查报告 — bid-agent 修复轮

> 审查对象：fix_summary.md + fix_C2~C6.md 对应修复
> 对照规范：AGENTS.md、system-workflow.md（节点④⑦⑧⑪⑭ 及策略 4.2/4.6）
> 审查范围：7 个改动文件（reference_retriever.py / pipeline.py / doc_assembler.py / optimized_prompts.py / quality_checker.py / compliance_checker.py / info_verification_gate.py）
> 口径：仅保留置信度 ≥80 的确证违规；工程判断性项标注为「建议」。

---

## 一、≥80 规范/风格违规

### [V1] N3 — 质量检查 8000 字豁免集合包含 spec ⑦ 标记为「差异化」的章节

- **file:line**：`quality_checker.py:33-37`（常量定义）、`quality_checker.py:567-568`（豁免分支）
- **违反条款**：system-workflow.md **节点⑦**（8 章结构表：仅 `ch2_authorization`=格式固定型，`ch7_schedule`/`ch8_after_sales`=差异化）+ **节点⑧**（"内容完整度：每章至少检查 8000 字"）
- **事实与判定**：
  - 修复将 `ch2_authorization`、`ch7_schedule`、`ch8_after_sales` 一并 `continue` 跳过 8000 字门槛，代码注释与 fix_summary.md 均称三者为「格式固定型短章」。
  - 但 spec ⑦ 的 8 章结构表明确：**仅 `ch2_authorization` 为格式固定型**，`ch7_schedule`（项目实施计划）与 `ch8_after_sales`（售后服务承诺）均为**差异化（子类型适配）**章节——修复的"格式固定短章"前提与规范事实不符。
  - 节点⑧ 要求"每章≥8000字完整度"为统一硬要求，对差异化章节豁免直接偏离该条款。
  - 且节点⑧ 路由已规定"FAIL + 已超轮次 → 强制通过到 CrossReferenceChecker（防止无限循环）"，即 8000 字未达标本有兜底、不会死循环；当前硬编码豁免会**静默放过 ch7/ch8 的真实不完整内容**，反而削弱质检。
- **置信度**：85（确为真实，规范⑦表格与⑧条款双重明确；仅余"是否应修订规范而非代码"的工程折衷空间）
- **建议**：
  1. 仅将 `ch2_authorization`（spec ⑦ 唯一格式固定型）纳入豁免集合；
  2. `ch7_schedule`/`ch8_after_sales` 恢复 8000 字检查（或确认为短章时，先在 spec/⑦ 修订分类后再豁免，遵循 AGENTS.md「规范先行」）；
  3. 同步更正 fix_summary.md 与代码注释中"ch7/ch8 为格式固定型"的错误表述。

---

## 二、建议（非违规，未达 80，仅供改进）

- **H6 子类型权重表未实际参与融合**（`reference_retriever.py:107-129`）：`get_retrieval_weights` 仅 `logger.debug` 记录，权重未用于检索排序/融合；`find_nearest_subtypes` 仅向候选列表追加文件，未体现 spec ⑥「子类型权重/_shared 权重/最近邻权重」动态加权。属实现不完整，建议在 RRF/候选加权处落地权重或明确此为 keyword 回退路径的简化。
- **合规正则内循环重复编译**（`compliance_checker.py:188-222`）：保密泄露相关正则（`_sensitive_prefix`/`_positive_ctx`/`_leak_verbs`）在「每章」循环内重复构造，建议提升为模块级常量，兼顾可读性与性能（风格，置信度 50）。
- **文档/注释与规范矛盾**（fix_summary.md、quality_checker.py:25-32）：将 ch7/ch8 描述为"格式固定型短章"，与 spec ⑦ 冲突，建议更正以免误导后续维护。

---

## 三、各修复项符合度结论

| 修复项 | 规范条款 | 结论 |
|--------|---------|------|
| H7 装配：连续页码 / 页眉页脚 / Mermaid 力导向 / 真实目录页码（PAGEREF+书签）/ 占位符红粗正则 / 文件名 `{project_id}_标书_v{round}` | ⑭ | ✅ 全部符合（力导向=Fruchterman–Reingold，箭头+边标签双渲染器均实现，PAGEREF 真实页码，文件名契约一致） |
| N2 保密信息泄露检查 | ⑪ | ✅ 符合（敏感标识符锚定前缀、文本型泄露排除"承诺/遵守/绝不"等正面声明，符合"上下文敏感匹配"精神，不误伤正面保密声明） |
| N4 来源标注 / 子类型回写 | ④ | ✅ 符合（招标文件/补充说明/LLM/用户填写/缺失 五类来源均可达、无死分支；bid_subtype 正确回写顶层 state） |
| N1 优化模板评分注入 | ⑦ | ✅ 符合（5 个优化模板均补 `{requirements_context}`，逐条响应评分项） |
| N3 8000 字门槛 | ⑦/⑧ | ❌ 与规范冲突（见 V1） |
| H3 元数据过滤 / H4 MockEmbedder / H6 权重消费 / N0 致命回归 / N6 片段 query | 4.2/代码修复 | ✅ 功能达成（H6 权重落地不完整见建议） |

**总结**：发现 **1** 个 ≥80 规范问题（N3，置信 85）；其余修复项均符合对应规范条款，无 YAGNI 违规、无规范先行偏离（bug 修复属例外）、无新增魔法字符串未常量化等风格违规。
