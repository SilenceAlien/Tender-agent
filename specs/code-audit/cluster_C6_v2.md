# 簇 C6 — 复审计报告（v2）

> 审计对象（同 C6）：`bid-agent/core/retrieval/` 下 `embeddings.py`、`faiss_index.py`、`pipeline.py`、`reference_retriever.py`、`template_library.py`、`subtype_router.py`，对照 `system-workflow.md`（⑥ TemplateMatcher FAISS、§4.2 检索管线、§4.4 知识库结构、子类型权重三档）。
> 审计方法：逐行 Read 上述 6 文件 + 规范，并 Grep 全仓确认调用方；逐项核对 C6 簇 5 个旧问题是否已修复，并查找回归/遗漏。
> 结论概览：**5 项旧问题中 2 项已修复（L1、H5），1 项部分修复（H4 维度已改 1536 但仍用 MockEmbedder），2 项未修复（H3 元数据过滤、H6 权重表死代码）**；并发现 **1 项高危回归 N1（关键词检索路径崩溃）**。

## 1. 文件清单（路径 + 行数 + 一句话职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| `bid-agent/core/retrieval/embeddings.py` | 137 | 嵌入接口：OpenAIEmbedder / MockEmbedder + 工厂 |
| `bid-agent/core/retrieval/faiss_index.py` | 270 | VectorIndexManager：FAISS HNSW / numpy 兜底，增删改查 + 持久化 |
| `bid-agent/core/retrieval/pipeline.py` | 144 | RetrievalPipeline：多索引并行 + RRF 融合 + 元数据过滤（占位） |
| `bid-agent/core/retrieval/reference_retriever.py` | 286 | ReferenceRetriever：RAG 语义检索 + 关键词兜底 |
| `bid-agent/core/retrieval/template_library.py` | 266 | 7 类标书模板库 + FAISS 索引构建 |
| `bid-agent/core/retrieval/subtype_router.py` | 511 | 子类型三层识别 + _shared 准入 + 权重三档 + 冷启动 |
| `system-workflow.md` | 716 | 系统工作流规范（对照基准） |

## 2. 旧问题修复核对表（C6 簇）

| 旧问题 | 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|--------|----------|---------------------|------|------|
| **H3** 元数据过滤 `if metadata_filter: pass` 纯占位 | §4.2 第5步「元数据过滤」 | `pipeline.py:125-133` | ❌ **未修复** | 仍为 `if metadata_filter: … pass`（注释 "filtering disabled … pass-through all results"），无任何过滤逻辑；RRF 融合后直接返回。规范要求的元数据过滤步骤未落地。 |
| **H4** 模板库 `MockEmbedder(dim=64)` | ⑥ OpenAI text-embedding-3-small 1536维 | `template_library.py:211`、`215` | ⚠️ **部分修复** | 维度已改为 1536（`:211 MockEmbedder(dim=1536)`、`:215 VectorIndexManager(dim=1536)`），与规范维度一致；**但仍使用 `MockEmbedder`（随机单位向量）而非 OpenAI Embedder**，模板向量与运行期查询向量均为随机、彼此无语义关联 → 语义匹配结果近似随机。规范「FAISS 语义检索 = OpenAI 1536维」未真正达成。 |
| **H5** RAG 语义检索空操作（doc store 未接） | ⑦ RAG 注入 / ⑥ 语义检索 | `reference_retriever.py:121-148`、`:232-248` | ✅ **已修复** | 新增 `_load_doc_by_id(doc_id)` 按 `kb_root/doc_id`（含 rglob 兜底）从知识库装载 doc 片段；语义路径在 `:232-244` 取得 doc_id 后调用之并返回 snippet，原 "doc store isn't wired yet" 注释已删除。代码层面语义检索已接通。 |
| **H6** 权重表 `{subtype,shared,neighbor}` 无调用方 / 冷启动最近邻未实现 | ⑥ 权重三档 + 冷启动最近邻 | `subtype_router.py:256-275`、`289-319`；`reference_retriever.py:62-119` | ❌ **未修复** | Grep 全仓确认：`get_retrieval_weights`（`:256`）**无任何调用方**；`find_nearest_subtypes`（`:289`）仅被 `get_cold_start_info`（`:356`，仅供 InfoVerificationGate 展示用）消费，未进入检索任何融合逻辑。`reference_retriever` 仍走「子类型 chunks → _shared → 范文」顺序回退（`:84-117`），未做加权融合，也未按 `neighbor` 权重检索「最相似子类型」。规范三档动态权重「算而不用」，冷启动最近邻检索未真正落地。 |
| **L1** 距离度量用 L2 非余弦 | ⑥ 余弦相似度 | `faiss_index.py:54-58`、`:82-86`、`:192-195` | ✅ **已修复** | FAISS 索引改为 `IndexHNSWFlat(dim, m, faiss.METRIC_INNER_PRODUCT)`（`:54-56`）；`add` 时对向量归一化（`:82-86`），`search` 时对 query 归一化（`:192-195`）→ 内积即余弦相似度。`_brute_force_search` 同样归一化后做内积（`:156-158`）。距离度量已改为余弦。 |

## 3. 规范整体符合度核对

