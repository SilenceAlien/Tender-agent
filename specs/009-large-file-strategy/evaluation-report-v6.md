# 超大招标文件读取策略 v6 — 综合评估报告

> **评估日期**：2026-07-12
> **评估方式**：逐模块对比 v5 评估报告 + 代码库交叉验证
> **方案版本**：v6 修订版
> **评估范围**：只做评估，不做执行

---

## 一、评分总览

| # | 评估模块 | v5 评分 | v6 评分 | 变化 | 核心结论 |
|---|---------|---------|---------|------|---------|
| 1 | 上传与预检 | 7/10 | **8/10** | +1 | C-01 document_id 修复有效，但临时文件清理仍缺失 |
| 2 | 流式解析与倒排索引 | 6/10 | **8/10** | +2 | C-05/C-10/C-12/C-13 全部修复，但反向索引内存未计入 |
| 3 | 磁盘持久化 | 6/10 | **8/10** | +2 | C-06/C-07 修复有效，但双写开销 + 进度回调用特殊值未处理 |
| 4 | 按需检索与 ReqExtractor | 7.5/10 | **8.5/10** | +1 | LRU 扩容 + per_page_limit 差异化有效 |
| 5 | OCR 集成 | 6.5/10 | **6/10** | -0.5 | C-09 子进程方案引入致命性能回归 + C-08/C-09 互相矛盾 |
| 6 | 并发控制与节点适配 | 6/10 | **7.5/10** | +1.5 | C-03 per-task 信号量修复有效，但快照探测仍有竞态 |
| 7 | 辅助函数与实施路径 | 7/10 | **8/10** | +1 | C-10/C-11 补全 + Phase 0/11 新增，但死代码 + 重复定义仍在 |

**综合评分：7.7/10**（v5: 6.6/10，提升 1.1 分）

---

## 二、v5 Critical 问题修复状态

### 全部 13 个 Critical 问题已修复 ✅

| # | v5 问题 | v6 修复方案 | 验证结果 |
|---|---------|-----------|---------|
| C-01 | document_id 依赖临时路径 | 改用 md5(filename + file_size + 前1MB内容hash) | ✅ 正确 — 同一文件重复上传生成相同 ID |
| C-02 | sections 字段类型冲突 | 重命名为 chapter_index | ✅ 正确 — 与 AgentState 顶层 sections:dict[str,str] 不再冲突 |
| C-03 | _calc_ocr_workers 竞态 | per-task 信号量，acquire 后持有 + finally release | ✅ 正确 — _ocr_pre_rendered 中包裹信号量 |
| C-04 | AgentState TypedDict 未更新 | 扩展 TypedDict + factory_state 默认值 | ✅ 正确 — 与现有 state.py 结构一致 |
| C-05 | bigram 英数处理 bug | 统一 _tokenize_query 切片逻辑，英数整词 | ✅ 正确 — ISO9001 搜索不再失败 |
| C-06 | _serialize_state deepcopy | 检测 streaming 模式跳过全文深拷贝 | ✅ 正确 — 对 documents 列表逐 doc 检查 parse_mode |
| C-07 | PageBlobReader 写入未集成 | _stream_parse_pdf 统一使用 PageBlobReader | ✅ 正确 — 但引入双写开销（见 NC-03） |
| C-08 | PaddleOCR 线程安全 | 线程级实例池 | ⚠️ 方向正确，但与 C-09 互相矛盾（见 NC-02） |
| C-09 | _ocr_with_timeout 不可靠 | 改用 multiprocessing.Process | ⚠️ 方向正确，但引入致命性能回归（见 NC-01） |
| C-10 | _detect_chapter_boundary 未定义 | 补全实现 + TOC 优先 + 正则回退 + 降级 | ✅ 正确 — 完整可运行 |
| C-11 | _build_meta 调用链断裂 | 5 元组返回 + 透传 probe_info 等 | ✅ 正确 — 但 §13.2 未同步更新（见 NC-04） |
| C-12 | 进度文件非原子写 | tmp + os.replace + 每50页增量落盘 | ✅ 正确 — 标准原子写入模式 |
| C-13 | remove_page O(N) 性能 | 反向索引 _page_to_tokens: dict[int, set[str]] | ✅ 正确 — O(K) 替代 O(N) |

