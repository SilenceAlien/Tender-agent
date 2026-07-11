# 标书 Agent 落地管道扩展方案

> 版本：v1.0
> 日期：2026-07-03
> 前置文档：`workflow-review.md`（工作流漏洞评审）
> 本文回答："如果这个 agent 准备落地，当前工作流是否过于简单？需要新增哪些管道节点？"

---

## 一、核心结论

**当前工作流对"原型演示"够用，对"真正交付用户生产标书"严重不足。**

当前 7 节点单线管道（解析 → 提取 → 模板 → 生成 → 质检 → 反馈 → 装配）的本质是"把 PDF 变成 DOCX"的格式转换器，而非"制作一份能中标的标书"的生产系统。两者的差距在于：

| 维度 | 当前管道 | 落地需要 |
|------|---------|---------|
| 管道形态 | 一条直线走到底 | 带决策门禁的分层管道 |
| 失败处理 | 全程无终止，硬撑到 END | Go/No-Go 门禁，不满足即终止 |
| 质量保障 | 单节点自检 | 三道关：内容质量 + 跨章节一致性 + 合规 |
| 人工介入 | 无 | 必须有审核卡点（法律要求） |
| 报价 | 完全缺失 | 报价是标书核心 |
| 废标风险防护 | 无 | 资质门槛校验必须前置 |

**一句话**：当前管道是"生成器"，落地需要"生产链"。

---

## 二、缺失环节全景（10 项）

对照真实标书生产全流程，当前管道覆盖 4-5 个环节，缺失 10 个。按"废标风险 / 中标影响 / 法律必须"三维度评分（每维 0-10，总分 30）：

| # | 缺失环节 | 废标风险 | 中标影响 | 法律必须 | 总分 | 优先级 | 所属层 |
|---|---------|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | 资质门槛校验 (EligibilityChecker) | 10 | 3 | 6 | 19 | **P0** | L1 决策层 |
| 2 | 法律合规审查 (ComplianceChecker) | 9 | 2 | 10 | 21 | **P0** | L5 校验层 |
| 3 | 人工审核卡点 (HumanReviewGate) | 7 | 3 | 10 | 20 | **P0** | L6 人工层 |
| 4 | 格式深度合规 (ComplianceChecker 内) | 8 | 5 | 4 | 17 | **P0** | L5 校验层 |
| 5 | 跨章节一致性 (CrossReferenceChecker) | 4 | 8 | 2 | 14 | **P1** | L5 校验层 |
| 6 | 报价策略 (PricingStrategist) | 3 | 9 | 2 | 14 | **P1** | L3 策略层 |
| 7 | 评分自检 (ScoreSimulator) | 2 | 9 | 1 | 12 | **P1** | L5 校验层 |
| 8 | 竞争对手分析 (CompetitiveAnalyzer) | 1 | 7 | 1 | 9 | P2 | L2 需求层 |
| 9 | 电子签章封装 (SealingExporter) | 5 | 1 | 8 | 14 | P2 | L7 封装层 |
| 10 | 版本与协作 | 1 | 4 | 3 | 8 | P2 | 横切 |

**评分依据**：
- **废标风险**：不满足会导致整份标书被废（资质不符、格式违规、虚假业绩、未签字盖章）
- **中标影响**：影响评审打分（内容质量、报价竞争力、差异化）
- **法律必须**：法规强制要求（招标投标法、政府采购法对签章、真实性、保密的要求）

---

## 三、落地版分层管道设计（8 层）

### L0 入库层（现有 + 可选新增）

| 节点 | 状态 | 职责 |
|------|:--:|------|
| DocumentParser | 现有 | PDF/DOCX 解析（需加表格结构化） |
| AnnouncementFetcher | 新增(可选) | 从政府采购网抓取公告 |

### L1 决策层（新增，落地关键）

**这是当前管道完全缺失、但落地最关键的层。**

| 节点 | 状态 | 职责 | 门禁 |
|------|:--:|------|------|
| EligibilityChecker | **新增(P0)** | 校验企业资质是否满足招标门槛 | 不满足 → 终止，避免浪费生成成本 |
| RiskAssessor | 新增(P1) | 评估废标风险、竞争激烈度、历史中标概率 | 高风险 → 提示用户，由用户决策 |

**为什么关键**：当前管道无论资质是否满足都会生成 8 章内容，这是巨大的浪费。企业最痛的决策点是"这个标值不值得投"——资质差一项就是废标，生成再多内容也白费。

### L2 需求层（现有 + 新增）

