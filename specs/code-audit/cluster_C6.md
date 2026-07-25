# 簇 C6 — 审计发现（检索/RAG 模块 vs system-workflow.md）

> 审计日期：2026-07-20
> 审计范围：bid-agent/core/retrieval 下 6 个源码文件 + system-workflow.md
> 方法：逐行 Read 比对规范条款与代码实现；所有「缺失/错误」均附 file:line 证据

---

## 1. 文件清单（路径 + 行数 + 职责）

| 文件 | 行数 | 职责 |
|------|------|------|
| bid-agent/core/retrieval/embeddings.py | 137 | Embedding 抽象层：BaseEmbedder / OpenAIEmbedder / MockEmbedder / get_embedder 工厂 |
| bid-agent/core/retrieval/faiss_index.py | 249 | VectorIndexManager：FAISS HNSW（优先）/ numpy 暴力（回退）向量索引、CRUD、search、save/load |
| bid-agent/core/retrieval/pipeline.py | 139 | RetrievalPipeline + reciprocal_rank_fusion（RRF 融合）；§4.2 六步管线载体 |
| bid-agent/core/retrieval/reference_retriever.py | 255 | ReferenceRetriever：RAG 参考范文注入；语义(FAISS)/关键词双模式回退 |
| bid-agent/core/retrieval/template_library.py | 266 | 7 类标书模板定义 + build_template_index（构建每类 FAISS 索引） |
| bid-agent/core/retrieval/subtype_router.py | 511 | 劳务外包子类型三层识别、_shared 准入、检索权重表、冷启动、manifest 版本管理 |
| system-workflow.md | 716 | 系统规范：⑥ TemplateMatcher、§4.2 FAISS 管线、§4.4 知识库结构、子类型权重表 |

---

## 2. 模块实现对照表

| 规范条款 | 代码位置 (file:line) | 状态 | 说明 |
|----------|----------------------|------|------|
| ⑥ Embedding = OpenAI text-embedding-3-small | embeddings.py:53, 44-48 | ✓ | 默认 model 与 MODEL_DIMS 一致（3-small→1536） |
| ⑥/§4.2 1536 维向量 | faiss_index.py:15 (DEFAULT_DIM=1536) | ✓ | 默认维正确，但 template_library 未用 |
| §4.2 步骤1 Query 向量化 | pipeline.py:106 | ✓ | `embedder.embed_query(query)` |
| §4.2 步骤2 多索引并行搜索 | pipeline.py:117-120 | △低 | 顺序 for 循环，注释称"conceptually parallel"，非真实并行 |
| §4.2 步骤3 RRF 融合 `score(d)=Σ1/(k+rank_i(d))` | pipeline.py:26-55, 48 | ✓ | 公式正确，rank 从 1 起，`1.0/(k+rank)` |
| §4.2 步骤4 去重 | pipeline.py:44-51 | ✓(隐式) | RRF 用 dict 按 doc_id 合并，天然实现跨索引去重 |
| §4.2 步骤5 元数据过滤 | pipeline.py:125-128 | ✗缺失 | `if metadata_filter: pass` —— 纯占位 no-op，注释"metadata stored externally in Phase 3" |
| §4.2 步骤6 截断 Top-K | pipeline.py:52-53, 123 | ✓ | `top_n=final_k`（默认 3） |
| ⑥ 子类型权重表三档（0/1-4/≥5） | subtype_router.py:256-275 | ✓ | {subtype,shared,neighbor} 三档与规范 263-267 完全一致 |
| ⑥ `_shared` 严格准入 | subtype_router.py:67-75, 247-253 | ✓(部分) | 白名单存在；但仅检索期过滤，无入库强制；键含 sec/ch 混用 |
| ⑥ 冷启动回退 `_shared/chapters` + 最相似子类型 | subtype_router.py:274-275, 322-368；reference_retriever.py | ✗错误 | `neighbor` 权重 0.3 已计算但**从未被任何检索路径消费**；reference_retriever 未检索最近邻子类型 |
| ⑥ FAISS 语义检索 Top-3 模板（1536 维） | template_library.py:211, 215 | ✗错误 | 使用 `MockEmbedder(dim=64)` + `VectorIndexManager(dim=64)`，**非 1536 维 OpenAI 向量** |
| ⑦ RAG 注入：FAISS 检索同类型历史中标方案 | reference_retriever.py:204-217 | ✗错误 | 语义分支仅 `logger.debug` 打印 ID，"doc store isn't wired yet"，随即回退关键词，**未真正注入语义文本** |
| ⑥ 向量余弦相似度 Top-K | faiss_index.py:54；embeddings.py:86 | ✗错误 | 用 `IndexHNSWFlat`（默认 L2 平方距离），OpenAIEmbedder 返回向量**未归一化** → 非余弦相似度 |
| TemplateMatcher 接线 RRF 管线 | pipeline.py(独立) / template_library.py | 需人工确认 | 作用域内未提供 TemplateMatcher.py，未见其调用 RetrievalPipeline 或 OpenAIEmbedder |