### v5 P2 优化项修复状态

| 优化项 | 状态 | 说明 |
|--------|------|------|
| LRU 容量 50→200 | ✅ 已完成 | 200×3000字符≈600KB 仍可控 |
| per_page_limit 差异化 | ✅ 已完成 | scoring/qual=1200, tech=800, format=600 |
| 二次检索按章节索引 | ❌ 未完成 | 仍扫描前 100 页，未利用 chapter_index |
| QualityChecker 公司名用倒排索引 | ✅ 已完成 | search_pages(company_suffixes, top_k=50) |
| 磁盘预检 2GB→3GB | ✅ 已完成 | — |
| 验收标准统一召回率 90% | ✅ 已完成 | §1.3 和 §九 均改为 90% |
| PaddleOCR 版本锁定 | ✅ 已完成 | paddleocr==2.7.0.3, paddlepaddle==2.6.1 |
| 离线模型预置 | ✅ 已完成 | scripts/download_ocr_models.py |
| Phase 0 + Phase 11 | ✅ 已完成 | 总计 33-42 天 |

---

## 三、v6 新引入的问题

### NC-01：C-09 子进程方案引入致命性能回归 🔴

**严重程度**：Critical — OCR 阶段耗时可能增加 10-50 倍

C-09 将 `_ocr_with_timeout` 改为 `multiprocessing.Process`，每个 OCR 调用启动一个新子进程。子进程内部调用 `_get_ocr_engine()` 创建独立 PaddleOCR 实例（因为线程级实例池 `_ocr_engine_pool` 在子进程中为空）。

**问题**：PaddleOCR 模型加载需要 10-30 秒。如果每页 OCR 都启动新进程：

| 扫描页数 | 模型加载时间 | OCR 推理时间 | 总时间 | v6 vs v5 |
|---------|------------|------------|--------|---------|
| 30 页 | 30×20s = 10 分钟 | 30×2s = 1 分钟 | ~11 分钟 | v5: ~1 分钟 → **11x 慢** |
| 150 页 | 150×20s = 50 分钟 | 150×2s = 5 分钟 | ~55 分钟 | v5: ~5 分钟 → **11x 慢** |
| 750 页 | 750×20s = 250 分钟 | 750×2s = 25 分钟 | ~275 分钟 | v5: ~25 分钟 → **11x 慢** |

**影响**：750 页扫描 PDF 从 v5 预估的 25-40 分钟暴涨到 4.5+ 小时，远超成功标准（≤30 分钟）。

**修复建议**：改用 `multiprocessing.Pool` 或 `concurrent.futures.ProcessPoolExecutor`，在 `initializer` 中预加载 PaddleOCR 引擎，worker 进程在整个 OCR 批次内复用同一实例：

```python
from concurrent.futures import ProcessPoolExecutor

def _ocr_worker_init():
    """ProcessPool initializer — 每个 worker 进程启动时加载一次 PaddleOCR."""
    global _worker_ocr_engine
    _worker_ocr_engine = _get_ocr_engine()

def _ocr_worker_task(page_idx, img_bytes, img_shape, img_dtype, parsed_dir):
    """在已初始化的 worker 进程中执行 OCR（复用预加载的引擎）."""
    # ... 使用 _worker_ocr_engine.ocr(img_array) ...

with ProcessPoolExecutor(
    max_workers=max_workers,
    initializer=_ocr_worker_init,
) as executor:
    futures = [executor.submit(_ocr_worker_task, idx, ...) for idx, ... in pre_rendered]
```

---

### NC-02：C-08 与 C-09 互相矛盾 🔴

