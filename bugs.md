# 标书制作智能体 — 全流程代码审查 Bug 报告

> 审查日期：2026-07-07
> 审查范围：`bid-agent/` 全部核心代码（`core/`, `gui/`, `config/`, `tests/`）
> 测试结果：473 个测试全部通过（安装 PyMuPDF 后）
> 审查方法：人工代码审查 + 测试运行 + 跨模块数据流分析

---

## Bug 汇总

| 编号 | 严重程度 | 模块 | 简述 |
|------|---------|------|------|
| BUG-01 | 🔴 高 | `core/graph.py` | headless 模式下 HumanReviewGate 未设置 auto_approve，管道在到达 DocumentAssembler 前终止 |
| BUG-02 | 🔴 高 | `core/nodes/feedback_processor.py` | FeedbackProcessor 不读取 cross_ref_report / compliance_report，可能导致无限循环 |
| BUG-03 | 🔴 高 | `gui/pipeline_runner.py` | ScoreSimulator 未注入 LLM 函数，始终降级为启发式评分 |
| BUG-04 | 🔴 高 | `core/retrieval/faiss_index.py` | FAISS 模式下 save/load 导致向量数据全部丢失 |
| BUG-05 | 🟡 中 | `core/nodes/template_matcher.py` | TemplateMatcher 的 search_fn 从未注入，模板匹配始终返回空结果 |
| BUG-06 | 🟡 中 | `core/evolution/prompt_registry.py` | mark_used() 的 avg_score 计算存在 off-by-one 错误 |
| BUG-07 | 🟡 中 | `core/evolution/metrics_tracker.py` | avg_revision_rounds 计算公式不正确 |
| BUG-08 | 🟡 中 | `core/nodes/compliance_checker.py` | "最" 字正则排除列表不完整，常见技术术语被误报为违禁词 |
| BUG-09 | 🟡 中 | `core/nodes/doc_parser.py` | _load_pdf 异常捕获范围过窄，fitz 非 ModuleNotFoundError 异常会导致崩溃 |
| BUG-10 | 🟡 中 | `requirements.txt` | pdfminer.six 未列入依赖，PyMuPDF 不可用时回退会崩溃 |
| BUG-11 | 🟡 中 | `core/retrieval/faiss_index.py` | HNSW 索引不支持 reconstruct()，remove() 方法可能抛出异常 |
| BUG-12 | 🟡 中 | `gui/panels/config_panel.py` | oa_model 存入 api_keys 字典，语义错误且需特殊处理 |
| BUG-13 | 🟡 中 | `core/graph.py` | run_pipeline_with_snapshots 步骤计数器与实际节点序列不匹配 |
| BUG-14 | 🟢 低 | `core/nodes/feedback_processor.py` | _classify_issue 中 issue_lower 定义后未使用，英文关键词大小写敏感 |
| BUG-15 | 🟢 低 | `gui/panels/export_panel.py` | 重新运行质量检查时未传入 llm_fn，仅使用本地规则检查 |

---

## 详细 Bug 描述

### BUG-01: headless 模式下 HumanReviewGate 未设置 auto_approve，管道提前终止 🔴

- **文件**: `core/graph.py` — `build_graph()` 函数（第 237 行）
- **严重程度**: 高
- **类型**: 功能缺陷

**问题描述**:

`build_graph()` 函数的文档注释说明它是 headless 模式（CI/批处理），管道应"端到端运行无需暂停"。其中 `InfoVerificationGate` 被正确注册为 `auto_confirm=True`：

```python
graph.add_node("InfoVerificationGate", functools.partial(info_verification_gate, auto_confirm=True))
```

但 `HumanReviewGate` 未设置 `auto_approve=True`：

```python
graph.add_node("HumanReviewGate", human_review_gate)  # auto_approve 默认为 False
```

这导致 headless 模式下 `HumanReviewGate` 始终将 `review_status` 设为 `"pending"`，`route_after_review` 返回 `"__end__"`，管道在到达 `DocumentAssembler` 之前终止。**headless 管道永远不会生成 DOCX 输出**。

**复现路径**:

1. 调用 `run_pipeline(state)` （不预设 `review_status`）
2. 管道运行至 HumanReviewGate → `review_status = "pending"`
3. `route_after_review` → `"__end__"` → 管道终止
4. `DocumentAssembler` 从未执行，`export_path` 为空

**影响**: 任何通过 `build_graph()` / `run_pipeline()` 运行的 headless 管道都无法生成最终文档。

**修复建议**: 在 `build_graph()` 中注册 HumanReviewGate 时添加 `auto_approve=True`：

```python
graph.add_node("HumanReviewGate", functools.partial(human_review_gate, auto_approve=True))
```

---

### BUG-02: FeedbackProcessor 不读取 cross_ref_report / compliance_report，可能导致无限循环 🔴

- **文件**: `core/nodes/feedback_processor.py` — `feedback_processor()` 函数（第 78-154 行）
- **严重程度**: 高
- **类型**: 逻辑缺陷 / 死循环风险

**问题描述**:

当 `CrossReferenceChecker` 或 `ComplianceChecker` 检查失败并路由到 `FeedbackProcessor` 时，`FeedbackProcessor` 仅从 `quality_report` 读取问题：

```python
quality = state.get("quality_report", {})
issues = []
issues.extend(quality.get("compliance", []))
issues.extend(quality.get("consistency", []))
```

但 `CrossReferenceChecker` 的问题存储在 `cross_ref_report` 中，`ComplianceChecker` 的问题存储在 `compliance_report` 中，均不在 `quality_report` 中。

如果 `QualityChecker` 通过（verdict="PASS"），则 `quality_report` 中的 `compliance` 和 `consistency` 列表为空。此时 `FeedbackProcessor` 找不到任何问题，**不递增 `current_round`** 直接返回。但 `route_after_feedback` 始终返回 `"SectionGenerator"`，导致管道无限循环：

```
QualityChecker(PASS) → CrossReferenceChecker(FAIL) → FeedbackProcessor(无问题, 不递增 round)
    → SectionGenerator(无反馈, 跳过) → QualityChecker(PASS) → CrossReferenceChecker(FAIL) → ...
```

由于 `current_round` 永远不递增，`route_after_cross_ref` 中的 `state["current_round"] < state.get("max_rounds", 3)` 条件始终为 True，循环无法终止。

**影响**: 当跨章一致性检查或合规检查发现 QualityChecker 未覆盖的问题时，管道会无限循环挂起。

**修复建议**: `FeedbackProcessor` 应同时读取 `cross_ref_report` 和 `compliance_report` 中的问题，并在这些报告有 FAIL 问题时递增 `current_round`。

---

### BUG-03: ScoreSimulator 未注入 LLM 函数，始终降级为启发式评分 🔴

- **文件**: `gui/pipeline_runner.py` — `build_llm_for_pipeline()` 函数（第 56 行、第 85 行）
- **严重程度**: 高
- **类型**: 功能缺失

**问题描述**:

`graph.py` 中的 `_LLM_NODES` 常量列出了 6 个需要 LLM 的节点，包括 `ScoreSimulator`：

```python
_LLM_NODES = ("ReqExtractor", "ContractExtractor", "SectionGenerator", "QualityChecker", "FeedbackProcessor", "ScoreSimulator")
```

`build_graph()` 中 `ScoreSimulator` 也通过 `_bind` 注册了 LLM 绑定：

```python
graph.add_node("ScoreSimulator", _bind("ScoreSimulator", score_simulator))
```

但 `pipeline_runner.py` 的 `build_llm_for_pipeline()` 只为 5 个节点构建 LLM 函数，**不包含 `ScoreSimulator`**：

```python
nodes = ["ReqExtractor", "ContractExtractor", "SectionGenerator", "QualityChecker", "FeedbackProcessor"]
```

