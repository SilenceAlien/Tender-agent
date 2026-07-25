# Bug 扫描专项 — 修复回归审查（review_bug）

> 审查对象：子代理修复的 9 个文件（C2/C3/C4/C5/C6 簇）
> 基线：`fix_summary.md` + `fix_C2~C6.md` 修复证据
> 方法：仅审查变更点，对照代码实际实现核验「改了什么」「是否引入回归」「修复本身是否正确」
> 保留规则：仅保留置信度 ≥80 的问题；低于 80 的丢弃。

---

## 一、各修复项正确性结论（无问题项）

- **N0（致命回归）已正确修复，无问题**：`reference_retriever.py:181` 已独立定义 `def _keyword_score(self, query: str, text: str) -> float:`（4 空格方法级缩进，位于 `_load_doc_by_id` 的 `return ""` 之后、无死代码）；关键词检索主/兜底路径在 `:225` 正常调用 `self._keyword_score(query, p)`，不再抛 `AttributeError`。`query_hint` 参数在 `:148` 签名、`snippet_query = query_hint or "招标要求"`（:158）、`:274` 调用处均正确传递。
- **H3（元数据过滤）已正确修复，无问题**：`pipeline.py:154` `_matches_metadata` 对 str→大小写不敏感包含、非 str→相等，且 `actual is None` 返回 False（必须显式满足）；`search`（:134-149）逻辑为「无 metadata_store/doc 透传保留、不匹配才丢弃」，不会误删正常文档。
- **H6（权重表接入）已正确修复，无问题**：`reference_retriever.py:115` `get_retrieval_weights(bid_subtype, kb_root=self.kb_root)`（self.kb_root 已是 Path，无路径错误）；`:121` `find_nearest_subtypes` 在 `neighbor>0` 或 `not candidates` 的回退位调用；未改动既有「子类型→_shared→范文→rglob」回退顺序。导入与调用正常，无异常。
- **H7（装配）已正确修复，无问题**：`_force_directed_layout`（:334）单节点返回 `(0.0,0.0)`、空图返回 `{}`、无边仅斥力；`k=sqrt(area)*1.2`、`area≥2` 保证无除零；归一化 `hi>lo else 1.0` 防塌缩除零。`_add_field`/`_add_bookmark`（:417/:445）为标准 OXML 写法；空 `format_rules` 时 `get` 返回 None 不崩溃。文件名契约（:1558-1571）`max(1, int(current_round))` 避免 v0，`safe_id` 空时回退 `"标书"`，不产生空文件名。M10 正则 `(【待填写[：:][^】]+】)` 正确收紧。
- **N1（优化模板）已正确修复，无问题**：`optimized_prompts.py` 全量占位符经 grep 仅为 `{requirements_context}`（11 处，含 5 个 sec* 模板），无其他占位符；`get_optimized_section_prompt`（:432-435）`template.format(requirements_context=...)` 不会抛 `KeyError`，与 `section_generator.py:436` 传参键完全匹配。
- **N2（保密正则）已正确修复，无问题**：`compliance_checker.py:188-194` 手机号/身份证/银行账号均加敏感前缀锚定（`(手机|账号|卡号|身份证|...)`），普通长数字串（合同/项目编号）不再误报；`:217-223` 文本泄露用可选捕获组 `group(1)` 排除正面承诺语境，未匹配时 `group(1) is None` 安全返回；全部正则可正常编译。
- **N4（子类型回写）已正确修复，无问题**：`info_verification_gate.py:369` `"bid_subtype": corrected_fields.get("bid_subtype", state.get("bid_subtype", ""))` 正确回写顶层 state；含该键用新值、不含则保留旧值；其余字段经 `_merge_contract_sources` + `contract.to_dict()` 完整保留，未破坏其他字段回写。
- **M17（来源标注）已正确修复，无问题**：`_annotate_field_sources`（:57-136）五类来源（用户填写/招标文件/补充说明/LLM/缺失）均可达、无死分支，`bid_subtype` 单独标注，无崩溃。

---

## 二、新引入 / 仍存的高危问题（置信度 ≥80）

### 问题 1：N3 短章豁免跳过「非空校验」——空章节静默通过（回归）
- **文件:行**：`bid-agent/core/nodes/quality_checker.py:567-568`
- **证据**：
  ```python
  if section_name in FORMAT_FIXED_SHORT_CHAPTERS:
      continue   # 直接跳过，连 content 非空都不校验
  ```
  豁免集合（`ch2_authorization`/`ch7_schedule`/`ch8_after_sales`，:33-37）在字数门槛循环中被 `continue` 整段跳过；循环前半段仅对以「【生成失败」开头的占位文本跳过（:562-563），**未对豁免章节做 `if not content.strip():` 之类的非空校验**。
- **回归性质**：修复前（无豁免概念）所有章节统一走 `len(content) < min_section_chars` 判定，空章节（0 字）必被记 `completeness_issues` → verdict FAIL。修复后，若是这些格式固定型章节、且生成返回**空字符串**（非「【生成失败】」占位），会完全绕过校验，verdict 判 PASS，出现「空授权委托书/空售后服务/空进度安排」漏检。
- **置信度**：85（机制确定；空 content 触发路径确定，仅发生概率取决于上游是否返回真空而非占位）
- **严重度**：中高（政府采购标书缺授权书/售后章节属硬伤，但该类章节生成返回真空的概率中等偏低）
- **修复建议**：在 `FORMAT_FIXED_SHORT_CHAPTERS` 的 `continue` 之前补一层非空守卫，例如：
  ```python
  if section_name in FORMAT_FIXED_SHORT_CHAPTERS:
      if not content.strip():
          completeness_issues.append(f"「{section_name}」内容为空，请确认生成结果")
      continue
  ```
  即可保留门槛豁免、同时拦住真空章节，避免回归。

---

## 三、审查汇总

- 发现 ≥80 高危 bug：**1 个**（N3 豁免章节缺失非空校验，回归）。
- 其余 7 项修复（N0 / H3 / H6 / H7 / N1 / N2 / N4）+ M17 均核验通过，未引入新 bug、修复本身正确。
- 低于 80 置信度的可疑点（如 `int(current_round)` 在 `current_round` 显式为 `None` 时会 `TypeError`，属既有 state 契约风险、非本轮修复引入）已按规则丢弃，不在本报告列出。
