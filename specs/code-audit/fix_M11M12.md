# 修复记录：M11 单字关键词过度命中 & M12 去重偏离规范

> 日期：2026-07-20
> 修改文件（仅以下两处）：
> - `bid-agent/core/evolution/consistency_lesson_store.py`
> - `bid-agent/core/evolution/consistency_lesson_extractor.py`（未改动，仅随编译校验）

## M11（中危）：单字关键词过度命中 — 已修复

**根因**：上一轮 M11 修复引入 `get_lessons_for_section` 关键词兜底（`kw in section_name`），
而模板/LLM 关键词含大量单字（「人」「元」「名」「台」「辆」「¥」「最」等），做子串匹配
几乎每段都命中，经验过注入（N1）。

**修复**（集中在 store.py 单点）：
1. 新增模块级 `_is_valid_keyword()`：纯 ASCII 字母/数字长度 ≥ 3，含中文/混合长度 ≥ 2，单字一律丢弃。
2. `add_lesson` 中 `keyword_set = {kw for kw in keywords if _is_valid_keyword(kw)}`，
   在存储/合并唯一切口过滤，覆盖模板与 LLM 两条提取路径。
3. `get_lessons_for_section` 关键词兜底增加 `len(kw) >= 2` 守卫，单字永不造成章节误命中。

**未破坏**：H11 类型映射、`applicable_sections` 字面匹配、occurrence_count 合并逻辑均保留。

## M12（中危）：去重偏离规范 — 已修复

**根因**：`_find_similar` 存在额外分支 `if sim >= 0.6: return lesson`，
关键词 Jaccard≥0.6 时即使无章节重叠也合并，违背 §4.3「Jaccard + 章节重叠」AND 要求，
会误并不同章节的不同问题。

**修复**：删除该分支，合并严格为 `sim >= _SIMILARITY_THRESHOLD(0.3) AND section_overlap`；
纯相似但章节不重叠的经验保持独立。

## 验证

- `py_compile` 两文件：通过，无语法错误。
- 脚本验证（用临时文件，未留痕）：
  - M11：单字「人/元/名/台」被过滤，保留「不一致/人员数量」；英文 `ab` 无效、`abc` 有效、`¥` 无效。
  - M12：同 issue_type、相同关键词模板但不同章节（投标函 vs 分项报价表）两条经验保持独立（count=3）；
    同章节相似经验正确合并（count 不增）。
- 结论：两项均修复，经验可正常存储、查询、合并。