因此 `ScoreSimulator` 在生产环境中永远收不到 LLM 函数，始终降级为 `_heuristic_score()`（基于关键词覆盖率的粗略估算）。`_llm_score()` 函数成为死代码。

**影响**: 评分模拟功能无法使用 LLM 进行精确评分，仅能提供基于关键词匹配的粗略估算，严重影响评分预测的准确性和实用性。

**修复建议**: 在 `build_llm_for_pipeline()` 的 nodes 列表中添加 `"ScoreSimulator"`。

---

### BUG-04: FAISS 模式下 save/load 导致向量数据全部丢失 🔴

- **文件**: `core/retrieval/faiss_index.py` — `save()` / `load()` 方法
- **严重程度**: 高
- **类型**: 数据丢失

**问题描述**:

当 FAISS 可用时，向量存储在 FAISS 索引中，`self._vectors` 为 `None`。

`save()` 方法在 `self._vectors is None` 时保存空数组：

```python
if self._vectors is not None:
    np.savez_compressed(path, vectors=self._vectors, **data)
else:
    np.savez_compressed(path, vectors=np.zeros((0, self.dim), dtype=np.float32), **data)
```

`load()` 方法从文件加载向量到 `self._vectors`，但**不重建 FAISS 索引**：

```python
mgr = cls(dim=dim or stored_dim)
mgr._ids = ids
if len(vectors) > 0:
    mgr._vectors = vectors.astype(np.float32)
# FAISS 索引未被重建！
```

由于加载时 `vectors` 是空数组（保存时就是空的），`len(vectors) > 0` 为 False，`mgr._vectors` 保持 `None`。同时 FAISS 索引也不被重建（`load()` 创建的新实例的 `_faiss_index` 是空的）。

**结果**: save → load → 所有向量数据丢失，搜索返回空结果。

**影响**: 任何涉及向量索引持久化的功能（如知识库构建后保存再加载）都会丢失全部数据。

**修复建议**: `save()` 方法应在 FAISS 模式下从 FAISS 索引中提取向量（使用 `make_direct_map()` + `reconstruct()`，或将向量同时保存在 `self._vectors` 中）。`load()` 方法应在加载向量后重新构建 FAISS 索引。

---

### BUG-05: TemplateMatcher 的 search_fn 从未注入，模板匹配始终返回空结果 🟡

- **文件**: `core/nodes/template_matcher.py` — `template_matcher()` 函数
- **严重程度**: 中
- **类型**: 功能缺失 / 死代码

**问题描述**:

`template_matcher()` 函数需要 `search_fn` 参数来搜索模板索引：

```python
def template_matcher(state, search_fn=None):
    ...
    if not query or search_fn is None:
        logger.warning("No query text or search function — returning empty matches")
        return {"matched_templates": [], ...}
```

但在 `build_graph()` 和 `build_generation_graph()` 中，`TemplateMatcher` 注册时**未注入 `search_fn`**：

```python
graph.add_node("TemplateMatcher", template_matcher)  # search_fn 默认为 None
```

因此 `TemplateMatcher` 始终返回空匹配列表，`selected_template_id` 始终为空字符串。整个模板匹配步骤是死计算。

`SectionGenerator` 中的 `_resolve_sections_from_template()` 因为 `selected_template_id` 为空，始终回退到 `_get_sections_for_bid_type()`，模板驱动的章节选择功能完全失效。

**影响**: 模板匹配功能完全不工作，无法根据招标文件内容自动选择最合适的标书模板。

**修复建议**: 在 `build_graph()` 和 `build_generation_graph()` 中注入 `search_fn`，可以从 `VectorIndexManager` 构建。

---

### BUG-06: PromptRegistry mark_used() 的 avg_score 计算存在 off-by-one 错误 🟡

- **文件**: `core/evolution/prompt_registry.py` — `mark_used()` 方法（第 167-190 行）
- **严重程度**: 中
- **类型**: 计算错误

**问题描述**:

`mark_used()` 方法在 SQL UPDATE 语句中同时递增 `used_count` 和更新 `avg_score`：

```sql
UPDATE prompt_registry
SET used_count = used_count + 1,
    avg_score = (avg_score * used_count + ?) / (used_count + 1)
WHERE id = ?
```

SQLite 的 SET 子句**从左到右依次执行**。当计算 `avg_score` 时，`used_count` 已经被递增了。实际计算变为：

```
avg_score = (old_avg * (old_count + 1) + new_score) / (old_count + 2)
```

而正确的计算应该是：

```
avg_score = (old_avg * old_count + new_score) / (old_count + 1)
```

**影响**: 每次调用 `mark_used()` 时，平均分计算都会有偏差，随着使用次数增加，偏差会累积。

**修复建议**: 将 `used_count` 递增和 `avg_score` 更新分两条 SQL 语句执行，或在一条语句中使用旧值：

```sql
UPDATE prompt_registry
SET avg_score = (avg_score * used_count + ?) / (used_count + 1),
    used_count = used_count + 1
WHERE id = ?
```

（先计算 avg_score，再递增 used_count）

---

### BUG-07: MetricsTracker avg_revision_rounds 计算公式不正确 🟡

- **文件**: `core/evolution/metrics_tracker.py` — `avg_revision_rounds()` 方法（第 160-212 行）
- **严重程度**: 中
- **类型**: 计算错误

**问题描述**:

`avg_revision_rounds()` 使用以下公式计算平均修订轮次：

```python
avg = (max_r + 1) / 2.0 if total_sessions > 0 else 0.0
```

这个公式假设修订轮次在 0 到 `max_r` 之间均匀分布，用 `(max + 1) / 2` 作为平均值。这在统计上是错误的——它不反映实际的平均修订轮次。

例如：3 次会话的修订轮次分别为 0、0、2，max_r = 2，公式计算 avg = 1.5，但实际平均值是 (0+0+2)/3 ≈ 0.67。

**影响**: 进化系统中显示的平均修订轮次数据不准确，可能误导优化决策。

**修复建议**: 查询每个会话（以 round=0 标识）的最大 round_num，然后计算所有会话最大 round_num 的平均值。

---

### BUG-08: ComplianceChecker "最" 字正则排除列表不完整，常见技术术语被误报 🟡

- **文件**: `core/nodes/compliance_checker.py` — `_CONTEXT_PATTERNS` 字典（第 50 行）
- **严重程度**: 中
- **类型**: 误报 / 正则缺陷

**问题描述**:

`ComplianceChecker` 的 "最" 字上下文敏感正则为：

```python
"最": re.compile(r"最(?![终后新近高低大小多少早晚初](?!级))"),
```

该正则排除 "最" 后跟 终/后/新/近/高/低/大/小/多/少/早/晚/初 的情况，但**不排除**常见的技术术语如：
- "最优化" → "优" 不在排除列表 → **误报为绝对化用语**
- "最重要" → "重" 不在排除列表 → **误报为绝对化用语**
- "最佳实践" → "佳" 不在排除列表 → **误报为绝对化用语**（但 "最佳" 本身可能需要关注）

而 `QualityChecker` 的对应正则有更完整的排除列表：

```python
"最": re.compile(r"最(?!最终|后|新|近|高|低|大|小|多|少|早|晚|初|优化|重要)"),
```

两个检查器对同一关键词使用了不同的排除规则，存在不一致。

**影响**: 标书中常见的 "最优化"、"最重要" 等技术术语会被误标为违法绝对化用语，导致不必要的反馈循环和内容修改。

**修复建议**: 将 ComplianceChecker 的 "最" 字正则排除列表与 QualityChecker 统一，添加 "优化"、"重要"、"佳" 等常见后续词。

---

### BUG-09: _load_pdf 异常捕获范围过窄，fitz 非 ModuleNotFoundError 异常会导致崩溃 🟡

- **文件**: `core/nodes/doc_parser.py` — `_load_pdf()` 函数（第 37-61 行）
- **严重程度**: 中
- **类型**: 异常处理缺陷

