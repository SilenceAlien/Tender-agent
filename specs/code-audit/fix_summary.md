# 标书代码问题修复 — 整合报告（fix_summary）

> 基线：复审计 `findings_v2.md` 发现的缺陷（12 高危已修复 7，剩 5 高危 + 8 中危 + 修复回归 N0~N6）。
> 本轮按 P0/P1 分簇并行修复，文件互不重叠。所有改动文件已联合 `py_compile` 通过。

## 🚀 修复总览

| 簇 | 文件 | 修复项 | 状态 |
|----|------|--------|------|
| C6 | reference_retriever.py / pipeline.py / subtype_router.py | N0(致命回归) / H3 / H6 / N6 / H4 | ✅ |
| C5 | doc_assembler.py | H7 / M9 / M10 / 目录真实页码 | ✅ |
| C3 | optimized_prompts.py | N1(回归) | ✅ |
| C4 | quality_checker.py / compliance_checker.py | N3 / N2(回归) | ✅ |
| C2 | info_verification_gate.py | N4 / M17 | ✅ |

## 🔴 P0 — 致命/核心（必修）

### N0｜`reference_retriever.py` 关键词检索崩溃回归（最高优先）
- **根因**：修 H5（接通 doc store）时把 `_keyword_score` 方法体误塞进 `_load_doc_by_id` 的 `return` 之后，且丢失 `def` 行 → `:192` 调用抛 `AttributeError`，关键词检索主/兜底路径全崩。
- **修复**：在 `return ""` 之后独立还原 `def _keyword_score(self, query, text)` 方法（181 行），语义检索调用恢复可用。验证 `hasattr(r,'_keyword_score')==True`。

### H7｜`doc_assembler.py` 装配格式（政府采购硬要求）
- 连续页码：footer 插 PAGE 域（`:1192`）。
- 页眉页脚：读取 `format_rules.header/footer` 写入（`:1172-1191`）。
- Mermaid：新增 `_force_directed_layout` 力导向布局（`:334`）替换树形。
- 目录真实页码：删除硬编码"第{i}页"，改 PAGEREF 域+章节书签（`:1274/1302`）。

## 🟠 P1 — 中危回归/链断

### N1｜`optimized_prompts.py` 优化模板漏 `{requirements_context}`
- 5 章优化模板（sec1~sec4/sec5b）补「评分要求：\n{requirements_context}」；`section_generator.py:436` 已正确传参，无需改。

### N3｜`quality_checker.py` 8000 字门槛误伤短章
- 新增 `FORMAT_FIXED_SHORT_CHAPTERS = {ch2_authorization,ch7_schedule,ch8_after_sales}`，豁免章节只做非空+关键字段校验，避免"永远不达标→永远 FAIL→重生成"死循环。

### N2｜`compliance_checker.py` 保密泄露正则过宽
- 手机号/身份证/银行账号正则加敏感前缀锚定（普通长数字串不再误报）；文本泄露正则用可选捕获组排除"承诺/遵守/绝不/不泄露"等正面声明，仅真实泄露第三方保密内容才判违规。

### N4｜`info_verification_gate.py` 子类型回写断链
- `apply_user_corrections` 增补 `bid_subtype` 回写顶层 state；重构 `_annotate_field_sources` 删除死分支，`bid_subtype` 不再恒显"缺失"。

## 🟡 P2 — 上轮遗留中危（本轮一并收尾）

- **H3 元数据过滤**（`pipeline.py`）：`if metadata_filter: pass` 占位已实现真正过滤（`_matches_metadata` 辅助函数，str 大小写不敏感包含匹配）。
- **H6 子类型权重表**（`reference_retriever.py`）：接入 `get_retrieval_weights` + `find_nearest_subtypes`，冷启动/无匹配时补充最相似子类型 chunks。
- **H4 MockEmbedder**（`reference_retriever.py`）：标注离线/测试专用，不强制 OpenAI key。
- **M10 占位符正则**（`doc_assembler.py:1308`）：收紧为 `【待填写[：:][^】]+】`。
- **M9/N8 文件名契约**（`doc_assembler.py:1559-1571`）：`{project_id}_标书_v{max(1,round)}`，去时间戳、避免 v0。
- **M17 来源类别**（`info_verification_gate.py`）：五类来源全部可达、无死分支。

## ✅ 验证结论
- 9 个改动文件联合 `py_compile`：**ALL_COMPILE_OK**（退出码 0）。
- C6 运行时冒烟：`_keyword_score` 可用、子类型路由导入正常。
- C5/C3/C4/C2 各 agent 自测（单测/桩脚本）通过，临时脚本已清理。

## ⚠️ 仍未处理（低优先 / 需决策，非阻塞）
- 上轮遗留未修中危：M2(Eligibility WARNING 语义)、M4(FeedbackProcessor 三层反馈历史)、M6(Compliance 格式合规仅声明级)、M8(页边距默认值冲突 下3.5/右2.6 vs 规范3.7/2.8)、M11(自进化单字关键词过度命中)、M12(去重偏离)、M15(rejected 重跑整段 Phase2)、M16(router 默认模型误导)、L 系列低危。
- 这些不影响主流程跑通，建议作为下一轮增量优化。

## 📁 交付物
- `fix_summary.md`（本报告）
- `fix_C2.md` / `fix_C3.md` / `fix_C4.md` / `fix_C5.md` / `fix_C6.md`（5 份逐文件修复证据）
- 基线：`findings_v2.md` + `cluster_C*_v2.md`
