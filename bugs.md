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