**问题描述**:

`_load_pdf()` 使用 `except ModuleNotFoundError` 捕获 fitz 导入失败，但**不捕获** fitz 运行时异常：

```python
try:
    import fitz
    doc = fitz.open(file_path)
    ...
except ModuleNotFoundError:
    pass

# Fallback: pdfminer.six
from pdfminer.high_level import extract_text as pdfminer_extract_text
return pdfminer_extract_text(file_path)
```

如果 fitz 已安装但 `fitz.open(file_path)` 抛出非 `ModuleNotFoundError` 异常（如 PDF 文件损坏、加密、格式不支持的 `RuntimeError` / `ValueError` / `FileDataError`），异常不会被捕获，函数直接崩溃，不会回退到 pdfminer.six。

**影响**: 上传损坏或加密的 PDF 文件时，解析器会崩溃而非优雅降级。

**修复建议**: 将 `except ModuleNotFoundError` 改为 `except Exception`，或至少捕获 `(ModuleNotFoundError, RuntimeError, ValueError, Exception)`。

---

### BUG-10: pdfminer.six 未列入 requirements.txt，回退路径会崩溃 🟡

- **文件**: `requirements.txt`
- **严重程度**: 中
- **类型**: 缺失依赖

**问题描述**:

`_load_pdf()` 函数在 PyMuPDF 不可用时回退到 pdfminer.six：

```python
from pdfminer.high_level import extract_text as pdfminer_extract_text
return pdfminer_extract_text(file_path)
```

但 `pdfminer.six` **未列入 `requirements.txt`**。如果用户环境未安装 PyMuPDF（或 PyMuPDF 安装失败），回退代码会因 `ModuleNotFoundError: No module named 'pdfminer'` 而崩溃。

此外，`_load_doc()` 中使用的 `olefile` 也未列入依赖（但该导入已被 `try/except ImportError` 保护，不会崩溃）。

**影响**: 在未安装 PyMuPDF 的环境中，PDF 解析功能完全不可用。

**修复建议**: 在 `requirements.txt` 中添加 `pdfminer.six>=20221119`。

---

### BUG-11: HNSW 索引不支持 reconstruct()，remove() 方法可能抛出异常 🟡

- **文件**: `core/retrieval/faiss_index.py` — `remove()` 方法（第 91-113 行）
- **严重程度**: 中
- **类型**: 兼容性缺陷

**问题描述**:

`remove()` 方法在 FAISS 模式下使用 `reconstruct()` 提取向量：

```python
if _HAS_FAISS and self._faiss_index is not None:
    new_vectors = np.zeros((len(keep_indices), self.dim), dtype=np.float32)
    for new_idx, old_idx in enumerate(keep_indices):
        self._faiss_index.reconstruct(old_idx, new_vectors[new_idx])
    self._faiss_index = self._create_faiss_index()
    self._faiss_index.add(new_vectors)
```

但 FAISS 的 `IndexHNSWFlat` **默认不支持 `reconstruct()`** — 需要先调用 `index.make_direct_map()` 才能使用。不调用 `make_direct_map()` 直接使用 `reconstruct()` 会抛出 `RuntimeError: Direct map not set`。

**影响**: 在 FAISS 模式下调用 `remove()` 方法会崩溃。

**修复建议**: 在 `_create_faiss_index()` 后调用 `index.make_direct_map()`，或在 `remove()` 中使用 `self._vectors` 的 numpy 副本来避免使用 `reconstruct()`。

---

### BUG-12: config_panel.py 将 oa_model 存入 api_keys 字典，语义错误 🟡

- **文件**: `gui/panels/config_panel.py` — `render_config_panel()` 函数（第 116 行）
- **严重程度**: 中
- **类型**: 设计缺陷

**问题描述**:

配置面板将 OpenAI 模型名称存入 `api_keys` 字典：

```python
api_keys["oa_model"] = oa_model
```

`api_keys` 字典的语义是"API 密钥映射"，将模型名称混入其中是语义错误。这迫使 `pipeline_runner.py` 中的 `build_llm_for_pipeline()` 添加特殊过滤：

```python
for provider, key in api_keys.items():
    if key and provider not in ("oa_model",):  # 特殊处理
        router.set_api_key(provider, key)
```

如果未来添加其他非密钥字段到 `api_keys`，需要同步修改此过滤条件，容易遗漏。

**影响**: 代码可维护性差，容易引入新 bug。

**修复建议**: 将 `oa_model` 存储在配置的单独字段中（如 `config["openai_model"]`），不要混入 `api_keys`。

---

### BUG-13: run_pipeline_with_snapshots 步骤计数器与实际节点序列不匹配 🟡

- **文件**: `core/graph.py` — `run_pipeline_with_snapshots()` 函数（第 544-617 行）
- **严重程度**: 中
- **类型**: 逻辑缺陷

**问题描述**:

`run_pipeline_with_snapshots()` 使用简单的 `step` 递增计数器来索引 `_NODE_LABELS` 列表，以确定刚完成的节点名称：

```python
for chunk in app.stream(state, stream_mode="values"):
    node_name = _NODE_LABELS[step][0] if step < len(_NODE_LABELS) else f"Step{step}"
    ...
    step += 1
```

但管道使用条件路由（反馈循环、提前终止、跨章检查链），实际节点执行序列不是线性的。例如：
- 首次运行：DocumentParser → ReqExtractor → ... → QualityChecker → CrossReferenceChecker → ...（14 个节点）
- 有反馈循环：DocumentParser → ... → QualityChecker → FeedbackProcessor → SectionGenerator → QualityChecker → ...（>14 个步骤）

当 `step` 超过 `_NODE_LABELS` 长度时，节点名退化为 `Step{N}`，丢失了有意义的标签。反馈循环中的重复节点（如第二次 QualityChecker）会被错误标记为下一个顺序节点。

**影响**: 快照文件名和元数据中的节点名称不准确，影响调试和分析。

**修复建议**: 从 LangGraph 的 stream 输出中提取实际执行的节点名称（使用 `stream_mode="updates"` 可以获取节点名称），而非依赖线性计数器。

---

### BUG-14: _classify_issue 中 issue_lower 定义后未使用 🟢

- **文件**: `core/nodes/feedback_processor.py` — `_classify_issue()` 函数（第 25-51 行）
- **严重程度**: 低
- **类型**: 代码缺陷

**问题描述**:

`_classify_issue()` 定义了 `issue_lower = issue.lower()`，但后续所有关键词匹配都使用 `issue`（原始大小写）而非 `issue_lower`：

```python
issue_lower = issue.lower()  # 定义了但从未使用

for kw in style_keywords:
    if kw in issue:  # 应该是 issue_lower
        ...
for kw in score_keywords:
    if kw in issue:  # 应该是 issue_lower
        ...
```

对于中文关键词无影响（`.lower()` 不改变中文字符），但 `score_keywords` 中的英文关键词 `"score"` 无法匹配 `"Score"` 或 `"SCORE"`。

**影响**: 包含大写 "Score" 的反馈文本不会被分类为 "score_align" 类型。

**修复建议**: 将所有 `if kw in issue` 改为 `if kw in issue_lower`。

---

### BUG-15: 导出面板重新运行质量检查时未传入 llm_fn 🟢

- **文件**: `gui/panels/export_panel.py` — `render_export_panel()` 函数（第 33-37 行）
- **严重程度**: 低
- **类型**: 功能降级

**问题描述**:

导出面板中"重新运行质量检查"按钮直接调用 `quality_checker(result)`：

```python
if st.button("🔁 重新运行质量检查"):
    from core.nodes.quality_checker import quality_checker
    qc = quality_checker(result)  # 未传入 llm_fn
    result.update(qc)
    st.session_state["pipeline_result"] = result
    st.rerun()
```

