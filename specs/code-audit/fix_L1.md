# L1 修复记录（fix_L1）

> 修复对象：`bid-agent/core/retrieval/faiss_index.py`、`embeddings.py`
> 结论：**L1 在当前代码中已修复（现状即符合余弦相似度要求），无需改动源码。**
> 与审计报告 `cluster_C6_v2.md` §2 L1 行「✅ 已修复」一致。

## 1. 现状核查（改了哪几行 / 改法）

经逐行核查，距离度量**当前已为余弦相似度**，并非任务描述的 L2。关键证据如下：

| 修复点 | 文件 / 行 | 现状（已符合） |
|--------|-----------|----------------|
| 索引度量 | `faiss_index.py:53-58` `_create_faiss_index` | 使用 `faiss.IndexHNSWFlat(dim, m, faiss.METRIC_INNER_PRODUCT)`（内积）；归一化后内积 = 余弦相似度。等效于 `IndexFlatIP`（HNSW 为近似但排序正确）。 |
| 建索引归一化 | `faiss_index.py:82-86` `add` | 对入库向量做 `vec / np.linalg.norm(vec, axis=1, keepdims=True)`（零范数用 `np.maximum(norms,1e-10)` 兜底），再 `add` 到索引。 |
| 查询归一化 | `faiss_index.py:191-195` `search` | 对 query 向量同样 L2 归一化后再 `search`；返回距离按 `1 - 内积` 转回距离。 |
| 兜底 numpy | `faiss_index.py:143-181` `_brute_force_search` | 同样先归一化再 `q_norm @ v_norm.T` 计算余弦，零向量有保护分支。 |
| remove 重建 | `faiss_index.py:125-128` `remove` | 重建索引时对保留向量再次归一化。 |
| load 重建 | `faiss_index.py:252-255` `load` | 从 .npz 载入后重建索引时再次归一化。 |
| OpenAIEmbedder | `embeddings.py:81-86` | 返回原始 1536 维向量，由索引层统一归一化（正确分工）。 |
| MockEmbedder | `embeddings.py:99-104` | 返回单位向量（已归一化），与索引层归一化互补、双重归一化无害，离线 1536 维随机向量安全。 |

对照任务 4 项要求：① 已用内积+归一化实现余弦 ✓；② 建索引与查询两处均归一化 ✓；③ MockEmbedder 随机向量归一化安全、不影响离线 ✓；④ `search`/`embed` 接口签名未变 ✓。

## 2. 验证结果

- `py_compile faiss_index.py embeddings.py`：✅ 通过（无语法错误）。
- 独立脚本（已删除）：构造 3 个归一化文档向量 + 1 个靠近 doc[2] 的 query，`faiss.IndexFlatIP` 返回顺序 `[2,0,1]` 与真实余弦相似度排序一致 → **PASS**。
- 模块级冒烟（`VectorIndexManager` + `MockEmbedder`，dim=8）：构造接近 "a" 的 query，`search` 返回 Top1 为 `"a"` → **PASS，按余弦排序**。

## 3. 最终处置

L1 已满足，本次**未修改任何源码**，避免对正确代码引入回归风险。若后续需「精确（非近似）余弦」，可将 `IndexHNSWFlat(..., METRIC_INNER_PRODUCT)` 改为 `IndexFlatIP(dim)`（`faiss_index.py:56`），其余归一化逻辑不变即可。
