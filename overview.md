# 标书 Agent 系统升级改造 — 完成报告

> 日期：2026-07-03
> 优化方案：specs/005-optimization-plan/
> 执行会话：会话 4

---

## 一、执行摘要

根据 `specs/005-optimization-plan/` 下的四份优化方案文档（spec.md、workflow-review.md、landing-pipeline-expansion.md、prompt-essay-optimization.md），对标书 Agent 进行了全面升级改造。

**管道从 7 节点扩展到 11 节点**，修复了 3 个 P0 致命漏洞，新增 4 个落地必需节点，产品成熟度从 34/100 预估提升至 70+/100。

---

## 二、完成的工作

### Phase A：紧急修复（3 个 P0 致命漏洞）

| 漏洞 | 修复方案 | 验证结果 |
|------|---------|---------|
| LLM 注入断裂（3/4 节点走 mock） | `functools.partial` 预绑定 `llm_fn` | ✅ 4 节点全部调用真实 LLM |
| 反馈循环完全失效（重试空转） | 读取 `feedback_history`，定向重生成 `target_section` | ✅ 有反馈章节被重生成 |
| 模板消费断裂（匹配结果从未被读） | `template_matcher` 写 `selected_template_id`，`section_generator` 读取 | ✅ 模板章节结构被使用 |

### Phase B：底座夯实

| 任务 | 内容 | 状态 |
|------|------|:--:|
| B1 质量检查修复 | 截断 2000→8000、非JSON→FAIL、0-100评分、完成性启发式修复 | ✅ |
| B2 优化 Prompts | 劳务管理服务类使用真实标书 8 章结构（投标函/身份证明/授权委托书/保证金/偏差表/资格审查/服务方案/应急保障） | ✅ |
| B3 bid_type 贯穿 | AgentState → SectionGenerator/TemplateMatcher/QualityChecker | ✅ |
| B4 ScoreSimulator | 新增评分模拟节点，预测得分+薄弱环节+排名 | ✅ |
| B5 工程健壮性 | req_extractor 输入截断 8000、早期失败路由、并行错误隔离 | ✅ |

### Phase C：落地管道 MVP（4 个新节点）

| 节点 | 层级 | 职责 | 状态 |
|------|:--:|------|:--:|
| EligibilityChecker | L1 决策 | 资质门槛校验，不满足→终止管道 | ✅ |
| CrossReferenceChecker | L5 校验 | 跨章节一致性（人员数/金额/日期） | ✅ |
| ComplianceChecker | L5 校验 | 格式合规+法律合规（绝对化用语/虚假业绩） | ✅ |
| ScoreSimulator | L5 校验 | 评分模拟（已在 B4 完成） | ✅ |

### Phase D：验证

- **单元+集成测试**：357 通过 / 3 失败（FAISS 预存问题）/ **0 回归**
- **端到端管道**：11 节点全部执行，劳务管理服务类标书从 PDF→DOCX 完整跑通

---

## 三、升级后的管道架构

```
DocumentParser ──[FAILED]──→ END
      │
      ↓ [OK]
ReqExtractor ──[FAILED]──→ END
      │
      ↓ [OK]
EligibilityChecker ──[FAIL:资质不满足]──→ END
      │
      ↓ [PASS/WARNING]
TemplateMatcher → SectionGenerator
      │
      ↓
QualityChecker ──[FAIL]──→ FeedbackProcessor → SectionGenerator (定向重生成)
      │ [PASS]
      ↓
CrossReferenceChecker ──[FAIL]──→ FeedbackProcessor
      │ [PASS]
      ↓
ComplianceChecker ──[FAIL]──→ FeedbackProcessor
      │ [PASS]
      ↓
ScoreSimulator → DocumentAssembler → END
```

---

## 四、新增文件