`quality_checker()` 的 `llm_fn` 参数默认为 `None`，因此只会运行本地规则检查（违禁词、完整性、一致性），不会执行 LLM 深度检查。即使用户已配置 LLM，此处的质量检查也不使用 LLM。

**影响**: 导出前的质量复查结果不如管道运行时全面，可能遗漏本地规则未覆盖的问题。

**修复建议**: 从 `pipeline_runner` 获取 LLM 函数并传入，或使用 `get_pipeline_llm()` 全局 LLM。

---

## 测试覆盖度分析

### 已通过的测试

- 473 个测试全部通过（安装 PyMuPDF 后）
- 覆盖了单元测试、集成测试、契约测试

### 测试未覆盖的场景

1. **BUG-01**: E2E 测试仅 `test_full_pipeline_completes` 预设了 `review_status="approved"`，其他 E2E 测试不检查 DocumentAssembler 是否完成，因此 BUG-01 未被测试捕获
2. **BUG-02**: 无测试覆盖 "QualityChecker PASS 但 CrossReferenceChecker FAIL" 的场景
3. **BUG-03**: 无测试验证 ScoreSimulator 是否实际接收到 LLM 函数
4. **BUG-04**: 无测试覆盖 FAISS 模式下的 save → load 循环
5. **BUG-05**: 无测试验证 TemplateMatcher 在实际管道中是否收到 search_fn
6. **BUG-06**: 无测试验证 `mark_used()` 多次调用后的 avg_score 准确性
7. **BUG-09**: 无测试覆盖 fitz 安装但 PDF 损坏的场景

---

## 修复优先级建议

| 优先级 | Bug 编号 | 原因 |
|--------|---------|------|
| P0（立即修复） | BUG-01, BUG-02 | 管道无法正常完成 / 可能死循环 |
| P1（高优先级） | BUG-03, BUG-04 | 核心功能失效 / 数据丢失 |
| P2（中优先级） | BUG-05, BUG-06, BUG-07, BUG-08, BUG-09, BUG-10 | 功能降级 / 计算错误 / 依赖缺失 |
| P3（低优先级） | BUG-11, BUG-12, BUG-13, BUG-14, BUG-15 | 边缘场景 / 代码质量 |

---

# 全流程功能测试报告（2026-07-11）

> 测试人：端测测（Web 应用测试专家）
> 测试日期：2026-07-11
> 测试范围：13 节点管道全部功能 + Streamlit UI + 多模型配置 + E2E
> 测试方法：动态功能测试（mock LLM + 真实 PDF）+ 静态代码审查 + pytest 套件
> 测试环境：Python 3.13.12 / venv / macOS / bid-agent/.venv
> 仅记录问题，不做修复

## 一、测试概况

| 维度 | 结果 |
|------|------|
| pytest 套件 | **507 通过 / 0 失败**（65s） |
| Streamlit UI 冒烟 | **HTTP 200**，四面板正常加载，无启动错误 |
| 动态功能测试（mock LLM + 真实 PDF） | 覆盖 13 节点 + E2E，**发现 14 个问题** |
| 真实 PDF 解析 | 21MB 技术标 PDF → 10883 字符 + 14 个表格，正常 |

**关键矛盾**：pytest 507 全通过，但动态功能测试发现 14 个问题——说明**现有测试套件存在显著盲区**，未覆盖多个 PRD 验收场景。

## 二、旧 Bug 状态核查（BUG-01 ~ BUG-15）

| 编号 | 状态 | 说明 |
|------|:--:|------|
| BUG-01 | ✅ 已修复 | `build_graph()` 中 HumanReviewGate 已设 `auto_approve=True`（graph.py:249） |
| BUG-02 | ✅ 已修复 | feedback_processor 现读取 `cross_ref_report` + `compliance_report`（feedback_processor.py:143-160） |
| BUG-03 | ✅ 已修复 | pipeline_runner nodes 列表已含 ScoreSimulator（pipeline_runner.py:56,85） |
| BUG-04 | ❓ 未验证 | FAISS save/load 向量丢失，本次未测持久化场景 |
| BUG-05 | ✅ 已修复 | `build_default_search_fn()` 注入（graph.py:221,241） |
| BUG-06 | ❓ 未验证 | mark_used off-by-one，本次未测 |
| BUG-07 | ❓ 未验证 | avg_revision_rounds 公式，本次未测 |
| BUG-08 | ✅ 已修复 | "最"字正则已对齐 QualityChecker（compliance_checker.py:52） |
| BUG-09 | ✅ 已修复 | `_load_pdf` 已用 `except Exception`（doc_parser.py:56） |
| BUG-10 | ✅ 已修复 | pdfminer.six 已列入 requirements.txt |
| BUG-11 | ❓ 未验证 | HNSW reconstruct，本次未测 |
| BUG-12 | ✅ 已修复 | openai_model 独立配置字段（config_panel.py:32,44,122） |
| BUG-13 | ✅ 已修复 | 使用 `stream_mode="updates"`（graph.py:613） |
| BUG-14 | ✅ 已修复 | `issue_lower` 已被使用（feedback_processor.py:64,70,76） |
| BUG-15 | ✅ 已修复 | export_panel 传入 llm_fn（export_panel.py:36-49） |

**结论**：15 个旧 bug 中 **11 个已确认修复**，4 个（BUG-04/06/07/11）涉及 FAISS 持久化和 evolution 模块，本次未覆盖，建议后续补充测试。

## 三、新发现问题（N01 ~ N14）

### N01: headless 管道 export_path 丢失 — 文件生成了但调用方拿不到路径 🔴

- **文件**：`core/state.py`（AgentState 定义）+ `core/nodes/doc_assembler.py`
- **严重程度**：高
- **类型**：数据丢失 / 状态管理缺陷

**问题描述**：
`doc_assembler` 节点返回 `{"export_path": result_path, ...}`，但 `export_path` **未在 `AgentState` TypedDict 中定义**。LangGraph 的 StateGraph 以 TypedDict 为 schema，未定义字段会在状态合并时被**静默丢弃**。

**复现**（已验证）：
```
result = run_pipeline(state, llm_fns=...)  # headless 模式
result.get("export_path")  → ''  (空!)
# 但 data/exports/ 目录下实际生成了 DOCX 文件
# DocumentAssembler node_status = "completed"
```

**影响**：任何通过 `run_pipeline()` / `build_graph()` 运行的 headless 管道（CI/批处理/API 调用），即使 DocumentAssembler 成功生成 DOCX，调用方也无法从返回的 state 中获取文件路径。GUI 模式因 export_panel 直接调用 `doc_assembler()` 不受影响。

**修复建议**：在 `AgentState` TypedDict 中添加 `export_path: str` 字段，并在 `factory_state()` 默认值中初始化为 `""`。

---

### N02: 交互模式 DocumentAssembler 永不执行 — 导出完全靠 UI 按钮 🔴

- **文件**：`core/graph.py`（`build_generation_graph()`）+ `gui/panels/review_panel.py`
- **严重程度**：高
- **类型**：架构不一致 / 死代码

**问题描述**：
`build_generation_graph()`（交互模式）注册 HumanReviewGate 时**未设 `auto_approve=True`**（graph.py:438）。review_panel Phase 2 预设 `review_status="pending"`（review_panel.py:237），HumanReviewGate 返回 pending，`route_after_review` 返回 `"__end__"`，管道终止——**DocumentAssembler 从不执行**。

管道设计（PRD 3.3）：ScoreSimulator → HumanReviewGate → [approved] → DocumentAssembler。但交互模式下 approved 路径无触发方式，DocumentAssembler 是死代码。导出完全依赖 export_panel 的"导出 DOCX"按钮（直接调用 doc_assembler）。

**影响**：交互模式下管道的"文档装配"节点形同虚设，与 PRD 描述的管道流程不一致。review_panel 没有处理 HumanReviewGate approved 后 resume 管道的逻辑。

