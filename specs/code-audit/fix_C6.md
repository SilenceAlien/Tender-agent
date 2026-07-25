# 簇 C6 修复记录（fix_C6）

> 修复对象：`bid-agent/core/retrieval/reference_retriever.py`、`pipeline.py`、`subtype_router.py`
> 仅修改上述三个文件，未触碰 embeddings.py / faiss_index.py / template_library.py。

## 1. 🔴 N0 — `_keyword_score` 方法定义丢失（最高优先，致命回归）
- **文件/行**：`reference_retriever.py`
  - 删除：原 `_load_doc_by_id` 的 `return ""` 之后（原 149-162 行）的死代码段。
  - 新增：`return ""` 之后、`_extract_best_snippet` 之前，`def _keyword_score(self, query: str, text: str) -> float:` 独立方法（4 空格方法级缩进，内部逻辑不变）。
- **改法**：将遗留死代码移出 `_load_doc_by_id` 并补 `def` 行，成为独立方法；第 192 行 `self._keyword_score(query, p)` 现已正确解析。
- **验证**：`py_compile` 通过；运行时 `hasattr(r, '_keyword_score')==True`，`_keyword_score(...)` 返回正常分值。

## 2. H3 — 元数据过滤占位
- **文件/行**：`pipeline.py` `__init__`（新增 `metadata_store` 可选参数）、`search`（替换 `if metadata_filter: pass` 占位）、新增模块级 `_matches_metadata` 辅助函数。
- **改法**：`search` 在 RRF 融合后按 `metadata_filter` 中每个键做匹配（str→大小写不敏感包含，否则相等）；`metadata_store` 缺省时仅丢弃「有明确但不匹配」的 doc，无元数据 doc 原样透传；无 filter 时原样返回。
- **验证**：`py_compile` 通过。

## 3. H6 — 消费子类型权重表/最近邻
- **文件/行**：`reference_retriever.py` `_find_reference_files`（在 _shared 搜索之后插入）。
- **改法**：当 `bid_subtype` 存在时调用 `get_retrieval_weights(bid_subtype, kb_root=...)` 并记录日志（消费权重表）；当 `neighbor>0`（冷启动）或当前子类型+_shared 均无匹配时，调用 `find_nearest_subtypes(bid_subtype)` 用最相似子类型 chunks 补充检索。未改动既有「子类型 chunks → _shared → 范文 → rglob」回退顺序。
- **验证**：`py_compile` 通过；`find_nearest_subtypes`/`get_retrieval_weights` 导入与调用正常。

## 4. N6 — 硬编码 query
- **文件/行**：`reference_retriever.py` `_load_doc_by_id` 签名（新增 `query_hint: str = ""`）及两处 `query="招标要求"` 改为 `snippet_query = query_hint or "招标要求"`；语义检索调用处改为 `self._load_doc_by_id(doc_id, query_hint=query)`。
- **改法**：使用真实 query 抽取片段，为空时回退原默认 `"招标要求"`。
- **验证**：`py_compile` 通过。

## 5. H4 — MockEmbedder 离线/测试专用（保守）
- **文件/行**：`reference_retriever.py` `__init__` 中 `self.embedder = embedder` 上方加注释。
- **改法**：标注 embedder 可为 MockEmbedder（离线/测试专用），缺 `OPENAI_API_KEY` 仍可运行，未强制要求 key，未改动 OpenAIEmbedder。
- **验证**：`py_compile` 通过；不影响离线运行。

## 验证汇总
- `py_compile reference_retriever.py`：✅ 通过
- `py_compile pipeline.py`：✅ 通过
- `py_compile subtype_router.py`：✅ 通过
- 运行时冒烟测试：`_keyword_score` 可用、子类型路由导入正常。