**严重程度**：Critical — 两个修复方案在架构层面冲突

| 修复 | 方案 | 适用场景 |
|------|------|---------|
| C-08 | 线程级实例池 `_ocr_engine_pool: dict[int, PaddleOCR]` | 多线程 OCR |
| C-09 | `multiprocessing.Process` 每页启动子进程 | 进程级超时 |

**矛盾**：C-08 的实例池是进程内字典，子进程有自己的内存空间，无法访问父进程的 `_ocr_engine_pool`。因此：
- 如果 OCR 在子进程中执行（C-09），C-08 的实例池完全无用
- 如果 OCR 在线程中执行（C-08 生效），C-09 的子进程超时机制不适用

**当前代码状态**：`_ocr_batch_parallel_safe` 使用 `ThreadPoolExecutor`（线程池），但 `_ocr_pre_rendered` 内部调用 `_ocr_with_timeout`（子进程）。这意味着：
1. 线程池调度任务
2. 每个任务内部启动子进程做实际 OCR
3. C-08 的实例池在线程侧创建，但子进程侧不使用它（子进程自己创建新实例）

**结果**：C-08 和 C-09 都部分生效但都没完全达到设计目标：
- C-08 的实例池被创建但从未用于实际 OCR（OCR 在子进程中）
- C-09 的子进程每次都创建新 PaddleOCR 实例（NC-01 的根因）

**修复建议**：选择一种方案并贯彻到底：

**方案 A（推荐）**：线程池 + 实例池 + 超时降级
- 保留 C-08 线程级实例池
- 放弃 C-09 子进程超时，改为线程级超时（接受无法 kill 的风险）
- 增加 OCR 卡死检测：如果某页 OCR 超过 3 倍平均时间，跳过并记录

**方案 B**：进程池 + initializer 预加载
- 使用 `ProcessPoolExecutor(initializer=_ocr_worker_init)`
- worker 进程预加载 PaddleOCR，整个批次复用
- 超时后 `terminate()` 真正回收
- 放弃 C-08 线程级实例池（进程池自带隔离）

---

### NC-03：C-07 双写磁盘 I/O 开销 🟡

**严重程度**：Medium — 性能影响可控但不理想

C-07 修复在 `_stream_parse_pdf` 中同时写入 `pages.blob`（PageBlobReader）和 `page_NNNN.txt`（逐页文件）：

```python
blob_reader.write_page(page_num, page_text)      # 写 blob
page_file.write_text(page_text, encoding="utf-8") # 写逐页文件（向后兼容 + 调试）
```

**影响**：2000 页 PDF = 4000 次磁盘写入（原 2000 次）。在 SSD 上影响约 +1-2 秒，在 HDD 上约 +5-10 秒。

**建议**：
1. 增加 `--keep-page-files` 配置项，默认关闭逐页文件
2. 或仅在 `parse_mode == "debug"` 时写入逐页文件
3. 在文档中明确标注逐页文件为"临时兼容措施，将在后续版本移除"

---

### NC-04：§13.2 进度回调代码使用 4 元组解包 🟡

**严重程度**：Medium — 代码不一致，会导致运行时 ValueError

C-11 将 `_stream_parse_pdf` 的返回值从 4 元组改为 5 元组（增加 `parse_meta` dict）。但 §13.2 的进度回调示例代码仍使用 4 元组解包：

```python
# §13.2（错误）
index_builder, tables, chapter_index, total_chars = _stream_parse_pdf(...)

# §3.2.1 _parse_single_doc（正确）
index_builder, tables, chapter_index, total_chars, parse_meta = _stream_parse_pdf(...)
```

**修复**：更新 §13.2 代码为 5 元组解包。

---

### NC-05：进度回调特殊值 -1,-1 未处理 🟡

**严重程度**：Medium — 用户体验问题

`_stream_parse_pdf_with_limit` 在队列满时调用 `progress_callback(-1, -1)`，但 §13.2 的回调处理直接做除法：