**修复建议**：在 review_panel 中实现 HumanReviewGate 的 resume 逻辑（用户点"通过"后调用 `resume_after_review` + 重新 invoke 生成图到 DocumentAssembler），或在交互模式也接受 auto_approve 后由 export_panel 导出（当前实际行为，需文档化）。

---

### N03: config_panel DeepSeek 模型名虚构 — API 调用会失败 🔴

- **文件**：`gui/panels/config_panel.py`（第 9-12 行）
- **严重程度**：高
- **类型**：配置错误

**问题描述**：
```python
DS_MODELS = [
    {"id": "deepseek-v4-pro", ...},
    {"id": "deepseek-v4-flash", ...},
]
```
DeepSeek 官方 API 的实际模型名是 `deepseek-chat` 和 `deepseek-reasoner`。`deepseek-v4-pro` / `deepseek-v4-flash` **不存在**。用户配置后，任何 LLM 调用都会因模型名无效而失败。

**影响**：DeepSeek 用户无法正常使用系统（除非手动改模型名，但 UI 不允许自定义输入）。

**修复建议**：改为 `deepseek-chat`（V3）和 `deepseek-reasoner`（R1），或提供自定义模型名输入框。

---

### N04: config_panel 缺节点级模型配置 UI 🟡

- **文件**：`gui/panels/config_panel.py`
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.6 验收标准 2）

**问题描述**：
PRD 4.2.6 验收标准 2 要求"为不同节点配置不同模型（如生成用 GPT-4o、质检用 Claude）"。代码中有 `config_node_overrides` session state 的读取/保存（config_panel.py:30,42），但**配置面板 UI 中没有任何控件让用户为不同节点选择模型**。

**影响**：节点级模型配置功能不可用，用户只能全局选一个模型。

**修复建议**：在配置面板添加节点级模型选择 UI（如为 SectionGenerator/QualityChecker/ScoreSimulator 分别选择模型）。

---

### N05: CrossReferenceChecker 缺契约偏离校验 🟡

- **文件**：`core/nodes/cross_reference_checker.py`
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.2 验收标准 2）

**问题描述**：
PRD 4.2.2 验收标准 2："给定某章出现契约禁止的机构名（如"复旦大学"出现在非复旦项目中），标记为 CRITICAL: contract_forbidden_institution"。

但 `cross_reference_checker` **完全不读取 `project_contract`**，只检查人员/金额/日期/文档组成。契约禁止项检测（forbidden_institutions / forbidden_industries）未实现。

**复现**（已验证）：
```
sections = {"服务方案": "我们与复旦大学合作多年..."}
project_contract = {"forbidden_institutions": ["复旦大学"]}
# cross_reference_checker 返回 inconsistencies=[] — 未检测到契约偏离
```

**影响**：拼凑残留（其他项目的机构名错误出现）无法被自动检测，可能被评审专家发现导致扣分。

**修复建议**：新增 `_check_contract_deviation()` 函数，读取 `project_contract.forbidden_institutions` 并扫描各章节。

---

### N06: CrossReferenceChecker 缺技术参数一致性检查 🟡

- **文件**：`core/nodes/cross_reference_checker.py`
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.2）

**问题描述**：
PRD 4.2.2 要求"检查技术参数跨章节一致（技术方案章 vs 服务方案章）"。代码只有 `_check_personnel_consistency` / `_check_amount_consistency` / `_check_date_consistency` / `_check_document_composition_consistency`，**没有技术参数一致性检查**。

**复现**（已验证）：
```
sections = {"技术方案章": "响应时间30秒", "服务方案章": "响应时间60秒"}
# cross_reference_checker 返回 inconsistencies=[] — 未检测到参数矛盾
```

**影响**：技术参数跨章矛盾（如响应时间、服务时长）不会被检测。

---

### N07: ComplianceChecker 格式合规不检查 format_rules 🟡

- **文件**：`core/nodes/compliance_checker.py`（`_check_format_compliance` 第 58-79 行）
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.3 验收标准 2）

**问题描述**：
`_check_format_compliance(sections, format_rules)` 接收 `format_rules` 参数但**完全不使用它**，只检查每章内容长度是否 ≥100 字。PRD 4.2.3 验收标准 2 要求"检查页边距、字体、行距、目录格式"。

代码注释承认"无法完全验证页边距/字体（需渲染 DOCX）"，但连 format_rules 中可检查的结构规则（如是否声明了页边距）都没检查。

**影响**：格式合规检查名不副实，仅检查内容长度。

---

### N08: 标书类型命名三处不一致 🟡

- **文件**：`gui/panels/upload_panel.py` + `core/nodes/template_matcher.py` + `knowledge_base/`
- **严重程度**：中
- **类型**：命名不一致 / 数据缺失

**问题描述**：
标书类型在三个地方命名不一致：

| 位置 | 命名 | 缺失 |
|------|------|------|
| upload_panel（用户选择） | `服务类`、`货物类`、`软件类`、`工程类`、`集成类`、`运维类`、`劳务管理服务类`、`劳务外包类`（8种，带"类"） | — |
| template_matcher.TEMPLATE_TYPES | `服务`、`货物`、`软件`、`工程`、`集成`、`运维`、`劳务外包`（7种，**不带"类"**） | 缺"劳务管理服务类" |
| knowledge_base 目录 | `服务类`、`货物类`、`工程类`、`集成类`、`运维类`、`劳务外包类`、`劳务管理服务类`（7种，带"类"） | **缺"软件类"目录** |

**影响**：
1. "软件类"在 UI 可选但知识库无对应目录，选了会检索失败
2. TEMPLATE_TYPES 命名不带"类"与知识库目录名不匹配，可能导致模板索引构建/检索错位
3. "劳务管理服务类"不在 TEMPLATE_TYPES 中，但知识库有目录

**修复建议**：统一命名规范（建议全部带"类"），补齐软件类知识库或从 UI 移除。

---

### N09: ScoreSimulator 启发式分词对中文无效 🟡

- **文件**：`core/nodes/score_simulator.py`（`_heuristic_score` 第 108 行）
- **严重程度**：中
- **类型**：逻辑缺陷

**问题描述**：
```python
tokens = [t for t in name_lower.replace("，", " ").split() if len(t) >= 2]
```
`split()` 按空白分词，但中文评分项名称（如"劳务管理服务方案"）**没有空格**，split 后整个名称是一个 token。当该名称未在 corpus 中完整出现时，`hits = sum(1 for t in tokens if t in corpus)` 只检查整个长名称，几乎不会命中。

**复现**（已验证）：
```
scoring item = "劳务管理服务方案", sections = "完全不相关的内容"
# predicted = 0, gap = 40 — 因为中文无法分词，token fallback 失效
```

**影响**：无 LLM 时，启发式评分对中文评分项几乎无效，预测得分偏低。

**修复建议**：用 jieba 分词或 n-gram 切分中文评分项名称。

---

### N10: HumanReviewGate 无 sections 时自动 reject 🟢

- **文件**：`core/nodes/human_review_gate.py`（第 64-73 行）
- **严重程度**：低
- **类型**：设计不一致

**问题描述**：
无 sections 时自动返回 `rejected`。但 PRD 设计 HumanReviewGate 的三态是 pending/approved/rejected，由人工决定。自动 reject 不符合"等待人工"的设计意图，且 rejected 会路由到 FeedbackProcessor 触发修订（但无内容可修订）。

**影响**：边缘场景，实际运行中无 sections 的情况罕见。

---

### N11: config_panel provider 硬编码 deepseek 🟢

- **文件**：`gui/panels/config_panel.py`（第 125 行）
- **严重程度**：低
- **类型**：硬编码

**问题描述**：
```python
st.session_state["config_provider"] = "deepseek"  # 硬编码
```
即使用户配置 OpenAI，provider 始终是 deepseek。虽然 pipeline_runner 按节点配置模型不受影响，但 config_provider 字段语义错误。