| 规范条款 | 状态 | 证据 / 说明 |
|----------|------|------------|
| ⑥ OpenAI text-embedding-3-small 1536维 | ⚠️ 部分 | 维度全链路 1536（`embeddings.py:44-48`、`faiss_index.py:15`、`template_library.py:211,215`），但模板库与检索运行期默认仍用 `MockEmbedder` 随机向量，未接 OpenAI（见 H4）。「OpenAI」语义未实现。 |
| §4.2 六步管线：向量化→多索引并行→RRF→去重→元数据过滤→截断TopK | ⚠️ 部分 | 向量化 `:106`、多索引并行 `:117-120`、RRF `:123`（`reciprocal_rank_fusion` 公式 `1/(k+rank)` 正确，`pipeline.py:26-55`）、去重（RRF 天然去重）、截断 `final_k` 均实现；**第5步元数据过滤未实现（H3）**。 |
| ⑥ 子类型权重三档 + _shared 严格准入 + 冷启动回退 | ⚠️ 部分 | `_shared` 严格准入 `is_chapter_allowed_in_shared` 已落地并被 `reference_retriever.py:94-95` 消费（✓）；三档权重表 `get_retrieval_weights` 已计算但**无任何消费方（H6 ✗）**；冷启动最近邻仅展示未用于检索（H6 ✗）。 |
| ⑥ 模板库 Top-3 检索 | ✅ | `RetrievalPipeline` 默认 `final_k=3`（`pipeline.py:86`），`build_template_index` 每类 ≥3 模板（`template_library.py:259-265` 测试）；Top-3 产出符合规范。 |
| §4.4 知识库结构（_shared / 子类型分区） | ✅ | `reference_retriever._find_reference_files` 搜索优先级（子类型 chunks → _shared/chapters → 范文 → rglob）与规范 §4.4 结构一致。 |

## 4. 新发现问题（回归 / 遗漏）

### 高
- **N1 — ⚠️ 回归：`_keyword_score` 方法定义丢失，关键词检索路径必崩（AttributeError）**
  `reference_retriever.py:149-162` 本应是 `_keyword_score(self, query, text)` 方法体，却被错误地遗留在 `_load_doc_by_id`（`:121-148`）的 `return ""`（`:148`）**之后**、且**缺少 `def _keyword_score(self, query, text):` 行**。Grep 全仓确认：除 `:192` 的调用 `self._keyword_score(query, p)` 外，**没有任何 `def _keyword_score` 定义**。
  - 后果：该段为 `_load_doc_by_id` 内的死代码（return 之后永不可达）；同时真正的 `_keyword_score` 方法不存在。当 `_extract_best_snippet`（`:164-210`，关键词兜底主路径）执行到 `:192` 时，`self._keyword_score` → **AttributeError 崩溃**。
  - 影响范围：关键词检索是「无 embedder/faiss_index 时」以及「语义检索失败后回退」的**主路径**（`reference_retriever.py:250-266`）。即默认 / 离线场景下文检索直接抛异常，RAG 注入失败。属上一轮接 doc store 时引入的回归。
  - 修复：将 `:149-162` 移出 `_load_doc_by_id`，补 `def _keyword_score(self, query: str, text: str) -> float:` 并以方法级缩进（4 空格）定义为独立方法。

### 中
- **N2 — 模板库 / 检索仍用 MockEmbedder（随机向量），OpenAI 语义未落地（同 H4 延续）**
  `template_library.py:18,211` 与 `reference_retriever` 若传入 MockEmbedder，则模板匹配与语义检索结果为随机。规范 ⑥ 明确要求 OpenAI text-embedding-3-small。建议：在生产路径注入 `OpenAIEmbedder`（需 `OPENAI_API_KEY` / 代理 `base_url`），并对 Mock 使用显式标注为「离线测试专用」。

### 低 / 运行时风险（需人工确认）
- **N3 — `_load_doc_by_id` 硬编码 query="招标要求"** `reference_retriever.py:134,144`：语义检索装载片段时用固定 query 抽取段落，而非实际 `query_hint`，导致语义 snippet 与真实查询不相关（质量劣化，非崩溃）。
- **N4 — H5 语义检索运行期依赖索引已按「知识库相对路径」灌装 doc_id（运行时风险）** `reference_retriever.py:121-148` 已接 doc store，但仅在 RAG 的 `faiss_index` 被灌装了「等于 `knowledge_base` 下相对路径」的 doc_id 时才生效；当前代码库未见该索引的构建/灌装入口。若索引为空或 doc_id 非路径格式，语义路径仍静默回退关键词（N1 未修前还会崩）。**需人工确认**检索索引的构建流程与 doc_id 命名约定。
- **N5 — MockEmbedder 未归一化影响跨提供方一致性（提示）** `embeddings.py:99-104` MockEmbedder 已返回单位向量（与 faiss_index 归一化互补，无碍）；但若替换为未归一化的 OpenAI 向量，`faiss_index.add/search` 已统一归一化（L1 修复保证），无额外风险。

## 5. 修复状态结论

| 项目 | 结论 |
|------|------|
| 已修复 | **L1**（余弦度量，`faiss_index.py:54-58`+归一化）、**H5**（RAG doc store 接通，`reference_retriever.py:121-148,232-244`） |
| 部分修复 | **H4**（维度改 1536 但仍是 MockEmbedder 随机向量，`template_library.py:211,215`） |
| 未修复 | **H3**（元数据过滤仍占位 `pipeline.py:125-133`）、**H6**（权重表 `get_retrieval_weights` 无调用方、`find_nearest_subtypes` 仅展示未用于检索，`subtype_router.py:256-275,289-319`；`reference_retriever.py:62-119` 顺序回退未加权） |
| 新缺陷 | **N1 高危回归**：`_keyword_score` 定义丢失 → 关键词检索（主/兜底路径）运行期 AttributeError 崩溃（`reference_retriever.py:149-162` 死代码 + `:192` 调用无定义） |

**优先处理建议**：① 立即修 N1（否则离线/兜底检索崩溃）；② 落实 H3 元数据过滤、H6 权重表消费与冷启动最近邻检索；③ 将 MockEmbedder 切换为 OpenAIEmbedder（生产路径）或显式声明离线降级策略（N2）。