| 节点 | 状态 | 职责 |
|------|:--:|------|
| ReqExtractor | 现有(强化) | 提取评分项、技术规格、资质要求（需加表格结构化） |
| TemplateMatcher | 现有(需接通) | FAISS 检索历史模板 |
| CompetitiveAnalyzer | 新增(P2) | 分析竞争对手可能方案，为差异化提供输入 |

### L3 策略层（新增）

**当前管道从需求直接跳到生成，缺少"怎么打"的策略层。**

| 节点 | 状态 | 职责 |
|------|:--:|------|
| PricingStrategist | 新增(P1) | 报价策略（成本核算 + 竞争定价 + 资金占用） |
| DifferentiationPlanner | 新增(P2) | 差异化定位（基于竞争分析确定优势卖点） |

### L4 生成层（现有，强化）

| 节点 | 状态 | 职责 |
|------|:--:|------|
| SectionGenerator | 现有(强化) | 章节生成（+RAG 注入 +Prompt 进化 +定向修订） |

### L5 校验层（现有 + 2 新增，三道关）

**当前只有 QualityChecker 一道关，且存在严重缺陷（见 workflow-review.md）。落地需要三道关。**

| 节点 | 状态 | 职责 |
|------|:--:|------|
| QualityChecker | 现有(强化) | 内容质量：评分项覆盖、完整性、逻辑性 |
| CrossReferenceChecker | **新增(P1)** | 跨章节一致性：人员数量、技术参数、时间节点跨章一致 |
| ComplianceChecker | **新增(P0)** | 格式合规 + 法律合规：页边距/字体/签章 + 绝对化用语/虚假业绩/知识产权 |
| ScoreSimulator | 新增(P1) | 评分模拟：模拟评审打分，给出预测得分 |

### L6 人工层（新增，法律必须）

| 节点 | 状态 | 职责 |
|------|:--:|------|
| HumanReviewGate | **新增(P0)** | 人工审核卡点：法定代表人签字、法务审核、财务确认 |

**为什么必须**：招标投标法第二十七条规定投标文件需由法定代表人或授权代理人签字并加盖公章。完全自动化在法律层面不可接受——即使内容是 AI 生成的，最终责任必须由人承担。

### L7 封装层（现有 + 新增）

| 节点 | 状态 | 职责 |
|------|:--:|------|
| DocumentAssembler | 现有(强化) | DOCX 装配（强化格式深度合规） |
| SealingExporter | 新增(P2) | 电子签章 + 加密 PDF + 投递封装 |

---

## 四、新增节点详细设计

### 4.1 EligibilityChecker（P0，资质门槛校验）

**位置**：L1 决策层，DocumentParser 之后、ReqExtractor 之前或之后

**输入**：`state.requirements.qualifications`（招标要求的资质列表）+ 企业资质库

**输出**：
```python
{
    "eligibility_report": {
        "verdict": "PASS" | "FAIL" | "WARNING",
        "missing_quals": ["ISO27001", "CMMI3"],  # 缺失的资质
        "matched_quals": [...],
        "risk_level": "high" | "medium" | "low",
    },
    "node_status": {"EligibilityChecker": "completed"}
}
```

**门禁逻辑**：
- `verdict == FAIL`（资质不满足硬门槛）→ 路由到 END，终止管道
- `verdict == WARNING`（部分资质缺失但有替代方案）→ 暂停，等待用户确认
- `verdict == PASS` → 继续

**实现要点**：需要一个企业资质库（本地 JSON 或数据库），记录企业拥有的所有资质证书。

### 4.2 CrossReferenceChecker（P1，跨章节一致性）

**位置**：L5 校验层，QualityChecker 之后

**输入**：`state.sections`（全部章节内容）

**输出**：
```python
{
    "cross_ref_report": {
        "verdict": "PASS" | "FAIL",
        "inconsistencies": [
            {
                "type": "number_mismatch",
                "field": "项目人员数量",
                "ch5_says": "15人",
                "ch3_says": "12人",
                "severity": "high",
            }
        ],
    }
}
```

**检查项**：
- 人员数量跨章节一致（人员配置章 vs 服务方案章）
- 技术参数跨章节一致（技术方案章 vs 服务方案章）
- 时间节点跨章节一致（实施计划章 vs 服务方案章）
- 金额跨章节一致（报价章 vs 其他章）

**为什么重要**：评审专家一眼能看出"人员配置章说 15 人，服务方案章说 12 人"，这会扣分。当前 QualityChecker 只做单章检查，发现不了这类问题。

### 4.3 ComplianceChecker（P0，格式 + 法律合规）

**位置**：L5 校验层，CrossReferenceChecker 之后

