# 标书 Agent 工作流评审报告

> 评审版本：spec.md v1.0 的补充评审
> 评审日期：2026-07-03
> 评审范围：bid-agent/ 实际代码 + specs/005-optimization-plan/spec.md 优化方案
> 评审方法：逐文件代码审查 + 数据流追踪 + 反馈循环验证

---

## 一、评审结论概述

**综合评分：34 / 100**

| 维度 | 满分 | 当前 | 说明 |
|------|:--:|:--:|------|
| 架构合理性 | 20 | 8 | 骨架清晰但 LLM 注入机制断裂 |
| 数据流完整性 | 20 | 5 | 3 处数据断裂，模板匹配结果从未被消费 |
| 质量保障机制 | 20 | 6 | 截断检查 + 静默 PASS，启发式过宽 |
| 反馈循环有效性 | 15 | 2 | **致命**：反馈输出从未被 SectionGenerator 读取 |
| 可扩展性 | 10 | 7 | evolution 模块存在但未接入 |
| 工程健壮性 | 15 | 6 | 无错误隔离、无输入长度限制、无早期失败路由 |
| **合计** | **100** | **34** | spec.md 自评 37 分略乐观 |

**与 spec.md 自评的差异**：spec.md 给出 37 分，本次评审给出 34 分。差异主要在「反馈循环有效性」维度——spec.md 识别了"反馈循环弱"（整章重写而非定向修订），但未发现更严重的问题：**反馈循环实际上是断的**，SectionGenerator 根本不消费 feedback_history。这是比 spec.md 描述更致命的漏洞。

---

## 二、spec.md 已识别问题确认

spec.md 在「需求背景」中识别了 5 个核心短板，经代码验证全部属实：

| # | spec.md 识别 | 代码验证 | 位置 |
|---|--------------|---------|------|
| 1 | 知识库数据为空 | ✅ 属实，progress.md 显示已构建 54 文件修复 | knowledge_base/ |
| 2 | Prompt 写死无适配 | ✅ 属实，`_SECTION_PROMPTS` 是静态字典 | section_generator.py:38-135 |
| 3 | 质量检查粗糙 | ✅ 属实，`content[:2000]` 截断 + 非 JSON 返回 PASS | quality_checker.py:160,183 |
| 4 | 无评分模拟 | ✅ 属实，无 ScoreSimulator 节点 | graph.py |
| 5 | 反馈循环弱 | ✅ 属实，但严重程度被低估（见下方漏洞 3） | feedback_processor.py |

**spec.md 的诊断方向正确，但深度不足。** 以下 9 个漏洞是 spec.md 完全未提及的。

---

## 三、新发现漏洞（spec.md 未覆盖）

### 🔴 P0 致命漏洞

#### 漏洞 1：LLM 函数注入机制断裂

**现象**：`pipeline_runner.py` 的 `build_llm_for_pipeline()` 返回 `dict[str, Callable]`，为 4 个节点构建了 LLM 函数。但 `graph.py` 的 `build_graph()` 通过 `graph.add_node("ReqExtractor", req_extractor)` 直接绑定函数引用，**LangGraph 的 add_node 只接受 `(state) -> dict` 签名，无法传入 llm_fn 参数**。

**后果**：
- ReqExtractor、QualityChecker、FeedbackProcessor 的 `llm_fn` 永远是 `None`
- 只有 SectionGenerator 通过 `get_pipeline_llm()` 全局变量勉强工作
- 其他 3 个节点全部走 mock，实际管道只有 1/4 节点真正调用 LLM

**证据**：
```python
# graph.py:26 — 直接绑定函数引用，无参数注入机制
from core.nodes.section_generator import generate_all_sections_parallel as section_generator
graph.add_node("SectionGenerator", section_generator)

# pipeline_runner.py:85-105 — 构建了 dict 但无处传入
llm_fns: dict[str, Callable] = {}
for node in nodes:
    llm_fns[node] = _make_llm_fn(llm)  # ← 这个 dict 从未被 graph 消费
```

**修复方向**：使用 LangGraph 的 `RunnableConfig` 机制，或通过 `functools.partial` 预绑定 llm_fn 后再 add_node。

---

#### 漏洞 2：反馈循环完全失效（最严重）

**现象**：FeedbackProcessor 输出了结构化的 `target_section`、`target_paragraph_index`、`action` 字段，但这些信息写入 `feedback_history` 后，**SectionGenerator 从未读取 feedback_history**。

**后果**：
- `generate_all_sections_parallel` 中 `if name in existing and existing[name]: return name, existing[name]` 会跳过已生成章节
- 因此 FAIL → FeedbackProcessor → SectionGenerator 循环只是空转，章节内容不变
- 最终 `route_after_quality_check` 在 `current_round >= max_rounds` 时强制 PASS
- **质量问题永远未被修复，只是被"耗尽重试次数"后放过**

**证据**：
```python
# section_generator.py:292-293 — 跳过已生成章节
if name in existing and existing[name]:
    return name, existing[name]  # ← 永远返回旧内容

# section_generator.py 整个文件 — 从未读取 state["feedback_history"]
# feedback_processor.py:129-139 — 输出的 target_section 等字段无人消费
```

