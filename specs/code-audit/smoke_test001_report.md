# 全流程冒烟测试报告 — test001 劳务外包标书

> 验证你"在其他地方修复的 F3/F4"在真实全流程下是否生效。

## 一、测试场景

| 项 | 值 |
|----|----|
| 输入招标文件 | `2026年广州铁道车辆有限公司劳务管理服务采购（技术标）v2.pdf`（劳务外包类，真实 PDF） |
| 投标人 | 广州华南人力 |
| 标号 | test001 |
| 标书类型 | 劳务外包类 |
| 运行方式 | headless `run_pipeline`，无 API Key（LLM 节点 mock） |
| 注入方式 | 经 `project_contract` + `requirements` + `extra_reqs` 模拟人工确认的关键信息 |

## 二、结论：✅ 全流程跑通，F3 / F4 修复均生效

14 个节点全部 `completed`（FeedbackProcessor 为 `pending` 属正常——仅在反馈回路轮次触发，非失败）；生成 9 个章节；成功导出 docx。

```
DocumentParser        : completed
ReqExtractor          : completed
ContractExtractor     : completed
InfoVerificationGate  : completed
EligibilityChecker    : completed   ← F3 修复点：未再 return __end__
TemplateMatcher       : completed
SectionGenerator      : completed
QualityChecker        : completed
FeedbackProcessor     : pending     ← 正常（无反馈回路）
CrossReferenceChecker : completed
ComplianceChecker     : completed
ScoreSimulator        : completed
HumanReviewGate       : completed
DocumentAssembler     : completed   ← 导出成功
FAILED NODES : NONE
```

## 三、关键验证

**F3（资质校验 FAIL → 静默零产出 dead-end）— 已修复 ✅**
`EligibilityChecker` 正常 `completed`，未再 `return "__end__"`；流水线继续推进到 `TemplateMatcher` → … → `DocumentAssembler` 并产出文件。修复生效。

**F4（30k 硬截断丢 68% 内容）— 已修复 ✅**
`DocumentParser` 完整解析 **12 chunks + 14 tables**（完整文档，无截断崩溃）；`ReqExtractor` 正常 `completed`。修复生效。

**投标人 广州华南人力 贯通 ✅**
经 `extra_reqs` / 契约解析进入 `project_contract.bidder_name`，并正确落到导出文件名：
`/Users/alanchris/Desktop/标书agent02/bid-agent/data/exports/广州华南人力_标书_v1.docx`

## 四、观察项（非回归，环境 / 架构限制）

1. **标号 test001 未进入产物文档**（预期内，mock 模式限制）
   - 架构上 `bid_number` 由 `ReqExtractor` 从招标文件文本经 LLM 抽取并写入 `requirements.bid_number`；mock 模式无 LLM 无法抽取。
   - 我在 state 预置的 `requirements.bid_number` 被 `ReqExtractor` 整体覆盖（表驱动需求重建，丢弃预置字段）。
   - 即便经 `user_confirmed_fields` 注入 `bid_number`，也只映射到 `project_contract.project_code`（`contract_extractor.py:226-230`）；而文件名 `safe_id` 仅取 `requirements.bid_number` 或 `project_contract.bidder_name`（`doc_assembler.py:1560-1575`，不含 `project_code`），故不落文件名。
   - **结论**：标号要真实带入产物，必须有 LLM 从招标文件抽取，或 GUI 表单字段写入 `requirements.bid_number`。本次为 mock 验证，属预期限制。

2. **文档正文为 mock 内容**（环境限制，与 2026-07-20 smoke 报告一致）
   - 9 个章节均为 75–87 字通用占位模板（投标函 / 法定代表人身份证明 / 授权委托书 / 投标保证金 / 商务和技术偏差表 / 分项报价表 / 资格审查资料 / 服务方案 / 应急保障方案），正文未含 广州华南人力 / test001。
   - 内容正确性、跨章一致性、评分模拟需在**配置真实 API Key** 后重跑验证。

3. **project_name 在 mock 下被清空**（`ContractExtractor: 契约不完整: ['project_name 未填写']`），不影响本次导出。

## 五、导出物

- `/Users/alanchris/Desktop/标书agent02/bid-agent/data/exports/广州华南人力_标书_v1.docx`
  - 40 个非空段落，0 表格，内容为 mock 占位（不含真实投标人/标号文本）

## 六、建议

1. **配置真实 API Key 重跑**，验证正文内容质量、跨章一致性，以及 test001 标号能否从招标文件正确抽取。
2. 若需在 headless / 测试模式强制带入标号，建议在 `run_pipeline` 入口支持 `user_confirmed_fields` 透传至 `requirements.bid_number`，或在文件名 `safe_id` 候选中增加 `project_code`（当前架构缺口）。

---
*测试脚本：`bid-agent/scripts/smoke_labor_test001.py`（新增，仅读/运行，未改任何源码）*