```python
def progress_callback(page_num: int, total_pages: int):
    ratio = page_num / total_pages  # (-1)/(-1) = 1.0 → 显示"100% 完成"
```

**影响**：用户看到"100% 完成"但实际在排队等待。

**修复**：回调中检查特殊值：
```python
def progress_callback(page_num: int, total_pages: int):
    if page_num < 0:
        status_text.text("排队等待中...（前方有其他文件正在解析）")
        return
    ratio = page_num / total_pages
    progress_bar.progress(ratio, text=f"解析中... 第 {page_num}/{total_pages} 页")
```

---

### NC-06：`_ocr_page` 函数（§3.6.1）是死代码 🟡

**严重程度**：Low — 代码维护负担

§3.6.1 定义了完整的 `_ocr_page` 函数（~80 行），但整个 spec 中没有任何地方调用它：
- `_stream_parse_pdf` 只收集 `scan_page_indices`，不做单页 OCR
- `_ocr_batch_parallel_safe` 有自己的内联 OCR 逻辑（`_ocr_pre_rendered`）
- `_ocr_with_timeout` 被 `_ocr_pre_rendered` 调用，而非 `_ocr_page`

**建议**：删除 §3.6.1 的 `_ocr_page`，或明确标注其用途（如"单页调试入口"）。

---

### NC-07：反向索引内存未计入分析 🟡

**严重程度**：Low — 内存仍在可控范围，但分析不完整

C-13 新增 `_page_to_tokens: dict[int, set[str]]`，存储每页的 token 集合。但 §3.2.3 的内存分析仅计算了正向索引（~16-20MB），未计入反向索引：

| 文档规模 | 正向索引内存 | 反向索引内存（新增） | 总计 |
|---------|------------|-------------------|------|
| 2000 页 | ~20MB | ~80MB（2000页×800 tokens×50字节/str引用） | ~100MB |
| 5000 页 | ~40MB | ~200MB | ~240MB |

**结论**：仍在可控范围（<500MB），但应在 §3.2.3 中补充说明。

---

### NC-08：`_calc_ocr_workers` 快照探测仍有竞态 🟢

**严重程度**：Low — 实际限流由 per-task 信号量保证

C-03 修复了 per-task 信号量（`_ocr_pre_rendered` 中 acquire + finally release），但 `_calc_ocr_workers` 仍使用"试探性 acquire 全部 → release"做快照：

```python
for _ in range(_MAX_CONCURRENT_OCR):
    if _ocr_semaphore.acquire(blocking=False):
        snapshot_available += 1
for _ in range(snapshot_available):
    _ocr_semaphore.release()
```

**问题**：探测期间短暂占用了所有可用槽位，其他线程的 `acquire` 可能失败。虽然 spec 说"这只是建议值"，但可能返回 `max_workers=1`（恰好被探测占满），导致单线程 OCR。

**修复**：使用 `_ocr_semaphore._value` 直接读取（CPython 内部属性，已被广泛使用），或维护一个独立的 `AtomicCounter`：

```python
def _calc_ocr_workers(requested: int = 4) -> int:
    available = _ocr_semaphore._value  # 直接读取，不修改状态
    return min(requested, max(1, available))
```

---

## 四、v5 遗留未修复问题

### 遗留-01：PaddleOCR 依赖冲突风险 🔴

**状态**：未修复（仅缓解）

`paddlepaddle`（~1.5GB CPU 版）依赖 `numpy`/`pyyaml`，可能与现有环境冲突。spec 仅建议"用独立 venv 或 Docker"，但未提供具体的 Dockerfile 或 requirements 隔离方案。

**建议**：
1. 提供 `Dockerfile` 模板，包含 paddlepaddle 预装
2. 或将 OCR 作为独立微服务（Flask API），主应用通过 HTTP 调用
3. 在 Phase 0 增加环境验证步骤：`python -c "import paddleocr"` 成功才继续