**这是当前工作流最致命的问题。** spec.md 阶段 2.1 提出的"定向修订替代整章重写"方向正确，但应提升为 P0——因为现在连"整章重写"都没有真正发生。

---

#### 漏洞 3：TemplateMatcher 输出从未被消费

**现象**：TemplateMatcher 输出 `matched_templates`（template_id 列表），AgentState 有 `selected_template_id` 字段。但：
- SectionGenerator 完全不读取这两个字段，只用硬编码的 `DEFAULT_SECTIONS`
- `selected_template_id` 从未被任何节点设置（字段始终为空字符串）

**后果**：模板匹配这一整步是无效计算，FAISS 检索的结果从未影响生成内容。

**证据**：
```python
# section_generator.py — 无任何对 matched_templates / selected_template_id 的引用
# template_matcher.py:101 — 只设置 matched_templates，不设置 selected_template_id
# state.py:76 — selected_template_id 字段定义了但无人写入
```

---

### 🟠 P1 严重漏洞

#### 漏洞 4：质量检查完成性启发式过宽

**现象**：`_check_completeness` 的逻辑是"只要有任意章节 > 100 字，且章节数 ≥ 评分项数，就认为所有评分项都被覆盖"。

**后果**：8 章生成后，即使内容完全跑题，只要章节数够，完成性检查也会通过。

**证据**：
```python
# quality_checker.py:46-52
has_content = any(len(c) > 100 for c in sections.values())
found = found_explicit or (has_content and len(sections) >= len(scoring_items))
```

---

#### 漏洞 5：evolution 模块孤立未接入

**现象**：`core/evolution/` 目录有完整的 `prompt_registry.py`、`prompt_selector.py`、`metrics_tracker.py`，但 SectionGenerator 从未调用 PromptSelector。

**后果**：自进化机制是"建好了但没通电"。spec.md 的 AGENTS.md 强调"自进化机制"，但代码层面未实现接入。

---

#### 漏洞 6：req_extractor 无输入长度限制

**现象**：`combined = "\n\n".join(all_content_parts)` 把所有文档拼接后直接传给 LLM，无长度检查。

**后果**：spec.md 末尾自己记录了"集成类 9.7MB PDF OOM（exit code 137）"，但代码层面仍未加防护。

**证据**：
```python
# req_extractor.py:220
combined = "\n\n".join(all_content_parts)
# ← 无 len(combined) 检查，无分片逻辑
```

---

#### 漏洞 7：并行生成无错误隔离

**现象**：`generate_all_sections_parallel` 用 ThreadPoolExecutor，`future.result()` 无 try/except。

**后果**：一个章节 LLM 调用失败会导致整个并行批次抛异常，8 章全部丢失。

---

### 🟡 P2 改进项

#### 漏洞 8：node_status FAILED 无早期路由

**现象**：DocumentParser 或 ReqExtractor 标记 FAILED 后，graph 仍继续执行后续节点。

**后果**：解析失败时，空内容一路传到 SectionGenerator 才表现为"无内容可生成"，浪费 LLM 调用。

---

#### 漏洞 9：feedback_history 三层压缩未实现

**现象**：AgentState 定义了 `global_constraints`、`compressed_history`、`context_window_size` 三个字段用于三层压缩，但 feedback_processor 只做简单 append。

**后果**：多轮重试后 feedback_history 膨胀，可能超出 LLM 上下文窗口。

---

## 四、优化方案（补充 spec.md）

### 4.1 紧急修复（插入 spec.md 阶段 1 之前）

**优先级：P0-紧急，预计 2 天**

| 任务 | 文件 | 修复内容 |
|------|------|---------|
| LLM 注入修复 | graph.py, pipeline_runner.py | 用 `functools.partial` 预绑定 llm_fn，或改用 RunnableConfig |
| 反馈循环接通 | section_generator.py | 读取 feedback_history，对 target_section 执行定向重生成（删除已存在内容后重新生成） |
| 模板消费接通 | section_generator.py | 读取 selected_template_id，从模板库加载章节结构 |

**这是 spec.md 阶段 1-3 所有改进的前提**——如果 LLM 注入和反馈循环不修复，后续的 RAG 注入、评分模拟都建在沙子上。

### 4.2 spec.md 阶段调整建议

| spec.md 原阶段 | 建议调整 |
|----------------|---------|
| 阶段 1.2 质量检查修复 | 新增：修复 `_check_completeness` 启发式，改为逐评分项内容匹配 |
| 阶段 2.1 定向修订 | **提升为 P0**，与紧急修复合并 |
| 阶段 2.3 表格结构化 | **提升为 P1**，评分表是 ReqExtractor 的关键输入 |
| 阶段 1.6（新增） | req_extractor 输入长度限制 + 分片逻辑 |
| 阶段 1.7（新增） | 并行生成错误隔离，单章失败返回占位内容 |
| 阶段 1.8（新增） | node_status FAILED 条件路由，解析失败直接终止 |
| 阶段 3.4（新增） | evolution 模块接入 SectionGenerator |

