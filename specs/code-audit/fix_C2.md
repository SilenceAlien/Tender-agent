# 修复记录 — 簇 C2 节点④ 来源标注与子类型回写断链（N4 / M17 清理）

- 修复文件：`bid-agent/core/nodes/info_verification_gate.py`（唯一改动文件）
- 对照：`specs/code-audit/cluster_C2_v2.md`（N1/N2/N3/N4）、规范 `system-workflow.md` 节点④
- 验证：`py_compile` 通过；桩化临时脚本验证 N4 回写 + 五类来源均可达（测后删除）

## N4（中，回归）— `apply_user_corrections` 子类型回写断链  ✅ 已修复

**问题**：表单含 `bid_subtype`，`corrected_fields` 含该键，但函数只写入 `user_confirmed_fields`，未回写顶层 `state["bid_subtype"]`；下游 TemplateMatcher/SectionGenerator 读 `state.bid_subtype` 拿到编辑前旧值，子类型分区检索失效。

**修复**（`:349` 返回块）：新增
```python
"bid_subtype": corrected_fields.get("bid_subtype", state.get("bid_subtype", "")),
```
- 修正值含 `bid_subtype` → 回写新值；
- 未含该键（用户未改） → 保留编辑前现有值，不破坏既有状态。

**验证**：传入 `{"bid_subtype":"安保外包"}` → 返回 `bid_subtype=="安保外包"`；不传该键 → 保留原 `"保洁外包"`。

## M17 清理（中，上轮遗留）— 来源类别死分支/歧义  ✅ 已修复

重构 `_annotate_field_sources`（`:57`）：

1. **N3 死分支**（原 `:114` `elif value: ... and not value`，与 `elif value:` 互斥、恒不可达）→ 删除，原次级 LLM 判定并入主循环第 3 步 `elif field_name in contract_secondary and contract_secondary[field_name]: sources[...]="LLM"`，现已可达（验证：`bid_number` 仅 `project_code` 有值时标 `LLM`）。
2. **N1 死代码**：原 `else` 分支注释「补充说明」却赋值 `"LLM"`，致「补充说明」永不出产 → 修正为合同补全/合并字段（bidder_name、project_location、industry、duration、warranty、service_target）标 `"补充说明"`；GUI `_SOURCE_COLORS["补充说明"]`（orange）恢复为有效映射。
3. **N2 缺标注**：`bid_subtype` 未进入 `all_fields` 遍历 → 单独标注块：用户确认→`用户填写`；系统/LLM 判定（有值）→`LLM`；否则→`缺失`。修复后前端不再恒显「缺失」。

**五类来源现均可真实产出、无死分支**：`用户填写` / `招标文件`（req 字段）/ `补充说明`（合同合并字段）/ `LLM`（次级源 + bid_subtype 系统判定）/ `缺失`。

## py_compile 结果
```
python3 -m py_compile .../info_verification_gate.py  →  COMPILE_OK（无语法错误）
```

## 未触及（超出本任务范围，见 cluster_C2_v2.md）
- M2（节点⑥未消费 bid_subtype / 无分区权重表）、M3（WARNING 仅库空触发）：属节点⑤⑥，不在本修复文件内。