| 文件 | 用途 |
|------|------|
| `core/nodes/optimized_prompts.py` | 劳务管理服务类优化 prompts（8 章真实标书结构） |
| `core/nodes/score_simulator.py` | 评分模拟器节点 |
| `core/nodes/eligibility_checker.py` | 资质门槛校验节点 |
| `core/nodes/compliance_checker.py` | 合规审查节点 |
| `core/nodes/cross_reference_checker.py` | 跨章节一致性检查节点 |
| `data/company_quals.json` | 企业资质库模板 |

## 五、修改文件

| 文件 | 改动 |
|------|------|
| `core/graph.py` | LLM 注入 + 4 新节点注册 + 5 条件路由 |
| `core/state.py` | bid_type + 4 新状态字段 + ALL_NODES 11 节点 |
| `core/nodes/section_generator.py` | 反馈循环 + 模板消费 + bid_type + 错误隔离 |
| `core/nodes/quality_checker.py` | 3 漏洞修复 + 0-100 评分 |
| `core/nodes/req_extractor.py` | 输入长度限制 8000 |
| `core/nodes/template_matcher.py` | 写 selected_template_id |
| `gui/panels/review_panel.py` | 传 llm_fns 给 build_graph |

---

## 六、后续待办

| 优先级 | 任务 | 说明 | 状态 |
|:--:|------|------|:--:|
| P1 | 表格结构化提取 | doc_parser.py 的 `find_tables`，评分表直接解析为 JSON | ✅ 完成 |
| P1 | RAG 注入 SectionGenerator | FAISS 检索同类型范文注入 prompt | ✅ 完成 |
| P1 | HumanReviewGate 人工审核卡点 | 法律必须，法定代表人签字 | ✅ 完成 |
| P2 | PricingStrategist | 报价策略节点（当前完全缺失） | 待办 |
| P2 | evolution 模块接入 | PromptSelector 接入 SectionGenerator | 待办 |
| P2 | 多轮迭代优化 | 自动生成→评分→修订循环选最高分版本 | 待办 |

---

## 七、P1 阶段完成详情（2026-07-03 会话 5）

### P1-1: 表格结构化提取
- `doc_parser.py` 新增 `_extract_tables_pdf` / `_extract_tables_docx`，用 PyMuPDF `find_tables()` 和 python-docx 提取结构化表格
- 表格自动分类：scoring（评分表）/ qualification（资质表）/ unknown
- `req_extractor.py` 新增 `_requirements_from_tables`，直接消费表格数据，**跳过 LLM 调用**
- 表格来源的评分项标记 `source: "table"`，优先级高于 LLM 输出（表格是结构化 ground truth）
- 新增 state 字段：`extracted_tables`

### P1-2: RAG 注入 SectionGenerator
- 新增 `core/retrieval/reference_retriever.py`，支持两种检索模式：
  - 语义检索（FAISS + embedder，需要预构建索引）
  - 关键词检索（文件系统兜底，无需 API key 即可工作）
- `section_generator.py` 的 `_get_section_prompt` 自动注入检索到的范文片段
- 范文来源：`knowledge_base/{bid_type}/范文/` 目录下的 .txt 文件
- 每章节注入上限 1500 chars，避免 prompt 膨胀

### P1-3: HumanReviewGate 人工审核卡点
- 新增 `core/nodes/human_review_gate.py`，插入在 ScoreSimulator 和 DocumentAssembler 之间
- 三种状态：`pending`（等待人工）/ `approved`（通过）/ `rejected`（打回）
- 拒绝时审核意见自动转为 `feedback_history`，驱动 SectionGenerator 定向重生成
- 幂等设计：已有 verdict 不被节点覆盖，支持 GUI 二次 invoke
- `auto_approve` 参数供测试/CI 使用（生产环境禁用）
- 管道从 11 节点扩展到 12 节点

### P1 验证
- 全量测试：357 通过 / 3 失败（FAISS 预存问题）/ 0 回归
- 端到端：表格提取→需求消费→RAG 注入→人工审核→文档装配 完整跑通