---

### N12: upload_panel 默认标书类型不合理 🟢

- **文件**：`gui/panels/upload_panel.py`（第 17 行）
- **严重程度**：低
- **类型**：UX 缺陷

**问题描述**：
`index=7` 默认选中"劳务外包类"。最常见的标书类型应是"服务类"（index=0），默认选第 8 项不符合常规预期。

---

### N13: graph.py 模块注释"7-node"过时 🟢

- **文件**：`core/graph.py`（第 1 行）
- **严重程度**：低
- **类型**：文档过时

**问题描述**：
模块 docstring 写"the 7-node pipeline"，实际已扩展到 14 节点（含 InfoVerificationGate）。

---

### N14: PRD 说 13 节点但代码 14 节点 🟢

- **文件**：`PRD_标书制作智能体.md`（3.3 节）vs `core/state.py`（ALL_NODES）
- **严重程度**：低
- **类型**：文档不一致

**问题描述**：
PRD 3.3 说"13 节点状态机"，但 `ALL_NODES` 实际有 14 个（多了 `InfoVerificationGate`，US#6 新增）。test_state.py 的 `test_exactly_fourteen_nodes` 断言是 14。PRD 未同步更新。

## 四、测试盲区分析

pytest 507 全通过却未发现上述 14 个问题，说明存在以下盲区：

| 盲区 | 未覆盖的问题 | 原因 |
|------|------------|------|
| 管道最终 state 字段完整性 | N01（export_path 丢失） | 无测试断言 `result["export_path"]` 非空，仅 test_can_export_docx 直接调用 doc_assembler |
| 交互模式管道行为 | N02（DocumentAssembler 死代码） | 所有 E2E 测试用 headless run_pipeline，无测试覆盖 build_generation_graph |
| PRD 验收场景负向用例 | N05/N06/N07（功能缺失） | 无测试验证"契约禁止机构被检测""技术参数矛盾被检测""format_rules 被检查" |
| 配置有效性 | N03（模型名虚构） | 无测试验证 DS_MODELS 的模型名是否真实存在 |
| 跨模块命名一致性 | N08 | 无测试对比 upload_panel/TEMPLATE_TYPES/knowledge_base 三处命名 |
| 中文分词 | N09 | 启发式评分测试用英文/简单名称，未覆盖中文长名称 |

## 五、修复优先级建议

| 优先级 | 编号 | 原因 |
|:--:|------|------|
| P0（立即） | N01, N03 | 导出路径丢失 / 模型名无效导致功能不可用 |
| P1（高） | N02 | 架构不一致，交互模式管道不完整 |
| P2（中） | N04, N05, N06, N07, N08, N09 | 功能缺失 / PRD 验收标准未满足 |
| P3（低） | N10, N11, N12, N13, N14 | 边缘场景 / UX / 文档 |

---

## 六、补充说明

- **未验证的旧 bug**：BUG-04（FAISS save/load）、BUG-06（mark_used off-by-one）、BUG-07（avg_revision_rounds）、BUG-11（HNSW reconstruct）涉及 FAISS 持久化和 evolution 模块，本次测试未覆盖，建议后续专项测试。
- **真实 LLM 未测**：本次用 mock LLM 测试，真实 LLM 调用（含 DeepSeek/OpenAI 实际 API）未测，N03（模型名）需真实 API 验证。
- **性能未测**：PRD 要求"初稿生成 <10 分钟""文档解析 <30 秒"，本次未做性能基准测试（mock LLM 无意义）。
- **测试脚本**：动态功能测试脚本位于 `/tmp/bid_smoke/smoke_test.py`，问题列表位于 `/tmp/bid_smoke/issues.json`。

---

# 第三轮代码审查 — 变更增量测试报告（2026-07-18）

> 审查日期：2026-07-18
> 审查范围：git diff 未提交变更（10 个文件，+1602/-430 行）
> 测试方法：增量代码审查 + 动态验证（python 脚本验证过滤逻辑和匹配逻辑）+ pytest 套件
> pytest 结果：**507 通过 / 0 失败**（22.95s）
> 仅记录问题，不做修复

## 一、变更文件清单

| 文件 | 变更量 | 变更概述 |
|------|--------|---------|
| `core/nodes/eligibility_checker.py` | +270 行 | 新增"软要求"过滤机制（9 类模式），自动通过非证书类资质要求 |
| `core/nodes/req_extractor.py` | +9 行 | `MAX_INPUT_CHARS` 从 8000 提升到 30000 |
| `core/nodes/section_generator.py` | +9 行 | 劳务类标书跳过模板匹配，直接使用 9 章结构 |
| `core/nodes/template_matcher.py` | +40 行 | 新增 bid_type 过滤，避免跨类型模板误匹配 |
| `gui/app.py` | +34 行 | 新增文件日志配置（`~/.bid-agent/logs/bid-agent.log`） |
| `gui/components/feedback_form.py` | +104/-18 行 | 重写反馈表单：支持累积多条反馈后统一应用，触发管道重新生成 |
| `gui/panels/review_panel.py` | +11 行 | 新增失败章节检测（检查"生成失败"标记） |
| `gui/panels/upload_panel.py` | +25/-16 行 | 投标人名称改为必填项；移除"软件类"选项；默认选中"服务类" |
| `data/consistency_lessons.json` | +40 行 | 一致性经验库数据更新 |
| `specs/009-large-file-strategy/spec.md` | +1490/-430 行 | 大文件策略规范文档更新 |

## 二、旧 Bug 状态核查（BUG-04/06/07/11 未验证项）

| 编号 | 状态 | 说明 |
|------|:--:|------|
| BUG-04 | ❓ 仍未验证 | FAISS save/load 向量丢失，本次未测持久化场景 |
| BUG-06 | ❓ 仍未验证 | mark_used off-by-one，本次未测 |
| BUG-07 | ❓ 仍未验证 | avg_revision_rounds 公式，本次未测 |
| BUG-11 | ❓ 仍未验证 | HNSW reconstruct，本次未测 |

## 三、N01-N14 修复状态核查

| 编号 | 状态 | 说明 |
|------|:--:|------|
| N01 | ✅ 已修复 | `AgentState` 已添加 `export_path: str` 字段 |
| N02 | ✅ 已修复 | review_panel 新增"通过/驳回"按钮，通过后调用 doc_assembler；feedback_form 新增"应用反馈并重新生成"按钮 |
| N03 | ✅ 已修复 | DeepSeek 模型名已改为 `deepseek-chat` / `deepseek-reasoner` |
| N04 | ❌ 未修复 | 仍无节点级模型配置 UI |
| N05 | ✅ 已修复 | `cross_reference_checker` 新增 `_check_contract_deviation()` |
| N06 | ✅ 已修复 | `cross_reference_checker` 新增 `_check_tech_parameter_consistency()` |
| N07 | ❌ 未修复 | `_check_format_compliance` 仍不使用 `format_rules` |
| N08 | ⚠️ 部分修复 | upload_panel 和 TEMPLATE_TYPES 已对齐（带"类"）；但 `template_library.py` 的 `TEMPLATES` 字典键仍不带"类"，且缺"劳务管理服务"键 |
| N09 | ✅ 已修复 | `_heuristic_score` 已使用字符 n-gram 切分中文 |
| N10 | ✅ 已修复 | 无 sections 时返回 "pending" 而非 "rejected" |
| N11 | ✅ 已修复 | provider 不再硬编码 deepseek |
| N12 | ✅ 已修复 | 默认选中 index=0（"服务类"） |
| N13 | ✅ 已修复 | 模块注释已更新 |
| N14 | ✅ 已修复 | PRD 已同步更新为 14 节点 |

## 四、新发现问题（N15 ~ N22）