---

## 3. 错误 / 缺陷明细

### 高
- **H1 元数据过滤未实现** — `pipeline.py:125-128`。§4.2 明确列"元数据过滤"为第 5 步，代码为 `if metadata_filter: pass` 占位，且截断（`top_n`）在融合时即发生、早于（不存在的）过滤，违反"过滤→截断"顺序。RAG 结果无法按 bid_type/subtype 等元数据裁剪。
- **H2 模板库维度/模型与规范严重不符** — `template_library.py:211`（`MockEmbedder(dim=64)`）、`:215`（`VectorIndexManager(dim=64)`）。规范 ⑥ 要求 text-embedding-3-small 1536 维。后果：① 模板向量为随机向量，语义匹配实质失效；② 与 `DEFAULT_DIM=1536` 及 `OpenAIEmbedder`（1536）维度不兼容，若用真实 embedder 检索将直接 shape 报错。
- **H3 RAG 语义检索为空操作** — `reference_retriever.py:204-217`。语义分支取得 `doc_id` 后仅 debug 日志，注释承认"full text retrieval requires a doc store which isn't wired yet"，然后必然落回关键词检索。即规范 ⑦ "从知识库 FAISS 检索同类型历史中标方案优质段落注入 prompt" 未落地。
- **H4 子类型权重表 / 最近邻权重为死代码** — `subtype_router.py:256-275`（`get_retrieval_weights` 返回 {subtype,shared,neighbor}）、`reference_retriever.py` 检索路径。计算出的 `neighbor`(0.3) 与 `shared`(0.7) 权重**无任何调用方消费**；reference_retriever 仅做"子类型 chunks → _shared → 范文"的顺序回退，未做加权融合，也未在冷启动检索"最相似子类型"。规范 ⑥ 冷启动三档回退未真正实现。

### 中
- **M1 距离度量错误（L2 而非余弦）** — `faiss_index.py:54`（`IndexHNSWFlat` 默认 L2）、`embeddings.py:86`（OpenAI 向量未归一化）。规范 ⑥ 要求"向量余弦相似度 Top-K"。MockEmbedder 归一化后 L2 与余弦单调等价，但 OpenAIEmbedder 不归一化，L2≠余弦，影响真实检索质量。
- **M2 RRF 管线与模板/参考检索脱钩** — `pipeline.py` 的 `RetrievalPipeline` 独立存在，但 `template_library.build_template_index` 与 `reference_retriever` 均未使用它（前者单类单索引、后者文件系统遍历）。
- **M3 截断早于过滤** — `pipeline.py:123` 在 RRF 内已 `top_n=final_k` 截断，规范顺序是"RRF→去重→过滤→截断"。因 H1 过滤为 no-op，当前无实际危害，但顺序与规范不符。