**输入**：`state.sections` + `state.requirements.format_rules`

**输出**：
```python
{
    "compliance_report": {
        "verdict": "PASS" | "FAIL",
        "format_issues": [
            {"item": "页边距", "required": "上3.7cm", "actual": "上3.0cm", "severity": "high"}
        ],
        "legal_issues": [
            {"item": "绝对化用语", "text": "全国第一", "section": "ch3", "severity": "high"},
            {"item": "虚假业绩", "text": "未经验证的项目", "section": "ch6", "severity": "high"},
        ],
    }
}
```

**检查项**：
- **格式合规**：页边距、字体、行距、目录格式、附件顺序、页眉页脚
- **法律合规**：
  - 绝对化用语（"全国第一"、"唯一"、"最佳"）→ 违反广告法
  - 虚假业绩（无对应合同的项目）→ 违反招标投标法
  - 知识产权（未授权使用他人案例/图片）
  - 保密信息泄露（客户名称未脱敏）

**为什么 P0**：格式不合规直接废标；绝对化用语可能被举报导致中标后取消资格。

### 4.4 HumanReviewGate（P0，人工审核卡点）

**位置**：L6 人工层，校验层全部 PASS 之后、封装之前

**输入**：`state.sections` + 校验报告

**行为**：暂停管道，将内容推送到人工审核界面（Streamlit review_panel 已存在），等待人工确认。

**输出**：
```python
{
    "review_status": "pending" | "approved" | "rejected" | "modified",
    "reviewer": "张三",
    "review_comments": [...],
    "modified_sections": {...},  # 人工修改后的章节
}
```

**门禁逻辑**：
- `approved` → 继续 DocumentAssembler
- `rejected` → 返回 SectionGenerator，带审核意见
- `modified` → 用修改后的章节覆盖，继续

**为什么必须**：法律层面，投标文件需由法定代表人或授权代理人签字。即使 AI 生成内容，最终责任由人承担。

### 4.5 PricingStrategist（P1，报价策略）

**位置**：L3 策略层

**输入**：`state.requirements`（预算、控制价）+ 企业成本数据

**输出**：
```python
{
    "pricing_strategy": {
        "cost_estimate": 850000,  # 成本核算
        "proposed_price": 920000,  # 建议报价
        "profit_margin": 0.082,    # 利润率
        "strategy": "中等偏上",     # 报价策略
        "rationale": "...",         # 策略依据
    }
}
```

**为什么 P1**：报价是中标关键。当前管道完全不含报价，意味着生成的标书缺了最核心的一章。

---

## 五、落地 MVP 路线图

不是所有 10 个缺失环节都要在 MVP 实现。按"能不能交付用户"的最低门槛，MVP 必须包含：

### MVP 必须（P0，4 个新增节点）

| 阶段 | 节点 | 预估工时 | 依赖 |
|------|------|:--:|------|
| 紧急修复（前置） | LLM 注入 + 反馈循环 + 模板消费接通 | 2 天 | 无 |
| MVP-1 | EligibilityChecker | 2 天 | 企业资质库 |
| MVP-2 | ComplianceChecker（格式+法律） | 3 天 | 规则库 |
| MVP-3 | HumanReviewGate | 2 天 | review_panel 已有 |
| MVP-4 | CrossReferenceChecker | 2 天 | 一致性规则 |

**MVP 工时**：约 11 天（含紧急修复）

**MVP 完成后的管道**：
```
DocumentParser → ReqExtractor → EligibilityChecker ──[FAIL]──→ END
                                      │
                                  [PASS]
                                      ↓
                            TemplateMatcher → SectionGenerator
                                      ↓
                            QualityChecker ──[FAIL]──→ FeedbackProcessor → SectionGenerator
                                  │ [PASS]
                                  ↓
                       CrossReferenceChecker ──[FAIL]──→ FeedbackProcessor
                                  │ [PASS]
                                  ↓
                       ComplianceChecker ──[FAIL]──→ FeedbackProcessor
                                  │ [PASS]
                                  ↓
                            HumanReviewGate ──[REJECT]──→ SectionGenerator
                                  │ [APPROVE]
                                  ↓
                            DocumentAssembler → END
```

### 落地核心（P1，后续迭代）

| 节点 | 预估工时 |
|------|:--:|
| PricingStrategist | 3 天 |
| ScoreSimulator | 2 天 |
| RiskAssessor | 2 天 |

### 增强项（P2，远期）

| 节点 | 预估工时 |
|------|:--:|
| CompetitiveAnalyzer | 3 天 |
| DifferentiationPlanner | 2 天 |
| SealingExporter | 4 天（依赖电子签章 SDK） |
| 版本与协作 | 5 天（架构改造大） |