### N15: eligibility_checker `_DOC_PATTERNS` 正则过于宽泛，合法资质被误过滤 🔴

- **文件**：`core/nodes/eligibility_checker.py` — `_DOC_PATTERNS` 列表（第 89-113 行）
- **严重程度**：高
- **类型**：误报 / 过滤缺陷

**问题描述**：

`_DOC_PATTERNS` 包含以下过度宽泛的正则模式：

```python
".*运营方案",
".*培训支持方案",
".*实施方案",
".*管理方案",
".*服务方案",
".*技术方案",
".*保障方案",
".*应急预案",
```

这些模式会匹配任何以这些词结尾的字符串，包括合法的资质证书名称。

**已验证的误过滤案例**（通过 python 脚本确认）：

| 输入资质名称 | 应被保留 | 实际结果 |
|-------------|---------|---------|
| `信息技术服务方案资质` | ✅ 是 | ❌ 被过滤为"软要求" |
| `质量管理方案认证` | ✅ 是 | ❌ 被过滤为"软要求" |
| `IT服务方案资质认证` | ✅ 是 | ❌ 被过滤为"软要求" |

这些合法的资质证书会被自动通过（auto-pass），**不会与企业资质库进行匹配验证**，可能导致企业实际缺少该资质但系统认为满足要求。

**影响**：含"方案"二字的资质证书名称会被误过滤，资质门槛检查失效。

**修复建议**：`_DOC_PATTERNS` 中的方案类正则应添加边界约束，排除以"资质"/"认证"/"许可"/"证书"结尾的字符串，例如：`r".*服务方案(?<!资质)(?<!认证)(?<!许可)"` 或在 `_is_generic_qual()` 中添加反向检查：如果 qual 以"资质"/"认证"/"许可"/"证书"结尾，则不视为软要求。

---

### N16: eligibility_checker `_CAPABILITY_PATTERNS` 和 `_INVOICE_PATTERNS` 过度宽泛 🟡

- **文件**：`core/nodes/eligibility_checker.py` — `_CAPABILITY_PATTERNS`（第 129-140 行）和 `_INVOICE_PATTERNS`（第 193-201 行）
- **严重程度**：中
- **类型**：误报 / 过滤缺陷

**问题描述**：

`_CAPABILITY_PATTERNS` 中的 `"具有.*技术"` 和 `"具有.*能力"` 会匹配合法资质：
- `具有信息技术服务资质` → 匹配 `"具有.*技术"` → **误过滤**
- `具有质量检测能力认证` → 匹配 `"具有.*能力"` → **误过滤**

`_INVOICE_PATTERNS` 中的 `"能开具"` 是纯字面匹配，会匹配任何包含这三个字的文本：
- `能开具证明的机构资质` → 匹配 `"能开具"` → **误过滤**

**已验证**（通过 python 脚本确认）。

**影响**：含"具有...技术"/"具有...能力"/"能开具"的资质名称被误过滤。

**修复建议**：与 N15 相同，添加反向检查或边界约束。

---

### N17: template_matcher bid_type 子串匹配导致"劳务管理服务类"误匹配"服务"类型 🟡

- **文件**：`core/nodes/template_matcher.py` — bid_type 过滤逻辑（第 140-176 行）
- **严重程度**：中
- **类型**：匹配缺陷

**问题描述**：

bid_type 过滤使用双向子串匹配：

```python
bid_type_stripped = bid_type.replace("类", "")
if tpl_type in bid_type_stripped or bid_type_stripped in tpl_type:
    filtered.append((template_id, distance))
```

`TEMPLATES` 字典键为：`"服务"`, `"货物"`, `"工程"`, `"集成"`, `"运维"`, `"劳务外包"`（不带"类"）。

**已验证的匹配结果**：

| bid_type | stripped | tpl_type | 匹配结果 | 正确？ |
|----------|----------|----------|---------|--------|
| `劳务管理服务类` | `劳务管理服务` | `服务` | ✅ 匹配 | ❌ 错误！ |
| `劳务管理服务类` | `劳务管理服务` | `劳务外包` | ❌ 不匹配 | ❌ 错误！ |

"劳务管理服务类"应匹配"劳务外包"类型模板（最接近的类型），但由于子串匹配，"服务"是"劳务管理服务"的子串，导致错误匹配"服务"类型模板。

**缓解因素**：`section_generator.py` 对劳务类标书跳过模板匹配（见 N21），所以此 bug 的实际影响被降低。但 `selected_template_id` 仍会被设置为错误的"服务"类型模板 ID。

**影响**：`selected_template_id` 可能被设为错误类型的模板，影响调试和潜在的下游逻辑。

**修复建议**：使用精确匹配（`tpl_type == bid_type_stripped`）或映射表（如 `{"劳务管理服务类": "劳务外包"}`），而非子串匹配。

---

### N18: feedback_form `_apply_feedback_and_regenerate` 无异常处理 🟡

- **文件**：`gui/components/feedback_form.py` — `_apply_feedback_and_regenerate()` 函数（第 20-51 行）
- **严重程度**：中
- **类型**：异常处理缺陷

**问题描述**：

```python
def _apply_feedback_and_regenerate(result: dict) -> None:
    with st.spinner("🔄 正在应用反馈并重新生成章节..."):
        from core.graph import build_generation_graph
        llm_fns = st.session_state.get("pipeline_llm_fns", {})
        generation_graph = build_generation_graph(llm_fns=llm_fns)
        phase2_state = {**result}
        phase2_state["review_status"] = ""
        new_result = generation_graph.invoke(phase2_state)  # ← 无 try/except
        st.session_state["pipeline_result"] = new_result
    st.success("✅ 已应用反馈重新生成，请审阅更新后的内容")
    st.rerun()
```

`generation_graph.invoke()` 可能抛出异常（LLM API 超时、网络错误、JSON 解析失败等），但未被捕获。异常会导致：
1. Streamlit 显示原始 traceback（不友好的错误信息）
2. `st.session_state["pipeline_result"]` 不被更新（保留旧结果）
3. `st.rerun()` 不被执行（界面停留在 spinner 状态）

同样的问题也存在于 `review_panel.py` 第 406-421 行的"驳回修改"路径。

**影响**：管道异常时用户体验差，且可能丢失已提交的反馈。

**修复建议**：用 `try/except` 包裹 `generation_graph.invoke()`，异常时显示友好错误信息并保留原始结果。

---

### N19: feedback_form `current_round` 不递增，旧反馈被重复显示为"待处理" 🟡

- **文件**：`gui/components/feedback_form.py` — `render_feedback_form()` 函数（第 54-156 行）
- **严重程度**：中
- **类型**：逻辑缺陷

**问题描述**：

用户提交反馈后点击"应用反馈并重新生成"，管道执行完毕后 `current_round` **不递增**（注释说明"由 FeedbackProcessor 管理递增"）。但 `FeedbackProcessor` 仅在 `QualityChecker` 失败时才递增 `current_round`。如果质检通过，`current_round` 保持为 0。

反馈条目的 `round` 字段设为 `current_round + 1 = 1`。管道执行后，`pending_feedback` 仍按 `round == current_round + 1 == 1` 过滤：

```python
pending_feedback = [
    fb for fb in result.get("feedback_history", [])
    if fb.get("round", 0) == current_round + 1  # current_round 仍为 0
]
```

**结果**：已应用的旧反馈仍被显示为"本轮已提交 N 条反馈"，"应用反馈并重新生成"按钮仍可点击。用户可能重复应用同一反馈，触发不必要的 LLM 调用。

此外，`max_rounds` 限制不适用于用户主动发起的重新生成路径——用户可以无限次重新生成。

**影响**：反馈状态显示错误，可能导致重复生成和 LLM 调用浪费。

**修复建议**：
1. 用户主动应用反馈后，递增 `current_round` 或标记已应用反馈（如添加 `applied: True` 字段）
2. `pending_feedback` 过滤时排除已应用的反馈
3. 考虑对用户主动重新生成也施加 `max_rounds` 限制