### 低
- **L1 多索引未真正并行** — `pipeline.py:117-120` 顺序循环，注释"conceptually; FAISS is single-threaded here"。
- **L2 未知模型名静默回退 1536** — `embeddings.py:79` `MODEL_DIMS.get(self.model, 1536)`；若误传 `text-embedding-3-large`(3072) 会返回错误维度。
- **L3 `_shared` 准入仅检索期生效 + 键名混用** — `subtype_router.py:67-75` 白名单同时含 `sec1_bid_letter`/`ch1_letter` 等 sec/ch 命名；且只在 `reference_retriever.py:95` 检索时判断，无入库期强制约束。

---

## 4. 遗漏功能（规范有、代码无）

1. **元数据过滤能力**（§4.2 步骤5）—— `pipeline.py:125-128` 占位未实现。
2. **最近邻子类型检索**（⑥ 冷启动"最相似子类型推荐"）—— `get_retrieval_weights` 计算 neighbor 权重但无检索消费。
3. **模板库真实 OpenAI 1536 维 embedding** —— `template_library.py` 全程 MockEmbedder/64 维。
4. **RRF 管线与 TemplateMatcher/ReferenceRetriever 的接线** —— `RetrievalPipeline` 未被上层检索节点调用。
5. **语义检索 doc store（ID→正文映射）** —— `reference_retriever.py:212-214` 自承未接线，导致语义 RAG 失效。

---

## 5. 跨节点 / 横切问题

- **维度不一致（核心集成缺陷）**：规范与 `DEFAULT_DIM` 要求 1536，但 `template_library.py` 用 64 维。template 索引（64）与真实 OpenAIEmbedder（1536）无法互通，是 H2/M2 的根因。
- **权重表死代码**：`subtype_router.get_retrieval_weights`（含 neighbor）返回结构化权重，但下游 reference_retriever 走顺序回退、pipeline 走 RRF，均未读取该权重 → 规范 ⑥ 三档动态权重**仅"算而不用"**。
- **语义与关键词检索脱节**：reference_retriever 名义双模式，语义模式实际为空，所有流量落关键词文件扫描（`_find_reference_files` 的 rglob + 子串匹配），与"FAISS 语义检索"定位不符。
- **HNSW 近似性 vs 模板少样本**：`faiss_index.py:54` 用 HNSWFlat（近似），对每类仅 3-4 个模板的精确匹配场景，应优先用 `IndexFlatIP`（归一化后精确余弦），近似可能漏召回。

---

## 6. 需人工确认项

1. **TemplateMatcher.py 未在本次作用域**：规范 ⑥ 的 FAISS 语义检索 Top-3 由 TemplateMatcher 实现，但其源码未提供，无法确认其是否使用 `OpenAIEmbedder(1536)` + `RetrievalPipeline` + RRF，或另起炉灶。
2. **`_shared` 白名单键名 sec/ch 混用**（subtype_router.py:67-75）：`sec1_bid_letter` 与 `ch1_letter` 是否指向同一章节？是否与 SectionGenerator（⑦ 用 ch1_letter 等）键空间一致，需统一确认。
3. **实际知识库目录结构**：规范 §4.4 描述 `_shared/chapters/`、`{子类型}/chunks|patterns|prompts|manifest.json`，需确认磁盘真实结构是否匹配 `reference_retriever._find_reference_files` 的搜索路径（如 `_shared/chapters`、`范文`）。
4. **`get_embedder` 默认 provider="openai" 但无 API key 时**：生产是否真能调用 OpenAI？否则全链路（template_library 已用 Mock）与运行时 embedder 可能不一致。
5. **RRF 截断 `final_k=3` 是否与 ⑥ "matched_templates Top-3" 完全一致**：pipeline 默认 final_k=3 对齐，但 template 匹配是否有独立 Top-K 配置需确认。
