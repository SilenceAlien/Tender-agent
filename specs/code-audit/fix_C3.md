# 修复记录 — C3 簇 N1 回归（优化模板路径评分对齐）

> 修复对象：`bid-agent/core/nodes/optimized_prompts.py`、`bid-agent/core/nodes/section_generator.py`
> 对应审计：`specs/code-audit/cluster_C3_v2.md` §4 N1
> 修复日期：2026-07-20

## 问题
劳务外包「优化路径」5 章模板（sec1_bid_letter / sec2_legal_rep / sec3_authorization /
sec4_deposit / sec5b_price_table）缺少 `{requirements_context}` 占位符。
调用链 `section_generator.py:436` → `get_optimized_section_prompt(key, requirements_context)`
（`optimized_prompts.py:417-420`）内部执行 `template.format(requirements_context=...)`，
对无占位符模板会**静默丢弃**该参数，导致这 5 章 prompt 完全不含评分要求，违反规范 ⑦「每章包含评分要求」。
属上一轮修 M7 时只改通用模板、漏改优化模板的回归。

## 修复动作（仅改 optimized_prompts.py）
在以下 5 个优化模板的结尾「请直接输出…」前，补入与既有优化模板一致的评分注入块：
```
评分要求：
{requirements_context}
```
- `sec1_bid_letter`（投标函）
- `sec2_legal_rep`（法定代表人身份证明）
- `sec3_authorization`（授权委托书）
- `sec4_deposit`（投标保证金）
- `sec5b_price_table`（分项报价表）

其余占位符（`{contract_context}` 等）未改动；`section_generator.py` 无需修改。

## 传参确认
`section_generator.py:436` 已正确传入 `requirements_context` 作 format 参数，
`get_optimized_section_prompt` 已用 `.format(requirements_context=requirements_context)` 渲染，传参完整。

## 验证
- `py_compile` 两个文件：通过（无语法错误）。
- 临时脚本：9 章优化模板全部含 `{requirements_context}`，构造 dummy 参数 `get_optimized_section_prompt` 渲染全部成功，无 `KeyError`。脚本已删除。

## 结论
N1 已修复；传参本来已具备，无需补；py_compile 通过。