---

## 六、管道流程优化建议

除了新增节点，现有管道的流程也需要调整：

### 6.1 决策门禁化（最重要）

当前管道是"线性流"，任何节点失败都硬撑到 END。落地需要"门禁化"——在关键节点设置 Go/No-Go 决策点：

| 门禁位置 | 判断条件 | FAIL 路由 |
|---------|---------|----------|
| DocumentParser 之后 | 至少 1 文件解析成功 | END（错误终止） |
| EligibilityChecker 之后 | 资质满足硬门槛 | END（终止，避免浪费） |
| ComplianceChecker 之后 | 无致命合规问题 | 返回修改 |
| HumanReviewGate 之后 | 人工批准 | 返回修改 |

### 6.2 校验层并行化

当前 QualityChecker 是单节点。落地版 L5 有 4 个校验节点（QualityChecker / CrossReferenceChecker / ComplianceChecker / ScoreSimulator），可以并行执行后汇总结果，提升效率。

### 6.3 反馈循环分层

当前反馈循环只有一层（FAIL → 整章重生成）。落地需要分层：

| 问题类型 | 反馈路由 | 修复方式 |
|---------|---------|---------|
| 内容缺失（评分项未覆盖） | SectionGenerator | 章节内追加段落 |
| 跨章节不一致 | SectionGenerator（多章） | 定向修改矛盾数据 |
| 格式不合规 | DocumentAssembler | 格式参数调整（不重新生成内容） |
| 法律合规问题 | SectionGenerator | 替换违规表述 |

### 6.4 状态扩展

AgentState 需要新增字段：

```python
class AgentState(TypedDict):
    # ... 现有字段 ...

    # 新增：决策层
    eligibility_report: dict
    risk_assessment: dict

    # 新增：策略层
    pricing_strategy: dict
    differentiation_plan: dict

    # 新增：校验层
    cross_ref_report: dict
    compliance_report: dict
    score_simulation: dict

    # 新增：人工层
    review_status: str
    reviewer: str
    review_comments: list[dict]
```

---

## 七、与 spec.md 优化方案的关系

本文是 `spec.md` 的**架构层补充**，不是替代：

| 文档 | 关注点 | 关系 |
|------|--------|------|
| `spec.md` | 现有 7 节点的深化优化 | 纵向深化 |
| `workflow-review.md` | 现有 7 节点的漏洞诊断 | 质量审查 |
| **本文** | **管道结构的横向扩展** | **架构补全** |

执行顺序建议：

```
1. 紧急修复（workflow-review.md 提出的 3 个 P0）
2. spec.md 阶段 1（深化现有节点）
3. 本文 MVP-1~4（新增 4 个 P0 节点）  ← 与 spec.md 阶段 1 可并行
4. spec.md 阶段 2（定向修订、人工审核卡点）  ← 本文 HumanReviewGate 已覆盖
5. 本文 P1（报价、评分模拟、风险评估）
6. spec.md 阶段 3 + 本文 P2（远期增强）
```

---

## 八、落地阻碍清单

即使实现了所有节点，落地还有几个非技术阻碍：

| 阻碍 | 性质 | 应对 |
|------|------|------|
| 企业资质库缺失 | 数据 | 需要企业录入资质证书数据 |
| 历史业绩库缺失 | 数据 | 需要企业录入真实业绩（脱敏后） |
| 竞争对手情报获取 | 数据 | 公开中标公告 + 人工补充 |
| 电子签章 SDK | 技术 | 需采购第三方电子签章服务 |
| 法务规则库 | 知识 | 需法务团队定义合规规则 |
| 评审专家评分模型 | 知识 | 需收集历史评分数据训练 |

**最大的非技术阻碍是数据**——AI 能生成内容，但无法凭空创造企业的真实资质和业绩。落地前需要先完成数据准备。

---

## 九、总结

**当前工作流对原型演示够用，对落地生产严重不足。**

核心差距不在"节点深度"（spec.md 已规划深化），而在"管道结构"——缺少决策门禁、人工卡点、跨章节校验、合规审查。这些是"能不能交付用户"的硬门槛，不是"做得好不好"的优化项。

落地路径：先修复 workflow-review.md 的 3 个 P0 断裂（2 天），再深化 spec.md 的现有节点（5 天），同时新增本文 4 个 MVP 节点（9 天），合计约 16 天可达"可落地 MVP"。之后逐步补齐 P1（7 天）和 P2（14 天），约 5 周达"成熟落地版"。