---

### 遗留-02：`_extract_company_names` 重复定义 🟡

**状态**：未修复

| 位置 | 实现 | 复杂度 |
|------|------|--------|
| spec §3.9.5 `parse_helpers.py` | 3 个正则模式，返回 `set[str]` | 简单 |
| `quality_checker.py` 第 132 行 | 前缀剥离 + 句子片段过滤 + 长度过滤 + 投标人模式，返回 `list[str]` | 健壮 |

spec 的简化版本会漏掉 quality_checker.py 中的以下能力：
- `_COMPANY_PREFIX_NOISE` 前剥离（"致"/"参加"/"年"等）
- `_SENTENCE_FRAGMENT_MARKERS` 句子片段过滤
- `_MAX_COMPANY_NAME_LEN` 长度过滤（>30 字符视为句子片段）
- 投标人模式匹配（"投标人：XXX"）

**建议**：spec §3.9.5 应明确声明"复用 `quality_checker.py` 中已有的 `_extract_company_names`，不在此重复定义"。

---

### 遗留-03：`_classify_table` 重复定义 🟡

**状态**：未修复

spec §3.9.4 定义了 `_classify_table`（仅 scoring/qualification/unknown），但 `doc_parser.py` 中可能已有类似实现。需检查是否一致。

**建议**：合并到 `doc_parser.py` 或 `parse_helpers.py` 中的唯一定义点。

---

### 遗留-04：bigram 停用词列表不完整 🟢

**状态**：未修复

当前过滤列表 `'，。、；：！？的了吗呢吧'` 缺少高频无信息量 bigram：
- 代词类：`我们`、`他们`、`这个`、`那个`、`什么`
- 量词类：`一个`、`一些`、`这种`
- 连词类：`但是`、`因为`、`所以`、`如果`

**影响**：索引体积增加 ~5-10%，但不影响召回率（这些 bigram 会命中很多页面，但 hit_count 排序会降低其权重）。

**建议**：补充停用词列表，或在排序时对命中页面数 > 总页数 30% 的 token 降权。

---

### 遗留-05：TTL 清理仅在启动时执行 🟢

**状态**：未修复

`_cleanup_old_parsed_dirs()` 仅在 `app.py` 启动时调用。如果应用长期运行（Streamlit 长驻进程），72 小时后的目录不会被清理，直到下次重启。

**建议**：增加定时清理（如每 6 小时执行一次），或在上传新文件前触发清理。

---

### 遗留-06：缺少性能测试和小文件回归测试 🟡

**状态**：未修复

§15 测试策略缺少：
1. **性能基准测试**：200MB 文件解析的内存峰值和时间基准
2. **小文件回归测试**：确保 ≤50MB 文件走 inline 模式，行为不变
3. **并发压力测试**：3 用户同时上传 200MB 文件的资源竞争

**建议**：在 §15.1 增加：
```
tests/
├── performance/
│   ├── test_memory_peak.py        # 内存峰值测试
│   ├── test_parse_time.py         # 解析时间基准
│   └── test_concurrent_users.py   # 并发压力测试
├── regression/
│   └── test_small_file_unchanged.py  # 小文件行为不变
```

---

## 五、各模块详细评估

### 模块 1：上传与预检 — 8/10（v5: 7/10）

**改进**：
- C-01 document_id 修复彻底解决了断点续传失效问题
- md5(filename + file_size + 前1MB内容hash) 策略合理，防同名同大小但内容不同

**剩余风险**：
- 临时文件 `delete=False` 但解析失败时无人清理（v5 遗留）
- `_probe_pdf` 与 `_stream_parse_pdf` 重复打开同一 PDF 两次（v5 遗留）
- 非 PDF 大文件无 streaming 路径（v5 遗留）

---

### 模块 2：流式解析与倒排索引 — 8/10（v5: 6/10）

