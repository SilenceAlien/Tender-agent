# M8 修复记录 — 页边距默认值与规范冲突

文件：`bid-agent/core/nodes/doc_assembler.py`
问题：DEFAULT_FORMAT 与页边距应用处残留「下=3.5 / 右=2.6」，与规范 ⑭「上下3.7/左右2.8」冲突。
规范：system-workflow.md 节点 ⑭「默认上下3.7cm/左右2.8cm」。

## 改动明细（仅页边距相关字面量，其余装配逻辑不动）

| 行 | 类型 | 旧值 | 新值 |
|----|------|------|------|
| 9  | 模块 docstring (T020 注释) | `上3.7/下3.5/左2.8/右2.6cm` | `上3.7/下3.7/左2.8/右2.8cm` |
| 27 | `DEFAULT_FORMAT["page_margin"]` | `上3.7cm/下3.5cm/左2.8cm/右2.6cm` | `上3.7cm/下3.7cm/左2.8cm/右2.8cm` |
| 26 | 行内 fix 注释 | `was 上3.7/下3.5/左2.8/右2.6` | `aligned bottom/right defaults with spec (上下3.7cm/左右2.8cm)` |
| 41 | `_format_margin` docstring 示例 | `上3.7cm/下3.5cm/左2.8cm/右2.6cm` | `上3.7cm/下3.7cm/左2.8cm/右2.8cm` |
| 46 | 默认值兜底 dict | `{"top":3.7,"bottom":3.5,"left":2.8,"right":2.6}` | `{"top":3.7,"bottom":3.7,"left":2.8,"right":2.8}` |

（注：行 27、46 在修复前的文件中实际已为 3.7/2.8，本次主要是清除 L9/L41 残留旧字面量及 L26 注释中的旧值，保证全文页边距字面量一致。）

## 单位与覆盖逻辑确认
- 应用处 L1167-1170：`section.{top,bottom,left,right}_margin = Cm(margins[...])`，单位 `Cm()` 一致。
- 覆盖：`_format_margin` (L45) 用 `format_rules.get("page_margin", DEFAULT_FORMAT["page_margin"])`，自定义页边距可正确覆盖默认值；H7 的连续页码/页眉页脚/Mermaid 力导向逻辑（L1119-1121 等）未改动。

## 验证结果
- `py_compile`：COMPILE_OK（无语法错误）。
- Grep `2.6|3.5`：No matches found（无旧字面量残留）。
- 全局页边距字面量均为 3.7 / 2.8。

结论：**M8 已修复并验证通过**。
