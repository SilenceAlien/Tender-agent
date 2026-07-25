# 修复记录 — M6（ComplianceChecker 格式合规过浅）

- 修复文件：`bid-agent/core/nodes/compliance_checker.py`
- 修复日期：2026-07-20
- 对应审计：cluster_C4_v2.md §1（M6 未修复）、§2 节点 ⑪
- 规范依据：system-workflow.md:422-425（节点 ⑪「格式合规检查」）

## 问题

原 `_check_format_compliance` 仅做「声明级」校验：只检查 `format_rules`
是否声明 `page_margin/font/line_spacing` 与 `seal_requirement`，未对实际
格式要素做任何检查，无 format_rules 时即静默 PASS，偏离规范 ⑪ 要求的
页边距/字体/行距/目录/页眉页脚/签章内容级检查。

## 修复内容

重写 `_check_format_compliance`（含两个解析辅助函数 `_parse_margin`、
`_has_toc`），对规范 ⑪ 要求的格式要素做最大努力检查，**不引入任何渲染依赖**：

1. **页边距**：解析 `page_margin` 的 上/下/左/右（支持 `上3.7/下3.5/左2.8/右2.6cm`
   与带空格/带 cm 单位两种写法）；解析成功则输出声明值并标 `manual_confirm`，
   无法解析或缺失则报 medium。
2. **字体**：检查声明中正文是否含「仿宋」、标题是否含「黑体/楷体」；缺仿宋报
   medium，缺标题字体报 low；并显式列出「字体需在 Word 中人工确认」。
3. **行距**：声明存在即输出值并标 `manual_confirm`（真实行距需 Word 确认）。
4. **目录（TOC）**：扫描章节名/内容是否含「目录」标题；检测到则确认，否则
   显式「目录待确认」人工确认项。
5. **页眉页脚**：若 `format_rules` 声明 `header/footer` 则核对，否则显式
   「页眉页脚待确认」人工确认项。
6. **签章要求（seal_requirement）**：缺失仍报 medium；已声明则进一步做内容级
   检查——章节文本是否提及「公章/盖章/签章」，命中则确认，未命中则提示补正文。

对无渲染时无法静态核实的项（真实字体/行距/页眉页脚/目录格式），统一以
`severity=low` + `check_status="manual_confirm"` 显式列出，避免静默误判 PASS。

## 未破坏项

- 法律合规检查（绝对化用语/虚假业绩/知识产权/保密泄露，N2/M5 已修复）保持不变。
- verdict 逻辑不变（仅 high 级 legal issue 触发 FAIL），新增格式人工确认项不阻断管道。

## 验证

```
python3 -m py_compile bid-agent/core/nodes/compliance_checker.py  # COMPILE_OK
```

功能自测（桩模块绕过 langgraph 依赖）确认：
- 声明齐全场景输出 7 项（含各 manual_confirm 项），页边距解析正确；
- 斜杠/空格/带 cm 单位三种页边距写法均可解析；
- `format_rules` 缺失时输出「格式规则缺失」medium；
- 字体声明「宋体」时正确报「正文字体未声明仿宋」medium；
- TOC 检测对含「目录」章节返回 True、普通文本返回 False。

## 状态

✅ M6 已修复（务实内容级检查 + 显式人工确认项，无渲染依赖）。