**改进**：
- C-05 修复 bigram 英数处理，ISO9001 等资质编号搜索不再失败
- C-10 补全 `_detect_chapter_boundary`，TOC 优先 + 正则回退 + 降级策略完整
- C-12 原子写入（tmp + os.replace）+ 每50页增量落盘索引
- C-13 反向索引将 remove_page 从 O(N) 优化到 O(K)

**剩余风险**：
- 反向索引内存未计入 §3.2.3 分析（NC-07）
- `_page_to_tokens` 在断点续传恢复时需重建（代码中已处理，正确）
- bigram 停用词列表不完整（遗留-04）

---

### 模块 3：磁盘持久化 — 8/10（v5: 6/10）

**改进**：
- C-06 `_serialize_state` 正确检测 streaming 模式跳过深拷贝
- C-07 PageBlobReader 写入路径已集成
- PageBlobReader 线程安全修复（`threading.Lock` + `f.tell()` 替代 `stat().st_size`）

**剩余风险**：
- 双写开销（NC-03）
- OCR 回写采用追加模式，占位文本残留在 blob 中（v5 遗留，`update_page` 方法已提供但不自动 compact）

---

### 模块 4：按需检索与 ReqExtractor — 8.5/10（v5: 7.5/10）

**改进**：
- LRU 容量 50→200，避免 search_pages + get_summary 频繁淘汰
- per_page_limit 差异化：scoring/qual=1200（评分表常 >800 字符），format=600
- QualityChecker 改用倒排索引搜索公司名（top_k=50），覆盖全文而非仅前 20 页

**剩余风险**：
- 二次检索仍扫描前 100 页，未利用 chapter_index（遗留 P2 项）
- seen_pages 去重可能丢弃同页不同段信息（v5 遗留）

---

### 模块 5：OCR 集成 — 6/10（v5: 6.5/10，降 0.5 分）

**改进方向**：
- C-08 线程级实例池思路正确（但与 C-09 矛盾）
- C-09 子进程超时方向正确（但实现方式引入性能回归）
- PaddleOCR 版本锁定 + 离线模型预置

**新引入问题**：
- NC-01：子进程每页重新加载 PaddleOCR 模型（10-30s/页），750 页 = 4.5+ 小时
- NC-02：C-08 实例池与 C-09 子进程架构矛盾
- NC-06：`_ocr_page` 死代码

**建议**：采用 NC-02 的方案 B（ProcessPoolExecutor + initializer），一次性解决 NC-01 和 NC-02。

---

### 模块 6：并发控制与节点适配 — 7.5/10（v5: 6/10）

**改进**：
- C-03 per-task 信号量（acquire + finally release）彻底修复限流失效
- C-04 AgentState TypedDict 扩展与现有 state.py 结构一致

**剩余风险**：
- `_calc_ocr_workers` 快照探测仍有竞态（NC-08，但影响小）
- 全局信号量在多进程部署中无效（v5 遗留）
- 内存对比漏算 PaddleOCR 模型常驻内存（v5 遗留）

---

### 模块 7：辅助函数与实施路径 — 8/10（v5: 7/10）

**改进**：
- C-10/C-11 补全了 `_detect_chapter_boundary` 和 `_build_meta` 调用链
- Phase 0（修复 13 个 Critical）+ Phase 11（端到端联调）新增合理
- 总计 33-42 天与 v5 评估建议一致

**剩余风险**：
- `_ocr_page` 死代码（NC-06）
- `_extract_company_names` 重复定义（遗留-02）
- `_classify_table` 重复定义（遗留-03）
- 验收标准已统一为 90%（已修复）

---

## 六、问题汇总与优先级

### 🔴 Critical（必须在实施前修复）

| # | 问题 | 模块 | 修复方案 | 工作量 |
|---|------|------|---------|--------|
| NC-01 | C-09 子进程每页重新加载模型 | OCR | 改用 ProcessPoolExecutor + initializer | 1 天 |
| NC-02 | C-08 与 C-09 架构矛盾 | OCR | 选择方案 B（进程池 + initializer），放弃线程级实例池 | 0.5 天 |