---

### N20: req_extractor `MAX_INPUT_CHARS=30000` 可能导致小上下文模型 OOM 🟡

- **文件**：`core/nodes/req_extractor.py` — 第 184 行
- **严重程度**：中
- **类型**：配置风险

**问题描述**：

```python
MAX_INPUT_CHARS = 30000
```

注释说明"Modern LLMs (GPT-4o, Claude, etc.) handle 128K+ token context windows"，但系统支持的 LLM 包括 DeepSeek（`deepseek-chat` 上下文窗口 32K-64K tokens，但输出限制 4K-8K tokens）。30000 中文字符约 7500-15000 tokens（含 prompt 模板和 JSON 输出预留），对 DeepSeek 来说接近上下文窗口上限。

如果 LLM 输入过长，可能触发：
1. OOM（exit code 137）——正是原来 8000 限制要避免的问题
2. 输出截断（JSON 不完整导致解析失败）
3. API 限流/超时

**影响**：使用 DeepSeek 等较小上下文模型时，大文档解析可能失败。

**修复建议**：将 `MAX_INPUT_CHARS` 改为可配置参数（从配置面板读取），或按模型动态调整（如 DeepSeek 用 15000，GPT-4o 用 30000）。

---

### N21: section_generator 对劳务类标书完全跳过模板匹配 🟢

- **文件**：`core/nodes/section_generator.py` — `_resolve_sections_from_template()` 函数（第 569-575 行）
- **严重程度**：低
- **类型**：死代码 / 设计折衷

**问题描述**：

```python
if bid_type and ("劳务管理服务" in bid_type or "劳务外包" in bid_type):
    return _get_sections_for_bid_type(bid_type)
```

劳务类标书始终使用 `_get_sections_for_bid_type()` 返回的固定 9 章结构，忽略模板匹配结果。即使 `TemplateMatcher` 返回了正确的模板，该模板的章节定义也不会被使用。

注释说明这是因为"Template matching with MockEmbedder can select wrong-type templates"，这是一个临时解决方案。

**影响**：模板匹配对劳务类标书是死计算，浪费 LLM token 和处理时间。未来如果 `MockEmbedder` 被替换为真实 embedding 模型，此 bypass 仍会阻止模板匹配生效。

**修复建议**：在 N17 修复后（精确匹配 bid_type），移除此 bypass，或改为可配置开关。

---

### N22: template_library TEMPLATES 键名与 TEMPLATE_TYPES / upload_panel 不一致 🟢

- **文件**：`core/retrieval/template_library.py` — `TEMPLATES` 字典（第 21 行）vs `core/nodes/template_matcher.py` — `TEMPLATE_TYPES`（第 27 行）
- **严重程度**：低
- **类型**：命名不一致（N08 部分修复后的残留）

**问题描述**：

N08 修复后，`upload_panel.py` 和 `TEMPLATE_TYPES` 已统一使用带"类"后缀的命名（如 `"服务类"`, `"劳务外包类"`, `"劳务管理服务类"`）。

但 `template_library.py` 的 `TEMPLATES` 字典键**仍不带"类"后缀**：

| 位置 | 键名 | 缺失 |
|------|------|------|
| `upload_panel.py` | `服务类`, `货物类`, `工程类`, `集成类`, `运维类`, `劳务管理服务类`, `劳务外包类`（7 种，带"类"） | — |
| `TEMPLATE_TYPES` | `服务类`, `货物类`, `工程类`, `集成类`, `运维类`, `劳务外包类`, `劳务管理服务类`（7 种，带"类"） | — |
| `TEMPLATES` 字典键 | `服务`, `货物`, `工程`, `集成`, `运维`, `劳务外包`（6 种，**不带"类"**） | **缺"劳务管理服务"** |

这导致：
1. `template_matcher.py` 的 bid_type 过滤需要 `bid_type.replace("类", "")` 来对齐 TEMPLATES 键名——这个 strip 操作正是 N17 子串匹配 bug 的根源
2. "劳务管理服务类"在 TEMPLATES 中没有对应键，无法构建该类型的模板索引

**影响**：命名不一致是 N17 的根本原因；`TEMPLATE_TYPES` 列表包含"劳务管理服务类"但实际没有对应模板。

**修复建议**：将 `TEMPLATES` 字典键统一为带"类"后缀，并补充"劳务管理服务类"的模板定义（或将其映射到"劳务外包类"模板）。

## 五、新 Bug 汇总

| 编号 | 严重程度 | 模块 | 简述 |
|------|---------|------|------|
| N15 | 🔴 高 | `eligibility_checker.py` | `_DOC_PATTERNS` 方案类正则过于宽泛，合法资质证书被误过滤为"软要求" |
| N16 | 🟡 中 | `eligibility_checker.py` | `_CAPABILITY_PATTERNS` / `_INVOICE_PATTERNS` 过度宽泛，合法资质被误过滤 |
| N17 | 🟡 中 | `template_matcher.py` | bid_type 子串匹配导致"劳务管理服务类"误匹配"服务"类型模板 |
| N18 | 🟡 中 | `feedback_form.py` | `_apply_feedback_and_regenerate` 无异常处理，管道异常时 UI 崩溃 |
| N19 | 🟡 中 | `feedback_form.py` | `current_round` 不递增，旧反馈重复显示为"待处理"，用户可无限重新生成 |
| N20 | 🟡 中 | `req_extractor.py` | `MAX_INPUT_CHARS=30000` 可能导致 DeepSeek 等小上下文模型 OOM |
| N21 | 🟢 低 | `section_generator.py` | 劳务类标书完全跳过模板匹配（临时 bypass 变永久死代码） |
| N22 | 🟢 低 | `template_library.py` | TEMPLATES 键名不带"类"且缺"劳务管理服务"，与 TEMPLATE_TYPES 不一致 |

## 六、修复优先级建议

| 优先级 | 编号 | 原因 |
|:--:|------|------|
| P0（立即） | N15 | 合法资质被误过滤，资质门槛检查失效，可能投标不合格项目 |
| P1（高） | N16, N17, N19 | 资质误过滤 / 模板误匹配 / 反馈状态错误 |
| P2（中） | N18, N20 | 异常处理缺失 / OOM 风险 |
| P3（低） | N21, N22 | 死代码 / 命名不一致 |

## 七、测试盲区分析

pytest 507 全通过却未发现 N15-N22，原因：

| 盲区 | 未覆盖的问题 | 原因 |
|------|------------|------|
| 资质过滤边界 | N15, N16 | 无测试验证合法证书名称是否被 `_is_generic_qual()` 误过滤 |
| bid_type 匹配边界 | N17 | 无测试验证"劳务管理服务类"的模板匹配结果 |
| GUI 异常路径 | N18 | 无测试覆盖 `generation_graph.invoke()` 抛异常时的 UI 行为 |
| 反馈状态管理 | N19 | 无测试验证用户主动应用反馈后的 `current_round` 和 `pending_feedback` 状态 |
| LLM 上下文限制 | N20 | 无测试验证大输入对 DeepSeek 等模型的影响 |
| 模板匹配 bypass | N21 | 无测试验证劳务类标书是否使用模板匹配结果 |

## 八、补充说明

- **本轮审查基于 git diff 未提交变更**，不包括已提交但未 push 的代码。
- **N15/N16 通过 python 脚本动态验证**，确认了 5/10 合法资质被误过滤。
- **N17 通过 python 脚本动态验证**，确认了"劳务管理服务类"与"服务"类型的误匹配。
- **N18-N22 为静态代码审查发现**，未动态复现，但逻辑分析确认问题存在。
- **BUG-04/06/07/11 仍未验证**，涉及 FAISS 持久化和 evolution 模块，建议后续专项测试。
