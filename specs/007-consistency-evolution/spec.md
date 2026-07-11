# 功能规范：一致性自进化系统 (Consistency Self-Evolution)

## 需求背景

### 问题

当前标书生成系统中，一致性问题（如投标保证金银行保函 vs 银行转账混用、跨章节人员数量不一致、金额不匹配等）被发现后，仅通过 `FeedbackProcessor` 转为反馈驱动的章节重生。这意味着：

1. **同一类型的一致性错误会在不同项目、不同次生成中反复出现** — 系统没有"记忆"
2. **每次修复都是单次行为** — LLM 只知道"这次要修"，不知道"以后都要避免"
3. **人工沉淀成本高** — 像投标保证金互斥问题，需要人工分析后手写到 `NO_FABRICATION_DIRECTIVE` 和 `optimized_prompts.py` 中

### 目标

构建一个自进化机制：当一致性检查器（CrossReferenceChecker / ComplianceChecker / QualityChecker）发现问题时，自动从中提取可复用的规则（经验），持久化存储，并在后续生成时注入到 prompt 中，从而避免同类问题重复发生。

### 成功标准

1. 系统能自动从一致性问题中提取结构化经验规则
2. 经验持久化到磁盘（JSON 文件），重启后仍然可用
3. 后续章节生成时，积累的经验自动注入到 prompt 中
4. 同类一致性问题在同一 bid_type 下出现频率随经验积累显著下降

## 设计

### 数据流

```
CrossReferenceChecker / ComplianceChecker / QualityChecker
    │ 发现一致性问题 (cross_ref_report / compliance_report / quality_report)
    ↓
ConsistencyLessonExtractor
    │ 从问题中提取可复用规则（LLM 辅助 / 规则模式匹配）
    ↓
ConsistencyLessonStore (data/consistency_lessons.json)
    │ 持久化存储经验，去重 + 累加出现次数
    ↓
SectionGenerator._get_section_prompt()
    │ 将相关经验注入到章节 prompt 中
    ↓
LLM 生成时遵守经验规则 → 减少同类问题
```

### 经验数据结构

```json
{
  "id": "lesson_001",
  "issue_type": "mutual_exclusion",
  "title": "投标保证金提交方式互斥",
  "description": "银行保函与银行转账/电汇是两种互斥的保证金提交方式",
  "rule": "在同一章节中只能选择一种提交方式，严禁混用。如招标文件未明确指定，输出选择说明让投标人选择。",
  "keywords": ["保证金", "保函", "转账", "电汇"],
  "applicable_sections": ["投标保证金"],
  "applicable_bid_types": [],
  "severity": "high",
  "directive_text": "投标保证金的提交方式必须前后一致...",
  "occurrence_count": 3,
  "first_seen": "2026-07-07T10:00:00Z",
  "last_seen": "2026-07-07T12:00:00Z"
}
```

### 组件

| 组件 | 文件 | 职责 |
|------|------|------|
| ConsistencyLessonStore | `core/evolution/consistency_lesson_store.py` | 持久化存储、去重、查询经验 |
| ConsistencyLessonExtractor | `core/evolution/consistency_lesson_extractor.py` | 从一致性问题提取结构化经验 |
| SectionGenerator (修改) | `core/nodes/section_generator.py` | 注入经验到 prompt |
| FeedbackProcessor (修改) | `core/nodes/feedback_processor.py` | 触发经验学习 |

### issue_type 分类

| 类型 | 来源检查器 | 示例 |
|------|-----------|------|
| `mutual_exclusion` | CrossReference / Quality | 保函 vs 转账、总价包干 vs 单价计价 |
| `number_mismatch` | CrossReference | 跨章节人员数量不一致 |
| `amount_mismatch` | CrossReference | 投标报价金额不一致 |
| `date_mismatch` | CrossReference | 关键日期不一致 |
| `document_composition_mismatch` | CrossReference | 投标函声明组成与实际章节不符 |
| `format_violation` | Compliance | 格式不合规 |
| `legal_violation` | Compliance | 绝对化用语等法律合规问题 |

## 验收场景

1. **给定** 一次生成中 CrossReferenceChecker 发现金额不一致，**当** 管道完成反馈循环，**那么** `data/consistency_lessons.json` 中新增一条 `amount_mismatch` 类型的经验

2. **给定** 经验库中已有"保证金互斥"经验，**当** SectionGenerator 生成保证金章节时，**那么** prompt 中包含该经验的 directive_text

3. **给定** 同一类型的一致性问题出现第二次，**当** 经验提取器运行，**那么** 不创建重复经验，而是递增已有经验的 occurrence_count

4. **给定** 经验库中有 3 条经验，**当** 查询所有经验用于注入，**那么** 返回按 occurrence_count 降序排列的经验列表