### 🟡 Major（实施中必须解决）

| # | 问题 | 模块 | 修复方案 | 工作量 |
|---|------|------|---------|--------|
| NC-03 | 双写磁盘 I/O 开销 | 持久化 | 增加 `keep_page_files` 配置项，默认关闭 | 0.5 天 |
| NC-04 | §13.2 代码 4 元组解包 | 进度反馈 | 更新为 5 元组解包 | 0.1 天 |
| NC-05 | 进度回调 -1,-1 未处理 | 并发控制 | 回调中检查特殊值 | 0.1 天 |
| NC-06 | `_ocr_page` 死代码 | OCR | 删除或标注用途 | 0.1 天 |
| 遗留-02 | `_extract_company_names` 重复定义 | 辅助函数 | 声明复用 quality_checker.py 版本 | 0.1 天 |
| 遗留-03 | `_classify_table` 重复定义 | 辅助函数 | 合并到唯一定义点 | 0.2 天 |
| 遗留-06 | 缺少性能/回归测试 | 测试 | 补充测试文件 | 1 天 |
| 遗留-01 | PaddleOCR 依赖冲突 | OCR | 提供 Dockerfile 或微服务方案 | 1-2 天 |

### 🟢 Minor（优化项）

| # | 问题 | 修复方案 | 工作量 |
|---|------|---------|--------|
| NC-07 | 反向索引内存未计入分析 | 补充 §3.2.3 | 0.1 天 |
| NC-08 | _calc_ocr_workers 快照竞态 | 改用 `_value` 或 AtomicCounter | 0.2 天 |
| 遗留-04 | bigram 停用词不完整 | 补充停用词列表 | 0.2 天 |
| 遗留-05 | TTL 清理仅启动时 | 增加定时清理 | 0.3 天 |

---

## 七、总体结论

### 评分提升

| 维度 | v5 | v6 | 变化 |
|------|-----|-----|------|
| 综合评分 | 6.6/10 | 7.7/10 | **+1.1** |
| Critical 问题 | 13 个 | 2 个（NC-01, NC-02） | **-11** |
| P0 阻塞性问题 | 7 个 | 2 个 | **-5** |
| 已修复 Critical | 0/13 | 13/13 | **全部修复** |

### v6 核心改进

1. **13 个 v5 Critical 问题全部修复** — document_id、sections 冲突、AgentState TypedDict、bigram 英数、_serialize_state、PageBlobReader、OCR 线程安全/超时、章节检测、_build_meta 调用链、原子写入、remove_page 性能
2. **P2 优化全部落地** — LRU 扩容、per_page_limit 差异化、公司名倒排索引、磁盘预检、版本锁定、离线模型、Phase 0/11
3. **实施路径更务实** — 33-42 天含阻塞性修复和端到端联调

### v6 核心风险

1. **OCR 模块是最大短板** — C-08 和 C-09 虽然各自方向正确，但组合后互相矛盾，且 C-09 的子进程方案引入致命性能回归（NC-01）。这是 v6 唯一的 Critical 级新问题。
2. **遗留问题需在实施中清理** — 重复定义、死代码、测试缺失等问题不影响方案可行性，但影响代码质量。

### 建议

1. **优先修复 NC-01 + NC-02**：采用 `ProcessPoolExecutor + initializer` 方案，一次性解决 OCR 性能回归和架构矛盾。这是进入实施的唯一阻塞项。
2. **OCR 环境先行验证**：在 Phase 0 增加独立的 PaddleOCR 环境验证步骤，确认安装、API 兼容性、线程/进程安全后再进入 Phase 1。
3. **其余 Major/Minor 问题在各 Phase 中逐步解决**，不阻塞实施启动。

---

*评估基于 v6 修订版 spec.md（3002 行）+ 代码库交叉验证*