### 4.3 修复后的目标工作流

```
DocumentParser ──[FAILED]──→ END(错误终止)
      │
      ↓ [OK]
ReqExtractor ──[FAILED]──→ END(错误终止)
      │
      ↓ [OK]
TemplateMatcher → selected_template_id 写入 state
      │
      ↓
SectionGenerator(读取模板+feedback_history定向重生成)
      │
      ↓
QualityChecker(全量内容+0-100分+分类问题)
      │
      ├──[PASS]──→ ScoreSimulator → DocumentAssembler
      │
      └──[FAIL]──→ FeedbackProcessor(结构化指令)
                        │
                        ↓
                   SectionGenerator(仅重生成 target_section)
```

---

## 五、综合打分

### 5.1 当前状态评分：34 / 100

**扣分理由汇总**：
- 反馈循环完全失效（-13）：最严重，质量问题无法被修复
- LLM 注入断裂（-8）：3/4 节点走 mock
- 模板匹配无效（-5）：整步计算被丢弃
- 质量检查过宽（-6）：启发式形同虚设
- 工程健壮性不足（-9）：无错误隔离、无长度限制、无早期路由
- evolution 未接入（-3）：建好未通电
- 其他小问题（-2）

### 5.2 spec.md 优化方案评分：62 / 100

**spec.md 方案本身的质量**（不是当前代码状态）：

| 评估项 | 得分 | 说明 |
|--------|:--:|------|
| 问题识别覆盖度 | 7/10 | 识别了 5 个核心问题，但漏掉 9 个（尤其反馈循环断裂） |
| 方案技术可行性 | 8/10 | RAG 注入、评分模拟、定向修订方案都合理可行 |
| 优先级排序 | 6/10 | 表格结构化被低估为 P2，定向修订应提前 |
| 完整性 | 7/10 | 缺少 LLM 注入修复、错误隔离、早期失败路由 |
| 可执行性 | 8/10 | 文件路径精确、成功标准清晰、有 Gantt 图 |
| 风险识别 | 6/10 | 识别了 OOM 风险，但未识别反馈循环断裂风险 |
| **合计** | **42/60** → 换算 **62/100** | 方案方向正确，但需补充紧急修复 |

### 5.3 修复后预期评分

| 阶段 | 预期评分 | 关键提升 |
|------|:--:|---------|
| 紧急修复完成 | 55 | +21：LLM 注入、反馈循环、模板消费接通 |
| spec.md 阶段 1 | 70 | +15：RAG 注入、质量检查、评分模拟 |
| spec.md 阶段 2 | 85 | +15：定向修订、人工审核、表格结构化 |
| spec.md 阶段 3 + 新增项 | 93 | +8：多轮迭代、持久化、evolution 接入 |

---

## 六、结论

### 6.1 spec.md 优化方案是否合理？

**方向合理，但优先级有误，且遗漏了致命问题。**

spec.md 的三阶段路线图（知识库 → 底座夯实 → 质量深化 → 产品精炼）逻辑清晰，技术方案（RAG 注入、评分模拟、定向修订）都可行。但存在两个根本问题：

1. **未发现反馈循环已经断裂**——spec.md 认为"反馈循环弱（整章重写）"，实际是"反馈循环无效（重写从未发生）"。这个误判会导致阶段 2.1 的"定向修订"建立在错误前提上。

2. **未发现 LLM 注入断裂**——spec.md 的所有 LLM 相关改进（RAG 注入、评分模拟、A/B 测试）都假设 LLM 能正常调用，但实际 3/4 节点走 mock。

### 6.2 建议的执行顺序

```
0. 紧急修复（2天）← spec.md 缺失，必须先做
   ├─ LLM 注入接通
   ├─ 反馈循环接通
   └─ 模板消费接通

1. spec.md 阶段 0（已完成）✅

2. spec.md 阶段 1（5天）+ 新增项（3天）
   ├─ RAG 注入
   ├─ 质量检查修复（含启发式修复）
   ├─ 标书类型贯穿
   ├─ 评分模拟器
   ├─ [新增] req_extractor 长度限制
   ├─ [新增] 并行错误隔离
   └─ [新增] 早期失败路由

3. spec.md 阶段 2（5天）+ 表格提升为 P1
   ├─ 定向修订（已接通反馈循环后）
   ├─ 人工审核卡点
   └─ 表格结构化提取

4. spec.md 阶段 3（5天）+ evolution 接入
   ├─ 多轮迭代
   ├─ A/B Prompt
   ├─ 持久化续传
   └─ [新增] evolution 模块接入
```

### 6.3 一句话总结

> spec.md 是一份合格的产品规划文档，但它诊断的是"理想中的管道"，而非"实际运行的管道"。在管道的反馈循环和 LLM 注入断裂被修复之前，所有上层优化（RAG、评分、迭代）都是在沙子上盖楼。**先接通断裂的数据流，再谈深化。**
