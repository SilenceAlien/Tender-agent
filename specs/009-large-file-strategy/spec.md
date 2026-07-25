# 超大招标文件（>200MB）完整读取策略（v7.1 修订版）

> **状态**：方案设计（未执行）
> **日期**：2026-07-12
> **范围**：仅设计方案，不包含实现
> **修订说明**：
> - v2：补全算法设计、节点适配分析、LRU 缓存、跨阶段持久化、14 节点影响矩阵
> - v3：修复 5 个 Critical 问题（C1 倒排索引分词器不匹配、C2 generator 与 list 矛盾、C3 LRU cache hit bug、C4 上传阶段 OOM、C5 API 签名错误）；补全 10 个 Major 空白（M1 OCR 完整实现、M2 章节自动切分、M3 并行 OCR + 性能重估、M4 Reader 生命周期、M5 进度反馈、M6 断点续传、M7 raw_text 清理、M8 bid_subtype 适配、M9 召回率量化 + 二次检索、M10 测试策略）
> - v4：修复 3 个新 Critical 问题（NC1 预算分配截断 bug、NC2 fitz 线程安全 segfault、NC3 并行 OCR 未集成到流式解析）；补全 6 个 Major 空白（_sample_text_ratio 均匀采样、SearchResult dataclass 定义、5 个辅助函数实现、多用户并发控制、磁盘 I/O 优化 pages.blob、倒排索引内存占用分析）
> - v5：修复 3 个 Critical 问题（NC4 预渲染内存爆炸→分批处理、NC5 索引 token 残留→remove_page、NC6 ParsedDocumentReader 初始化矛盾→统一懒加载）；修复 6 个 Major 问题（M1 total_chars 估算不准→流式累加、M2 document_id 生成不统一→md5 hash、M3 suffix 作用域 bug、M4 _calc_ocr_workers 未集成、M5 PageBlobReader 未集成到 get_page、M6 架构图未反映两阶段 OCR）
> - v6：修复 13 个 Critical 问题（基于 7-agent 并行评估报告）
>   - C-01：document_id 生成 bug → file_path 为临时路径导致断点续传失效，改用 md5(filename + file_size + 前1MB内容hash)
>   - C-02：sections 字段类型冲突 → 与 AgentState 顶层 sections:dict[str,str] 冲突，重命名为 chapter_index
>   - C-03：_calc_ocr_workers 竞态缺陷 → check-then-act 模式限流失效，改为 acquire 后持有 + finally release
>   - C-04：AgentState TypedDict 未更新 → parse_mode/parsed_dir/meta 字段未声明，补全 TypedDict + factory_state 默认值
>   - C-05：bigram 英数处理 bug → 索引整词 vs 查询 bigram 不一致，统一 _tokenize_query 切片逻辑
>   - C-06：_serialize_state deepcopy 未改 → 快照导出仍 OOM，检测 streaming 模式跳过全文深拷贝
>   - C-07：PageBlobReader 写入未集成 → _stream_parse_pdf 仍用 page_file.write_text，统一使用 PageBlobReader
>   - C-08：PaddleOCR 线程安全 → 单例+4线程并发不安全，改为实例池
>   - C-09：_ocr_with_timeout 不可靠 → daemon 线程无法 kill，改用 multiprocessing.Process
>   - C-10：_detect_chapter_boundary / _save_chapter 未定义 → 补全实现 + 降级策略
>   - C-11：_build_meta 调用链断裂 → 缺传 probe_info 等参数，补全调用链
>   - C-12：进度文件非原子写 → 崩溃损坏 JSON，改用 tmp + os.replace 原子替换
>   - C-13：remove_page O(N) 性能 → 遍历全部 token，增加反向索引 dict[int, set[str]]
>   - P2 优化：LRU 容量 50→200 页；per_page_limit 按类别差异化；二次检索按章节索引扫描；QualityChecker 公司名改用倒排索引；磁盘预检 2GB→3GB；验收标准召回率统一为 90%；PaddleOCR 版本锁定 + 离线模型预置；实施路径增加 Phase 0 + Phase 11
> - **v7.2：修复 v7.1 二次审查报告中的 2 个 Critical + 5 个 Major + 3 个 Minor 问题**
>   - C-V7.1-01：信号量双重获取 — 调用方和函数内部各调一次 `_calc_ocr_workers`，每次 OCR 泄漏 N 个槽位，2 次后信号量耗尽 → 删除调用方的 `_calc_ocr_workers`，仅在 `_ocr_batch_parallel_safe` 内部调用
>   - C-V7.1-02：`_ocr_worker_init` 声称配置了 `basicConfig` 但代码中不存在，spawn 方式下子进程日志丢失 → 在函数开头添加 `logging.basicConfig()`
>   - M-V7.1-01：`_calc_ocr_workers` 读取 `_value` 和 `acquire` 之间存在 TOCTOU 竞态 → 直接 acquire `requested` 个，不预读 `_value`
>   - M-V7.1-02：日志使用 `max_workers` 而非 `actual_workers` → 修正
>   - M-V7.1-03：5 处残留"线程"术语未更新为"进程" → 全部修正
>   - M-V7.1-04：`test_ocr_batch_parallel_no_segfault` 未 mock `as_completed` → 添加 `as_completed` mock
>   - M-V7.1-05：验收标准召回率不一致（§1.3 和 §14.1 写 95%，§9.1 写 90%）→ 统一为 90%
>   - m-V7.1-01：`TimeoutError` 在 Python 3.8-3.10 与 `concurrent.futures.TimeoutError` 不匹配 → 改用 `FutureTimeoutError`
>   - m-V7.1-02：内存估算仅考虑 4 worker，未覆盖 8 worker 场景 → 补充
>   - m-V7.1-03：测试 `patch('__main__._ocr_batch_parallel_safe')` 模块路径错误 → 改为 `core.large_file._ocr_batch_parallel_safe`
> - **v7.1：修复 v7 审查报告中的 3 个 Critical + 13 个 Major + 8 个 Minor 问题**
>   - C-V7-01：`_stream_parse_pdf` 中 `KEEP_PAGE_FILES`/`blob_reader` 赋值缩进断裂（零缩进写在函数体内）→ 修正为函数内缩进（3 处）
>   - C-V7-02：v7 迁移到 ProcessPoolExecutor 后 per-task 信号量 acquire/release 被删除，OCR 并发限流失效 → 在 `_ocr_batch_parallel_safe` 中恢复信号量获取/释放
>   - C-V7-03：`pool.shutdown(wait=False)` 无法终止卡死的 worker 进程 → 增加 `terminate()` + `kill()` 强制回收
>   - M-V7-01：`_stream_parse_pdf` 返回类型标注 4 元组（实际 5 元组）→ 更新
>   - M-V7-02~03：架构图/标题仍写"线程池"/"多线程"→ 更新为"进程池"/"多进程"
>   - M-V7-04：§3.2.3 重复的内存分析表 → 删除旧表
>   - M-V7-05：`_ocr_worker_init` 用 logger，`_ocr_worker_task` 用 print → 统一为 logger + basicConfig
>   - M-V7-06~10：实施路径/文件表/并发参数/验收表/风险评估中"线程"→"进程"
>   - M-V7-11：`test_ocr_batch_parallel_no_segfault` mock `_get_ocr_engine`（不存在）→ 重构为 mock ProcessPoolExecutor
>   - M-V7-12：`test_ocr_process_pool_model_loading_once` 跨进程 `nonlocal` 不工作 → 改用 `multiprocessing.Value`
>   - M-V7-13：`_cleanup_old_parsed_dirs` 与 `_cleanup_expired_parsed_dirs` 重复 → 合并为一个
>   - m-V7-01~08：死参数删除/变量名修正/LRU 缓存值/OCR 结果健壮性/Python 版本标注/测试构造参数/断言修正/浅拷贝冗余
> - **v7：修复 2 个 Critical + 8 个 Major + 4 个 Minor 问题（基于 v6 评估报告）**
>   - NC-01：C-09 子进程每页重新加载模型导致性能回归（750页从25分钟暴涨到4.5+小时）→ 改用 ProcessPoolExecutor + initializer 预加载，worker 进程在整个批次内复用 PaddleOCR 实例
>   - NC-02：C-08 线程级实例池与 C-09 子进程架构矛盾 → 统一为进程池方案，放弃线程级实例池
>   - NC-03：C-07 双写磁盘 I/O 开销 → 增加 keep_page_files 配置项，默认关闭逐页文件
>   - NC-04：§13.2 进度回调使用 4 元组解包（应为 5 元组）→ 修正
>   - NC-05：进度回调特殊值 -1,-1 未处理 → 回调中检查特殊值显示"排队中"
>   - NC-06：_ocr_page 死代码 → 删除，OCR 逻辑统一在 _ocr_batch_parallel_safe
>   - NC-07：反向索引 _page_to_tokens 内存未计入 §3.2.3 分析 → 补充
>   - NC-08：_calc_ocr_workers 快照探测仍有竞态 → 改用 _ocr_semaphore._value 直接读取
>   - 遗留-01：PaddleOCR 依赖冲突 → 提供 Dockerfile 模板
>   - 遗留-02：_extract_company_names 重复定义 → 声明复用 quality_checker.py 版本
>   - 遗留-03：_classify_table 重复定义 → 声明复用 doc_parser.py 版本
>   - 遗留-04：bigram 停用词列表不完整 → 补充代词/量词/连词
>   - 遗留-05：TTL 清理仅在启动时 → 增加上传前触发 + 定时清理
>   - 遗留-06：缺少性能/回归测试 → 补充 test_memory_peak / test_small_file_unchanged / test_concurrent_users

---

## 一、需求背景

### 1.1 问题描述

当前系统的 `DocumentParser` 节点在处理招标文件时，采用 **一次性全量加载** 策略：

```python
# 当前 _load_pdf 的核心逻辑
doc = fitz.open(file_path)       # 打开整个 PDF
text_parts = []
for page in doc:                  # 遍历所有页
    page_text = page.get_text()   # 逐页提取文本
    text_parts.append(page_text)
doc.close()
return "\n".join(text_parts)      # 全部拼接成一个字符串
```

然后在 `_parse_single_doc` 中：
```python
raw_text = _load_pdf(file_path)              # 全文字符串
chunks = chunk_text(raw_text, chunk_size)     # 对全文做分块
result["parsed_content"] = raw_text           # 全文存入 AgentState
```

对于 200MB+ 的招标文件（可能包含 1000-5000 页，大量图纸、扫描件），这套方案存在以下致命问题：

| 问题 | 影响 | 严重程度 |
|------|------|---------|
| **内存爆炸**：全文加载到单个 Python 字符串 | 200MB PDF → 预计 50-200M 字符 → 2-8GB 内存 | 🔴 致命 |
| **双重打开**：`_load_pdf` 和 `_extract_tables_pdf` 各打开一次 PDF | 内存峰值翻倍 | 🔴 高 |
| **State 膨胀**：全文存入 `parsed_content`，流经所有 14 个节点 | 每个节点 deepcopy 全文 | 🔴 致命 |
| **快照 OOM**：`_serialize_state` 做 `copy.deepcopy(snapshot)` | 快照导出时直接 OOM | 🔴 高 |
| **有效信息浪费**：ReqExtractor 截断到 8000 字符 | 99.99% 解析内容被丢弃 | 🟡 中 |
| **无进度反馈**：同步阻塞，无进度条 | 用户长时间无响应 | 🟡 中 |
| **无 OCR**：扫描页 `get_text()` 返回空 | 工程类招标文件的图纸/工程量清单内容丢失 | 🔴 高 |
| **Streamlit 上传限制**：`uf.getvalue()` 一次性读入内存 | 浏览器/服务器超时 | 🟡 中 |

### 1.2 用户价值

招标文件超过 200MB 的场景在工程类、集成类标书中非常常见（含大量图纸、工程量清单、技术规格书）。当前系统无法处理这类文件，直接限制了产品在工程类和集成类标书市场的可用性。

### 1.3 成功标准

1. 能够稳定处理 200MB-500MB 的 PDF 招标文件
2. 解析过程中内存峰值控制在 4GB 以内（含上传阶段）
3. 解析过程有进度反馈（百分比/页码），通过 Streamlit 前端实时展示
4. 关键信息（评分标准、资质要求、技术规格、格式要求）提取完整性 ≥ 90%（通过召回率测试验证）
5. 端到端处理时间：纯文本 PDF ≤ 10 分钟；含 OCR 的扫描 PDF ≤ 30 分钟（200MB 文件）
6. 小文件（≤50MB）行为完全不变
7. 支持断点续传：解析中断后可从最后成功页面继续
8. TTL 保留期延长至 72 小时（覆盖跨天使用场景）

---

## 二、策略总览

### 2.1 核心思路：流式解析 + 倒排索引 + 磁盘持久化 + 按需检索 + 两阶段 OCR

将当前的 **「全量加载 → 全文存 State → 截断使用」** 模式，改为：

```
流式逐页解析 → 逐页写磁盘 + 构建倒排索引 → AgentState 只存引用 → 下游节点按需检索
                                                                    ↓
                                               扫描页 → 两阶段批量并行 OCR（NC2/NC3/NC4 修复）
```

关键转变：
- **全文不再进入 AgentState** — State 只存元信息和 `parsed_dir` 路径
- **解析时同步构建倒排索引** — 关键词 → 页码列表，搜索时 O(1) 查表，非 O(N) 扫描
- **下游节点通过 Reader 按需读取** — 需要哪部分内容就取哪部分
- **预算分配保证搜索结果不被截断**（NC1 修复）— 每类信息分配固定字符预算
- **两阶段 OCR 消除 segfault 风险**（NC2 修复）— 主线程预渲染 + 进程池并行推理
- **分批预渲染控制内存峰值**（NC4 修复）— 每批≤50页，避免 750 页全量预渲染 OOM
- **OCR 回写清理旧索引**（NC5 修复）— remove_page 清除占位文本 token，防止残留
- **懒加载避免初始化内存尖峰**（NC6 修复）— ParsedDocumentReader 统一为 @property 懒加载
- **多用户并发限流**（Major 修复）— 信号量控制同时解析数和 OCR 进程数

### 2.2 策略架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                         超大文件处理策略                                │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  阶段 1: 智能上传 + 格式预检                                          │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │
│  │ Streamlit     │→ │ 大小检测       │→ │ PDF 预检       │            │
│  │ maxUploadSize │  │ >50MB→streaming│  │ 页数/加密/扫描 │            │
│  │ =1024MB       │  │ ≤50MB→inline  │  │ 件比例/书签    │            │
│  └───────────────┘  └───────────────┘  └───────────────┘            │
│                                                                      │
│  阶段 2a: 流式解析（单次打开，逐页 yield，同步构建倒排索引）        │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │
│  │ 逐页文本提取   │→ │ 逐页表格提取   │→ │ 倒排索引构建   │            │
│  │ page.get_text │  │ page.find_     │  │ keyword→      │            │
│  │ +扫描页检测   │  │ tables()       │  │ [page_nums]   │            │
│  │ (OCR 延迟)    │  │               │  │               │            │
│  └───────────────┘  └───────────────┘  └───────────────┘            │
│                                                                      │
│  阶段 2b: 批量并行 OCR（NC4: 分批预渲染，每批≤50页，控制内存峰值）    │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │
│  │ Phase A: 主线程│→ │ Phase B: 进程池│→ │ 回写：更新页  │            │
│  │ 预渲染为 numpy │  │ PaddleOCR 推理 │  │ 面文件+重建  │            │
│  │ (每批≤50页)   │  │ (不接触 fitz)  │  │ 索引(NC5)    │            │
│  └───────────────┘  └───────────────┘  └───────────────┘            │
│                                                                      │
│  阶段 3: 磁盘持久化（非临时目录，支持跨阶段）                          │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │
│  │ pages/        │  │ tables.json   │  │ inverted_     │            │
│  │ page_NNNN.txt │  │ + index.json  │  │ index.json    │            │
│  │ + ocr_NNNN.txt│  │ + meta.json   │  │ {keyword:     │            │
│  │ pages.blob    │  │               │  │  [pages]}     │            │
│  │ (优化模式 M5) │  │               │  │               │            │
│  └───────────────┘  └───────────────┘  └───────────────┘            │
│                                                                      │
│  阶段 4: 按需检索消费（14 节点影响分析见 §四）                         │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │
│  │ ReqExtractor  │  │ QualityChecker│  │ 其他 11 节点  │            │
│  │ 倒排索引定位  │→ │ Reader 提取   │→ │ 无需改动       │            │
│  │ 关键页面      │  │ 公司名白名单  │  │ (依赖 requirements) │            │
│  └───────────────┘  └───────────────┘  └───────────────┘            │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 三、详细策略设计

### 3.1 阶段 1：智能上传 + 格式预检

#### 3.1.1 Streamlit 上传配置 + 流式分块写盘（C4 修复）

#### 配置

```python
# .streamlit/config.toml
[server]
maxUploadSize = 1024  # MB，允许上传到 1GB
```

#### 问题：`getvalue()` 全量加载导致上传阶段 OOM

当前代码（`upload_panel.py` 第 127 行）：
```python
tmp.write(uf.getvalue())  # 500MB 文件 → 500MB 内存峰值
```

#### 修复：分块读取写盘

```python
def _save_uploaded_file_chunked(uploaded_file, chunk_size: int = 8 * 1024 * 1024) -> str:
    """将 UploadedFile 分块写入临时文件，避免全量加载到内存.
    
    Args:
        uploaded_file: Streamlit UploadedFile 对象
        chunk_size: 每次读取的块大小（默认 8MB）
    
    Returns:
        临时文件路径
    """
    import tempfile
    from pathlib import Path
    
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        while True:
            chunk = uploaded_file.read(chunk_size)
            if not chunk:
                break
            tmp.write(chunk)
    return tmp.name
```

#### upload_panel.py 适配

```python
# 替换原来的 tmp.write(uf.getvalue())
for uf in uploaded_files:
    file_path = _save_uploaded_file_chunked(uf)
    # M3 修复：suffix 在 _save_uploaded_file_chunked 内部定义，
    # 此处作用域不可见。改为从文件名重新提取。
    from pathlib import Path as _Path
    saved_files.append({
        "filename": uf.name,
        "path": file_path,
        "type": _Path(uf.name).suffix.lstrip("."),
    })
```

**内存对比**：
- 修复前：上传 500MB 文件 → `getvalue()` 占用 500MB 内存
- 修复后：上传 500MB 文件 → 峰值内存仅 8MB（一个 chunk）

#### 上传进度反馈

由于 Streamlit 的 `file_uploader` 是阻塞式控件（上传完成后才返回），上传过程中的进度条无法在 Python 侧实现。但可以通过以下方式改善用户体验：

1. **上传前提示**：显示文件大小预估和预计时间
2. **上传后解析进度**：解析阶段的进度条（见 §3.10 进度反馈机制）

#### 3.1.2 大小检测与模式切换

```python
# 50MB 阈值基于以下计算：
# - 假设 PDF 每 MB 产生 2.5M 字符（保守估计）
# - 50MB PDF → 125M 字符 → 内存占用 ~500MB（含 chunks + State）
# - 此值以下，现有 inline 模式可接受
# - 此值以上，切换到 streaming 模式
LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50MB

file_size = Path(file_path).stat().st_size
if file_size > LARGE_FILE_THRESHOLD:
    parse_mode = "streaming"
else:
    parse_mode = "inline"
```

#### 3.1.3 格式预检

```python
def _probe_pdf(file_path: str) -> dict:
    """快速探测 PDF 基本信息，不加载全部内容."""
    import fitz
    doc = fitz.open(file_path)
    info = {
        "page_count": doc.page_count,
        "is_encrypted": doc.is_encrypted,
        "file_size_mb": Path(file_path).stat().st_size / 1024 / 1024,
        "text_ratio": _sample_text_ratio(doc, sample_pages=10),
        "has_bookmarks": len(doc.get_toc()) > 0,
        "toc": doc.get_toc(),
    }
    doc.close()
    return info

def _sample_text_ratio(doc, sample_pages: int = 10) -> float:
    """采样页面，计算有文本页面占比（扫描页检测）.
    
    Major 修复：v3 仅采样前 10 页，如果扫描页集中在文档后半部分
    （工程类标书常见：前半文字说明，后半图纸），采样比例严重失真。
    改为均匀采样：从整个文档中每隔 N 页取 1 页。
    """
    total = doc.page_count
    if total == 0:
        return 0.0
    
    # 均匀采样：计算采样间隔
    if total <= sample_pages:
        indices = list(range(total))
    else:
        step = total / sample_pages
        indices = [int(i * step) for i in range(sample_pages)]
    
    pages_with_text = 0
    for i in indices:
        text = doc[i].get_text() or ""
        if len(text.strip()) >= 50:
            pages_with_text += 1
    return pages_with_text / len(indices)
```

### 3.2 阶段 2：流式解析 + 增量倒排索引（C1 + C2 修复）

#### 3.2.1 核心改进：单次打开，逐页 yield + 增量构建索引

**C2 修复要点**：倒排索引在 generator 循环内部增量构建，而非事后批量处理 `list[PageResult]`。这样流式设计的内存优势得以保留 — 任何时刻只有当前页的文本在内存中。

```python
@dataclass
class IncrementalIndexBuilder:
    """在流式解析过程中增量构建倒排索引.
    
    C2 修复：替代原来需要 list[PageResult] 的 _build_inverted_index。
    每次调用 add_page() 时更新索引，内存只保留索引结构本身。
    
    C-13 修复（v6）：增加反向索引 _page_to_tokens: dict[int, set[str]]，
    remove_page 从 O(N) 遍历全部 token 优化为 O(K)（K=该页 token 数）。
    原方案遍历 50K-80K 个 token，750 扫描页 = 60M 次操作，批量 OCR 回写时性能灾难。
    """
    _index: dict[str, set[int]] = field(default_factory=dict)  # C-13: list→set，O(1) 去重
    _page_to_tokens: dict[int, set[str]] = field(default_factory=dict)  # C-13: 反向索引
    
    def add_page(self, page_num: int, text: str) -> None:
        """将一页文本加入索引（增量更新）."""
        tokens = _tokenize_chinese(text)
        self._page_to_tokens[page_num] = tokens  # C-13: 记录反向索引
        for token in tokens:
            if token not in self._index:
                self._index[token] = set()
            self._index[token].add(page_num)  # C-13: set.add() O(1)
    
    def remove_page(self, page_num: int) -> None:
        """从索引中移除指定页的所有 token（NC5 修复 + C-13 性能优化）.
        
        NC5 问题：OCR 回写时直接 add_page 会在占位文本 token 之上追加
        OCR 真实文本 token，导致占位文本的 token 残留在索引中，
        产生无效搜索命中。
        
        NC5 修复：在 add_page 写入 OCR 真实文本前，先 remove_page
        清除该页在索引中的所有旧记录。
        
        C-13 修复（v6）：通过反向索引 _page_to_tokens 直接定位该页的 token，
        O(K) 复杂度（K=该页 token 数 ~800），替代原方案 O(N) 遍历全部 token（N~80K）。
        """
        if page_num not in self._page_to_tokens:
            return  # 该页不在索引中
        
        tokens_to_remove = self._page_to_tokens[page_num]
        empty_tokens: list[str] = []
        
        for token in tokens_to_remove:  # C-13: 只遍历该页的 token
            if token in self._index:
                self._index[token].discard(page_num)  # C-13: set.discard() O(1)
                if not self._index[token]:
                    empty_tokens.append(token)
        
        # 清理空 token 条目，防止索引膨胀
        for token in empty_tokens:
            del self._index[token]
        
        # 清理反向索引
        del self._page_to_tokens[page_num]
    
    def to_dict(self) -> dict[str, list[int]]:
        """序列化为 JSON 兼容格式（set→sorted list）."""
        return {token: sorted(pages) for token, pages in self._index.items()}


def _stream_parse_pdf(
    file_path: str,
    parsed_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[IncrementalIndexBuilder, list[dict], list[dict], int, dict]:
    """流式解析 PDF，逐页写磁盘 + 增量构建倒排索引.
    
    C2 修复：索引在循环内增量构建，不再需要收集全部 PageResult 到 list。
    M6 支持：通过 progress.json 记录断点，支持断点续传。
    NC3 修复：两阶段 OCR — 流式解析时仅记录扫描页索引，解析完成后批量并行 OCR。
    M1 修复：total_chars 在流式循环中累加实际字符数，不再使用索引估算。
    
    v7.1 M-V7-01: 返回类型标注更新为 5 元组（含 meta dict）。
    
    Returns:
        (index_builder, all_tables, chapter_index, total_chars, parse_meta)
    """
    import fitz
    import time as _time
    import os  # C-12: os.replace 原子写入
    
    # C-11: 记录解析开始时间
    _parse_start = _time.time()
    
    # C-11: 在打开文件前先做格式预检
    probe_info = _probe_pdf(file_path)
    
    doc = fitz.open(file_path)
    pages_dir = parsed_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    
    # C-07 修复（v6）：统一使用 PageBlobReader 写入页面，替代 page_file.write_text
    # 原方案 PageBlobReader 仅在读取侧使用，写入侧仍用逐页文件，导致 pages.blob 永远不生成
    # v7 NC-03: 增加 keep_page_files 配置，默认 False（只写 blob），调试时可设为 True
    # v7.1 C-V7-01: 修正缩进（原零缩进导致 IndentationError）
    KEEP_PAGE_FILES = False  # v7 NC-03: 默认关闭逐页文件写入
    blob_reader = PageBlobReader(parsed_dir)
    
    # M6: 断点续传 — 读取已解析进度
    progress_file = parsed_dir / "progress.json"
    start_page = 0
    if progress_file.exists():
        prog = json.loads(progress_file.read_text())
        start_page = prog.get("last_completed_page", 0)
        logger.info(f"断点续传: 从第 {start_page + 1} 页继续")
    
    index_builder = IncrementalIndexBuilder()
    all_tables: list[dict] = []
    chapter_index: list[dict] = []  # M2: 章节索引
    current_chapter: dict | None = None
    chapter_text_parts: list[str] = []
    total_chars: int = 0  # M1: 流式累加实际字符数
    
    # NC3 修复：Phase 1 收集扫描页索引（不在循环内做 OCR）
    scan_page_indices: list[int] = []  # 0-based page indices needing OCR
    
    # M6: 如果断点续传，恢复已解析的索引和表格
    if start_page > 0:
        existing_index = json.loads((parsed_dir / "inverted_index.json").read_text())
        # C-13: 恢复为 set 格式 + 重建反向索引
        index_builder._index = {k: set(v) for k, v in existing_index.items()}
        index_builder._page_to_tokens = {}  # C-13: 重建反向索引
        for token, pages in index_builder._index.items():
            for pg in pages:
                if pg not in index_builder._page_to_tokens:
                    index_builder._page_to_tokens[pg] = set()
                index_builder._page_to_tokens[pg].add(token)
        existing_tables = json.loads((parsed_dir / "tables.json").read_text())
        all_tables = existing_tables
    
    try:
        total_pages = doc.page_count
        for page_idx in range(start_page, total_pages):
            page = doc[page_idx]
            page_num = page_idx + 1
            
            # 1. 文本提取
            page_text = page.get_text() or ""
            
            # 2. NC3 修复：扫描页检测（仅记录索引，不做 OCR）
            #    OCR 在流式解析完成后批量并行执行（见 Phase 2）
            text_length = len(page_text.strip())
            image_count = len(page.get_images())
            is_scan_page = False
            if text_length < 50 and image_count > 0:
                # 检查 OCR 缓存（断点续传时可能已有 OCR 结果）
                ocr_cache_file = pages_dir / f"ocr_{page_num:04d}.txt"
                if ocr_cache_file.exists():
                    page_text = ocr_cache_file.read_text(encoding="utf-8")
                    is_scan_page = True
                else:
                    # 记录扫描页索引，留待 Phase 2 批量 OCR
                    scan_page_indices.append(page_idx)
                    # 写入占位文本（后续 OCR 完成后覆盖）
                    page_text = f"[扫描页 — 待 OCR: 第 {page_num} 页]"
                    is_scan_page = True
            
            # 3. 表格提取（复用同一页对象）
            tables = []
            try:
                finder = page.find_tables()
                for tbl_idx, tbl in enumerate(finder.tables):
                    rows = tbl.extract()
                    cleaned = _clean_table(rows)
                    if cleaned:
                        tables.append({
                            "page": page_num,
                            "header": cleaned[0],
                            "rows": cleaned[1:] if len(cleaned) > 1 else [],
                            "type": _classify_table(cleaned[0]),
                        })
            except Exception:
                pass
            all_tables.extend(tables)
            
            # 4. 写入磁盘 — C-07 修复：统一使用 PageBlobReader
            # C-07: 原 page_file.write_text(page_text) 改为 blob_reader.write_page
            # v7.1 C-V7-01: 修正缩进（原零缩进导致 IndentationError）
            blob_reader.write_page(page_num, page_text)
            # v7 NC-03: 逐页文件默认不写（减少 I/O），调试时可开启 keep_page_files
            if KEEP_PAGE_FILES:
                page_file = pages_dir / f"page_{page_num:04d}.txt"
                page_file.write_text(page_text, encoding="utf-8")
            
            # M1: 流式累加实际字符数
            total_chars += len(page_text)
            
            # 5. 增量构建倒排索引（C2 修复：在循环内构建）
            index_builder.add_page(page_num, page_text)
            
            # 6. M2: 章节检测（见 §3.9 章节自动切分）
            chapter_match = _detect_chapter_boundary(page_text, page_num, probe_info=probe_info)
            if chapter_match:
                # 保存上一章节
                if current_chapter and chapter_text_parts:
                    _save_chapter(parsed_dir, current_chapter, chapter_text_parts)
                current_chapter = chapter_match
                chapter_text_parts = [page_text]
                chapter_index.append(current_chapter)
            elif current_chapter:
                chapter_text_parts.append(page_text)
            
            # 7. M6: 写入进度（每页更新）— C-12 修复：原子写入
            # C-12: 原 write_text 非原子写，崩溃中途中写会损坏 JSON
            # 修复：先写 .tmp 文件，再 os.replace 原子替换
            progress_data = json.dumps({
                "last_completed_page": page_num,
                "total_pages": total_pages,
                "scan_page_indices": scan_page_indices,  # NC3: 持久化扫描页索引
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            tmp_path = progress_file.with_suffix(".tmp")
            tmp_path.write_text(progress_data)
            os.replace(tmp_path, progress_file)  # C-12: 原子替换
            
            # C-12: 每 50 页增量落盘倒排索引，防止崩溃丢失全部索引
            if page_num % 50 == 0:
                blob_reader.flush_offsets()  # C-07: 同步偏移量索引
                idx_tmp = (parsed_dir / "inverted_index.json").with_suffix(".tmp")
                idx_tmp.write_text(json.dumps(index_builder.to_dict(), ensure_ascii=False))
                os.replace(idx_tmp, parsed_dir / "inverted_index.json")
            
            # 8. M5: 进度回调
            if progress_callback:
                progress_callback(page_num, total_pages)
        
        # 保存最后一个章节
        if current_chapter and chapter_text_parts:
            _save_chapter(parsed_dir, current_chapter, chapter_text_parts)
        
    finally:
        # NC2 修复：在 doc.close() 之前完成所有 fitz 操作
        # Phase 2: 批量并行 OCR（在 doc 仍打开时预渲染图片）
        if scan_page_indices:
            logger.info(
                f"Phase 2: 开始批量并行 OCR — {len(scan_page_indices)} 个扫描页"
            )
            if progress_callback:
                # 通知前端进入 OCR 阶段
                progress_callback(total_pages, total_pages)
            
            ocr_results = _ocr_batch_parallel_safe(
                doc=doc,
                scan_page_indices=scan_page_indices,
                parsed_dir=parsed_dir,
                # M4 修复：动态计算 OCR 进程数，避免跨用户 CPU 过载
                # v7.2 C-V7.1-01: 删除调用方的 _calc_ocr_workers，避免信号量双重获取
                # 信号量 acquire 在 _ocr_batch_parallel_safe 内部 _calc_ocr_workers 中完成
                max_workers=4,
            )
            
            # 回写 OCR 结果：更新页面文件 + 重建索引
            # NC5 修复：先 remove_page 清除占位文本的旧 token，再 add_page 写入真实 OCR 文本
            # M1 修复：累加 OCR 文本字符数（减去占位文本的字符数）
            # C-07 修复：OCR 回写也统一使用 PageBlobReader
            ocr_success_count = 0  # C-11: 用于 _build_meta
            ocr_failed_pages: list[int] = []  # C-11: 用于 _build_meta
            for page_idx_0, ocr_text in ocr_results.items():
                page_num = page_idx_0 + 1
                # C-07: 统一使用 PageBlobReader 写入 OCR 结果
                # v7.1 C-V7-01: 修正缩进（原零缩进导致 IndentationError）
                blob_reader.write_page(page_num, ocr_text)
                # v7 NC-03: 逐页文件默认不写，调试时可通过 keep_page_files 开启
                if KEEP_PAGE_FILES:
                    page_file = pages_dir / f"page_{page_num:04d}.txt"
                    page_file.write_text(ocr_text, encoding="utf-8")
                # M1: 减去占位文本字符数，加上 OCR 真实文本字符数
                # 修复：OCR 返回空串时 total_chars 会减小（减成负数），增加保护
                placeholder_text = f"[扫描页 — 待 OCR: 第 {page_num} 页]"
                if ocr_text:  # 修复：OCR 成功时才更新字符数
                    total_chars += len(ocr_text) - len(placeholder_text)
                    ocr_success_count += 1
                else:
                    ocr_failed_pages.append(page_num)
                # NC5: 移除占位文本的旧 token，防止残留
                index_builder.remove_page(page_num)
                # 加入真实 OCR 文本
                index_builder.add_page(page_num, ocr_text)
            
            logger.info(
                f"Phase 2: OCR 完成 — {len(ocr_results)}/{len(scan_page_indices)} 页成功"
            )
        
        doc.close()
    
    # 写入最终索引文件 — C-12: 原子写入
    blob_reader.flush_offsets()  # C-07: 最终同步偏移量索引
    idx_tmp = (parsed_dir / "inverted_index.json").with_suffix(".tmp")
    idx_tmp.write_text(json.dumps(index_builder.to_dict(), ensure_ascii=False))
    os.replace(idx_tmp, parsed_dir / "inverted_index.json")
    
    tbl_tmp = (parsed_dir / "tables.json").with_suffix(".tmp")
    tbl_tmp.write_text(json.dumps(all_tables, ensure_ascii=False))
    os.replace(tbl_tmp, parsed_dir / "tables.json")
    
    # M6: 解析完成，删除进度文件
    progress_file.unlink(missing_ok=True)
    
    # C-11 修复（v6）：返回更多元信息供 _build_meta 使用
    _parse_duration = _time.time() - _parse_start
    return index_builder, all_tables, chapter_index, total_chars, {
        "probe_info": probe_info,  # C-11: _probe_pdf 的结果
        "scan_page_count": len(scan_page_indices),
        "ocr_page_count": ocr_success_count if scan_page_indices else 0,
        "ocr_failed_pages": ocr_failed_pages if scan_page_indices else [],
        "parse_duration_sec": round(_parse_duration, 1),
    }
```

#### 3.2.2 倒排索引分词器（C1 修复：中文 bigram + 子串匹配）

**C1 问题**：v2 的分词器 `re.findall(r'[\u4e00-\u9fa5]{2,}', text)` 将「评分标准」存为一个完整 token，但搜索时用 `"评分"` 查询 → 查不到。召回率趋近于零。

**C1 修复方案**：采用 **中文 bigram（二元组）分词**，将每两个相邻汉字作为一个 token 存入索引。搜索时也用 bigram 拆分关键词，确保索引 key 与查询 key 一致。

```python
def _tokenize_chinese(text: str) -> set[str]:
    """中文 bigram 分词器（C1 修复）.
    
    核心原理：
    - 将连续中文字符串按 2 字滑窗切分为 bigram
    - 「评分标准」→ {"评分", "分标", "标准"}
    - 搜索「评分」→ 直接查 inverted_index["评分"] → 命中！
    
    优势：
    - 无需分词库（不依赖 jieba）
    - 索引 key 与查询 key 严格一致（都用 bigram）
    - 召回率高：任何包含「评分」的页面都会被命中
    
    代价：
    - 索引体积比完整词大约 2x（但仍是 O(N) 量级）
    - 精确率略低（可能命中不相关页面），但通过 hit_count 排序缓解
    
    Returns:
        去重后的 token 集合
    """
    tokens: set[str] = set()
    
    # 提取所有连续中文片段
    for match in re.finditer(r'[\u4e00-\u9fa5]+', text):
        segment = match.group()
        if len(segment) < 2:
            continue
        # 生成 bigram
        for i in range(len(segment) - 1):
            tokens.add(segment[i:i + 2])
    
    # 提取英文/数字词（≥2 字符）
    for match in re.finditer(r'[a-zA-Z]{2,}|[0-9]{2,}', text):
        tokens.add(match.group())
    
    # 过滤纯标点 bigram
    # v7 遗留-04 修复：补充代词/量词/连词/介词/助词停用词
    _STOPWORD_BIGRAMS = {
        # 原有标点
        '，。', '。、', '、；', '；：', '：！', '！？',
        # 代词
        '我的', '你的', '他的', '她的', '它的', '我们', '你们', '他们', '她们',
        '它们', '这是', '那是', '这是', '哪些', '哪个', '什么', '怎么',
        # 量词
        '一个', '一种', '一项', '一批', '一类', '这个', '那个', '本次', '此本',
        # 连词/介词
        '因此', '所以', '因为', '由于', '对于', '关于', '至于', '除了',
        '不仅', '而且', '虽然', '但是', '然而', '即使', '尽管',
        # 助词
        '的了', '是吗', '呢吧', '而已', '的话',
        # 常见无意义 bigram
        '的条', '的款', '的项', '为了', '以及', '及其', '或者',
    }
    tokens = {t for t in tokens if t not in _STOPWORD_BIGRAMS}
    # 兜底：过滤纯标点
    tokens = {t for t in tokens if not all(c in '，。、；：！？的了吗呢吧' for c in t)}
    
    return tokens


def _tokenize_query(keywords: list[str]) -> list[str]:
    """将搜索关键词拆分为 bigram + 英数整词（与索引使用相同分词器）.
    
    C-05 修复（v6）：原方案对整个查询串做 bigram（含英数），但索引器对英文/数字
    用整词提取（[a-zA-Z]{2,}|[0-9]{2,}），导致英数查询 100% 失败。
    例如搜索"ISO9001"→原查询 bigram ["IS","SO","O9","90","00","01"]，索引中却是
    "ISO"、"9001"，完全不匹配。
    
    修复方案：_tokenize_query 改为先按 [\\u4e00-\\u9fa5]+|[a-zA-Z]{2,}|[0-9]{2,}
    切片，中文片段做 bigram，英数片段整词查询 — 与索引器 _tokenize_chinese 对齐。
    """
    query_tokens: list[str] = []
    for kw in keywords:
        # C-05: 统一切片逻辑 — 与 _tokenize_chinese 完全一致
        for match in re.finditer(r'[\u4e00-\u9fa5]+|[a-zA-Z]{2,}|[0-9]{2,}', kw):
            segment = match.group()
            # 检查是中文还是英数
            if re.match(r'[\u4e00-\u9fa5]', segment):
                # 中文片段：生成 bigram
                if len(segment) <= 2:
                    query_tokens.append(segment)
                else:
                    for i in range(len(segment) - 1):
                        query_tokens.append(segment[i:i + 2])
            else:
                # 英文/数字片段：整词查询（与索引一致）
                query_tokens.append(segment)
    return query_tokens
```

**召回率验证（M9 预估）**：
- 搜索 `["评分", "分值", "得分", "评标"]` → bigram: `["评分", "分值", "得分", "评标"]`
- 含「评分标准」的页面 → 索引含 `"评分"` → ✅ 命中
- 含「评标办法」的页面 → 索引含 `"评标"` → ✅ 命中
- 含「分值分配」的页面 → 索引含 `"分值"` → ✅ 命中
- C-05 修复验证：搜索 `["ISO9001", "GB/T"]` → 查询 tokens: `["ISO", "9001", "GB"]`（英数整词）→ 索引中匹配 `"ISO"`、`"9001"`、`"GB"` → ✅ 命中
- 预期召回率：≥ 90%（仅遗漏不含任何 bigram 匹配的极端情况）

#### 3.2.3 倒排索引内存占用分析（Major 修复）

**问题**：v3 未分析 2000 页文档的倒排索引内存占用，可能导致序列化 JSON 过大或加载缓慢。

**估算模型**：

```
假设：2000 页 PDF，每页平均 3000 字符（中文），~1500 个 bigram/页（去重后 ~800 unique）

Unique bigram 总量估算：
- 2000 页 × 800 unique bigram/页 = 1,600,000 bigram（含重复）
- 去重后 unique bigram：~50,000-80,000（中文常用 bigram 约 5-8 万个）

JSON 序列化大小：
- 每个 entry: {"bigram": [page1, page2, ...]} 
- 平均每个 bigram 出现在 5 页：~50 字节/entry
- 80,000 entries × 50 字节 = ~4MB JSON

内存占用（加载后）：
- Python dict[str, list[int]]：约 JSON 大小的 3-5x
- ~4MB × 4 = ~16-20MB 内存

结论：倒排索引内存占用 ~16-20MB，完全可控。
Reader 懒加载时反序列化 ~4MB JSON ≈ 0.5-1 秒，可接受。

v7 NC-07 补充：反向索引 _page_to_tokens 内存
```
反向索引 _page_to_tokens: dict[int, set[str]]
- 每个 page 对应一个 set[str]，存储该页的所有 token（~800 unique bigram/页）
- 单个 set 内存：~800 × 60 字节 ≈ 48KB/页
- 2000 页 × 48KB = ~96MB
- 去重后（跨页共享 token）：实际 ~60-80MB

反向索引总内存：~60-80MB
正向索引 dict[str, list[int]] 内存：~16-20MB
倒排索引模块总内存：~76-100MB

JSON 序列化时仅保存正向索引（反向索引是运行时辅助结构，不持久化）。
加载时重建反向索引：~80MB dict 构建 ≈ 2-3 秒（可接受，首次 search 时触发）。
```

| 文档规模 | 页数 | Unique Bigram | JSON 大小 | 正向索引内存 | 反向索引内存 | 总内存 | 加载时间 |
|---------|------|--------------|----------|------------|------------|--------|---------|
| 小（50MB） | 500 | ~20,000 | ~1MB | ~5MB | ~20MB | ~25MB | ~0.2s |
| 中（200MB） | 2000 | ~80,000 | ~4MB | ~20MB | ~80MB | ~100MB | ~0.5s |
| 大（500MB） | 5000 | ~150,000 | ~8MB | ~40MB | ~200MB | ~240MB | ~1.0s |

> 注意：2000 页文档反向索引 ~80MB 内存，加上 PaddleOCR 4 worker ~6GB，
> 总峰值内存 ~6.1GB，在 8GB+ 服务器上可控。
```

### 3.3 阶段 3：磁盘持久化

#### 3.3.1 存储结构

```
data/parsed/{document_id}/
├── meta.json              # 文件元信息 + OCR 统计
├── index.json             # 章节索引（页码范围 → 章节名）
├── inverted_index.json    # 倒排索引（关键词 → 页码列表）
├── pages/                 # 逐页文件（兼容模式）
│   ├── page_0001.txt      # 第 1 页纯文本
│   ├── page_0002.txt
│   └── ...
├── pages.blob             # 合并存储（优化模式，见 §3.3.2）
├── page_offsets.json      # 偏移量索引（配合 pages.blob）
├── tables.json            # 全部表格（结构化 JSON）
├── chapters/               # C-02 修复：重命名 sections→chapters
│   ├── chapter_01.txt     # 按章节合并的文本
│   └── ...
```

#### 3.3.2 磁盘 I/O 优化：合并文件 + 偏移量索引（Major 修复）

**问题**：2000 页生成 2000 个 `page_NNNN.txt` 小文件。大量小文件在大多数文件系统上效率低下：
- ext4/XFS：每个 inode 至少 256 字节，2000 个文件 = ~500KB 元数据开销
- 读取时每次 `open() + read() + close()` 系统调用开销 ~0.1ms × 2000 = 200ms
- 目录遍历变慢

**方案**：合并为单个 `pages.blob` 文件 + `page_offsets.json` 偏移量索引。

```
data/parsed/{document_id}/
├── pages.blob              # 所有页面文本合并存储
├── page_offsets.json       # {page_num: [offset, length]}
├── ...
```

```python
class PageBlobReader:
    """合并文件读取器，通过偏移量随机访问单页内容.
    
    写入时追加到 blob 文件末尾，记录 [offset, length]。
    读取时 seek + read，O(1) 定位，无系统调用开销。
    
    C-07 修复（v6）：统一写入路径 — _stream_parse_pdf 中使用此类写入页面，
    替代原 page_file.write_text，确保 pages.blob 真正生成。
    
    线程安全修复（v6）：增加 threading.Lock 保护写入操作，
    修复 stat().st_size 与 write 之间的 TOCTOU 竞态。
    """
    
    def __init__(self, parsed_dir: Path):
        self.blob_path = parsed_dir / "pages.blob"
        self.offsets_path = parsed_dir / "page_offsets.json"
        # 线程安全：保护 write_page 的偏移量计算
        self._write_lock = threading.Lock()
        if self.offsets_path.exists():
            self.offsets = json.loads(self.offsets_path.read_text())
        else:
            self.offsets = {}
    
    def write_page(self, page_num: int, text: str) -> None:
        """追加写入一页文本到 blob 文件（线程安全）."""
        encoded = text.encode("utf-8")
        with self._write_lock:  # 线程安全保护
            # 修复 TOCTOU：用 open "ab" 的 f.tell() 替代 stat().st_size
            with open(self.blob_path, "ab") as f:
                offset = f.tell()
                f.write(encoded)
            self.offsets[str(page_num)] = [offset, len(encoded)]
    
    def read_page(self, page_num: int) -> str:
        """通过偏移量读取单页文本."""
        key = str(page_num)
        if key not in self.offsets:
            return ""
        offset, length = self.offsets[key]
        with open(self.blob_path, "rb") as f:
            f.seek(offset)
            return f.read(length).decode("utf-8")
    
    def flush_offsets(self) -> None:
        """将偏移量索引写入磁盘（原子写入）."""
        # C-12: 原子写入
        tmp = self.offsets_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.offsets, ensure_ascii=False))
        os.replace(tmp, self.offsets_path)
    
    def update_page(self, page_num: int, text: str) -> None:
        """更新已有页面内容（OCR 回写场景）.
        
        v6 新增：OCR 回写时，旧占位文本残留在 blob 中，新 OCR 文本追加到末尾。
        偏移量索引更新为新位置，旧块标记为废弃。
        长期累积的废弃块由清理策略定期 compact。
        """
        encoded = text.encode("utf-8")
        with self._write_lock:
            with open(self.blob_path, "ab") as f:
                offset = f.tell()
                f.write(encoded)
            # 更新偏移量指向新位置（旧块自然废弃）
            self.offsets[str(page_num)] = [offset, len(encoded)]
```

**性能对比**：

| 方案 | 2000 页写入 | 2000 页读取 | 磁盘元数据 | 实现复杂度 |
|------|-----------|-----------|----------|----------|
| 逐页文件（v3） | 2000 次 open/write | 2000 次 open/read | ~500KB inode | 低 |
| 合并 blob（优化） | 1 次持续写入 | 1 次 open + 2000 次 seek | ~12KB JSON | 中 |

**兼容策略**：`ParsedDocumentReader.get_page()` 内部检测 `pages.blob` 是否存在：
- 存在 → 使用 `PageBlobReader`（优先）
- 不存在 → 回退到逐页文件读取（向后兼容）

#### 3.3.3 元信息文件（meta.json）

```json
{
  "document_id": "doc_a3b4c5d6",
  "filename": "XX工程招标文件.pdf",
  "file_size_mb": 234.5,
  "file_type": "pdf",
  "page_count": 1856,
  "total_chars": 4520000,
  "total_tables": 127,
  "scoring_tables": 8,
  "qualification_tables": 15,
  "scan_page_count": 23,
  "scan_page_ratio": 0.012,
  "ocr_page_count": 18,
  "ocr_page_ratio": 0.010,
  "ocr_failed_pages": [45, 46, 47],
  "parse_mode": "streaming",
  "parsed_at": "2026-07-11T14:30:00Z",
  "parse_duration_sec": 127.5
}
```

#### 3.3.4 AgentState 中的表示

```python
# AgentState 中 document 记录的新格式
{
    "filename": "XX工程招标文件.pdf",
    "path": "/tmp/xxx.pdf",
    "type": "pdf",
    "status": "parsed",
    "parse_mode": "streaming",          # 新增：解析模式
    "parsed_dir": "data/parsed/doc_a3b4c5d6",  # 新增：解析结果目录（非临时）
    "meta": {                            # 新增：元信息摘要
        "page_count": 1856,
        "total_chars": 4520000,
        "total_tables": 127,
        "scoring_tables": 8,
        "qualification_tables": 15,
        "scan_page_ratio": 0.012,
    },
    "chapter_index": [                   # C-02 修复：重命名 sections→chapter_index，避免与 AgentState 顶层 sections:dict[str,str] 类型冲突
        {"name": "第一章 投标须知", "key": "ch1", "page_range": [1, 45]},
        ...
    ],
    # 以下字段保留但为空（向后兼容）
    "parsed_content": "",                # 大文件模式下为空
    "raw_text": "",                      # M7 修复：大文件模式下也必须为空
    "chunks": [],                        # 大文件模式下为空
    "chunk_count": 0,
    "tables": [],                        # 表格已在 tables.json 中
}
```

#### M7 修复：raw_text 字段清理

**问题**：现有代码 `_parse_single_doc` 第 476 行设置了 `result["raw_text"] = raw_text`。如果大文件模式下仍设置此字段，全文会通过 `raw_text` 留在 AgentState 中，内存优化完全失效。

**修复**：大文件模式下，`_parse_single_doc` 的 streaming 分支必须同时跳过 `raw_text` 和 `parsed_content`：

```python
def _parse_single_doc(file_record: dict, chunk_size: int = CHUNK_SIZE) -> dict:
    # ... existing file type detection ...
    
    file_size = Path(file_path).stat().st_size
    if file_size > LARGE_FILE_THRESHOLD:
        # ── streaming 模式 ──
        parsed_dir = _create_parsed_dir(file_record)
        # C-11: _stream_parse_pdf 现在返回 5 元组（含元信息 dict）
        index_builder, tables, chapter_index, total_chars, parse_meta = _stream_parse_pdf(
            file_path, parsed_dir, progress_callback=None
        )
        result.update({
            "status": "parsed",
            "parse_mode": "streaming",
            "parsed_dir": str(parsed_dir),
            "meta": _build_meta(
                file_path, tables, index_builder,
                total_chars=total_chars,
                probe_info=parse_meta["probe_info"],
                scan_page_count=parse_meta["scan_page_count"],
                ocr_page_count=parse_meta["ocr_page_count"],
                ocr_failed_pages=parse_meta["ocr_failed_pages"],
                parse_duration_sec=parse_meta["parse_duration_sec"],
            ),  # C-11 修复：补全调用链
            "chapter_index": chapter_index,  # C-02 修复：重命名 sections→chapter_index
            # M7 修复：以下字段必须为空，否则全文会进入 AgentState
            "parsed_content": "",     # ← 空
            "raw_text": "",           # ← 空（v2 遗漏）
            "chunks": [],             # ← 空
            "chunk_count": 0,         # ← 0
            "tables": tables,         # 表格同时写入 tables.json 和 AgentState
            "error": None,
        })
    else:
        # ── inline 模式（现有逻辑不变）──
        raw_text = _load_pdf(file_path)
        # ... existing inline logic ...
```

### 3.4 阶段 4：按需检索消费（Reader 接口）

#### 3.4.1 SearchResult 数据结构 + ParsedDocumentReader 完整设计

```python
from dataclasses import dataclass


@dataclass
class SearchResult:
    """倒排索引搜索结果项."""
    page_num: int       # 页码（1-based）
    text: str           # 该页完整文本
    hit_count: int      # 命中的 bigram 数量（用于排序）


class ParsedDocumentReader:
    """大文件解析结果的读取器，支持按页/按章节/按关键词检索.
    
    NC6 修复：统一为懒加载版本 — __init__ 不加载任何 JSON 文件，
    通过 @property 在首次访问时才读取磁盘。避免实例化时
    一次性加载 meta.json + index.json + inverted_index.json + tables.json
    四个大 JSON 文件导致内存尖峰。
    """
    
    def __init__(self, parsed_dir: str):
        self.parsed_dir = Path(parsed_dir)
        # NC6: 懒加载 — 不在 __init__ 中加载 JSON
        self._meta: dict | None = None
        self._index: dict | None = None
        self._inverted_index: dict | None = None
        self._tables_cache: list[dict] | None = None
        
        # M5: PageBlobReader 懒加载
        self._blob_reader: PageBlobReader | None = None
        
        # 手动 LRU 缓存（不能用 @lru_cache，因为 self 不 hashable）
        from collections import OrderedDict
        self._page_cache: OrderedDict[int, str] = OrderedDict()
        # P2 优化（v6）：LRU 容量 50→200 页，200×3000字符≈600KB 仍可控
        # 原 50 页太小：search_pages(top_k=20) 一次就可能填满，get_summary 再取 10 页频繁淘汰
        self._cache_max_size = 200
    
    @property
    def meta(self) -> dict:
        if self._meta is None:
            self._meta = json.loads((self.parsed_dir / "meta.json").read_text())
        return self._meta
    
    @property
    def index(self) -> dict:
        if self._index is None:
            self._index = json.loads((self.parsed_dir / "index.json").read_text())
        return self._index
    
    @property
    def inverted_index(self) -> dict:
        if self._inverted_index is None:
            self._inverted_index = json.loads(
                (self.parsed_dir / "inverted_index.json").read_text()
            )
        return self._inverted_index
    
    def _evict_if_needed(self):
        """当缓存超过上限时，移除最旧的条目."""
        while len(self._page_cache) > self._cache_max_size:
            self._page_cache.popitem(last=False)
    
    def get_page(self, page_num: int) -> str:
        """读取指定页的文本（带 LRU 缓存）.
        
        C3 修复：cache hit 时也调用 move_to_end 更新访问顺序。
        v2 的 bug：cache hit 时直接 return，不更新顺序 →
        频繁访问的页面可能被淘汰（实际是 FIFO 而非 LRU）。
        
        M5 修复：优先使用 PageBlobReader（pages.blob）读取，
        不存在时回退到逐页文件读取（向后兼容）。
        """
        if page_num in self._page_cache:
            # C3 修复：cache hit 也要移到末尾（标记为最近使用）
            self._page_cache.move_to_end(page_num)
            return self._page_cache[page_num]
        
        # M5: 优先使用 PageBlobReader
        content = self._read_page_from_storage(page_num)
        self._page_cache[page_num] = content
        self._evict_if_needed()
        return self._page_cache[page_num]
    
    def _read_page_from_storage(self, page_num: int) -> str:
        """M5: 从存储层读取单页文本 — 优先 blob，回退逐页文件."""
        blob_path = self.parsed_dir / "pages.blob"
        if blob_path.exists():
            # 使用 PageBlobReader（懒初始化）
            if self._blob_reader is None:
                self._blob_reader = PageBlobReader(self.parsed_dir)
            return self._blob_reader.read_page(page_num)
        else:
            # 回退：逐页文件读取
            path = self.parsed_dir / "pages" / f"page_{page_num:04d}.txt"
            return path.read_text(encoding="utf-8")
    
    def get_section(self, section_key: str) -> str:
        """读取指定章节的完整文本."""
        path = self.parsed_dir / "chapters" / f"{section_key}.txt"  # C-02: sections→chapters
        return path.read_text(encoding="utf-8") if path.exists() else ""
    
    def get_tables(self, table_type: str | None = None) -> list[dict]:
        """读取表格，可选按类型过滤.
        
        M4 优化：缓存表格数据，避免每次调用都读磁盘 + 反序列化。
        NC6 统一：懒加载属性方式一致。
        """
        if self._tables_cache is None:
            self._tables_cache = json.loads(
                (self.parsed_dir / "tables.json").read_text()
            )
        tables = self._tables_cache
        if table_type:
            tables = [t for t in tables if t["type"] == table_type]
        return tables
    
    def search_pages(self, keywords: list[str], top_k: int = 20) -> list[SearchResult]:
        """通过倒排索引快速定位包含关键词的页面.
        
        C1 修复：使用 bigram 分词查询，确保查询 key 与索引 key 匹配。
        M9 增强：增加二次检索兑底机制。
        
        时间复杂度：O(K) 其中 K 是 bigram 数量，与总页数无关。
        """
        matched_pages: dict[int, int] = {}  # {page_num: hit_count}
        
        # C1 修复：将关键词拆分为 bigram 后查询
        query_tokens = _tokenize_query(keywords)
        
        for token in query_tokens:
            page_nums = self.inverted_index.get(token, [])
            for page_num in page_nums:
                matched_pages[page_num] = matched_pages.get(page_num, 0) + 1
        
        # M9: 如果倒排索引命中率不足，进行二次检索（线性扫描前 100 页）
        if len(matched_pages) < 3:
            logger.info(
                f"search_pages: 倒排索引仅命中 {len(matched_pages)} 页，"
                f"启动二次检索（线性扫描前 100 页）"
            )
            for page_num in range(1, min(101, self.meta["page_count"] + 1)):
                if page_num in matched_pages:
                    continue
                text = self.get_page(page_num)
                for kw in keywords:
                    if kw in text:
                        matched_pages[page_num] = matched_pages.get(page_num, 0) + 1
        
        # 按命中率排序
        sorted_pages = sorted(matched_pages.items(), key=lambda x: x[1], reverse=True)
        
        # 返回 top_k 页面的完整文本
        results = []
        for page_num, hit_count in sorted_pages[:top_k]:
            text = self.get_page(page_num)
            results.append(SearchResult(
                page_num=page_num, text=text, hit_count=hit_count
            ))
        
        return results
    
    def get_summary(self) -> str:
        """获取文档摘要（前 10 页 + 项目信息页）.
        
        用于 ReqExtractor 的 LLM 输入，优先包含：
        1. 前 10 页（通常含项目基本信息、招标公告）
        2. 包含"项目名称""招标编号"等关键词的页面（通过倒排索引定位）
        
        总长度控制在 3000-5000 字符内.
        """
        parts = []
        
        # 前 10 页
        for i in range(1, min(11, self.meta["page_count"] + 1)):
            parts.append(self.get_page(i))
        
        # 关键信息页（通过倒排索引定位）
        key_info_keywords = ["项目名称", "招标编号", "招标人", "采购人", "包号"]
        for result in self.search_pages(key_info_keywords, top_k=5):
            if result.page_num > 10:  # 避免与前 10 页重复
                parts.append(result.text)
        
        combined = "\n\n".join(parts)
        return combined[:5000]  # 截断到 5000 字符
```

#### 3.4.2 ReqExtractor 适配

```python
def req_extractor(state: AgentState, llm_fn=None) -> dict:
    documents = state.get("documents", [])
    
    # ── 优先消费结构化表格（无变化）──
    table_scoring, table_quals = _requirements_from_tables(extracted_tables)
    
    # ── 大文件模式：按需检索关键页面 ──
    for doc in documents:
        if doc.get("parse_mode") == "streaming":
            reader = ParsedDocumentReader(doc["parsed_dir"])
            
            # 1. 评分标准 → 搜索包含"评分""分值""得分"的页面
            scoring_pages = reader.search_pages(
                ["评分", "分值", "得分", "评分标准", "评标"], top_k=10
            )
            
            # 2. 资质要求 → 搜索包含"资质""资格""证书"的页面
            qual_pages = reader.search_pages(
                ["资质", "资格", "证书", "许可", "认证"], top_k=10
            )
            
            # 3. 技术规格 → 搜索包含"技术参数""规格""要求"的页面
            tech_pages = reader.search_pages(
                ["技术参数", "规格", "技术要求"], top_k=10
            )
            
            # 4. 格式要求 → 搜索包含"格式""密封""装订""份数"的页面
            format_pages = reader.search_pages(
                ["格式要求", "密封", "装订", "份数", "签字", "盖章"], top_k=5
            )
            
            # 5. 项目基本信息 → 通常在前 10 页
            first_pages = "\n".join(
                reader.get_page(i) for i in range(1, min(11, reader.meta["page_count"] + 1))
            )
            
            # 6. M8 修复：bid_subtype 子类型识别使用前 10 页文本
            # v2 问题：combined[:3000] 在大文件模式下可能是评分标准页而非项目概况
            # 修复：大文件模式下用 first_pages（前 10 页）做子类型识别
            bid_subtype_detect_text = first_pages[:3000]
            
            # 7. 合并关键内容，控制在 MAX_INPUT_CHARS 以内
            combined = _assemble_relevant_content(
                first_pages=first_pages,
                scoring_pages=scoring_pages,
                qual_pages=qual_pages,
                tech_pages=tech_pages,
                format_pages=format_pages,
                max_chars=MAX_INPUT_CHARS
            )
        else:
            # 小文件模式：保持现有逻辑
            combined = doc.get("parsed_content", "")
```

#### 3.4.3 _assemble_relevant_content 算法（NC1 修复：预算分配策略）

**NC1 问题**：v3 的 `first_pages`（10 页 × ~3000 字符 = ~30,000 字符）单独就远超 `MAX_INPUT_CHARS=8000` 的总预算。后续 `combined[:max_chars]` 截断时，搜索到的评分/资质/技术/格式页面全部被丢弃，倒排索引 + search_pages 机制完全白做。

**NC1 修复**：采用 **预算分配策略**，为每个信息类别分配固定字符预算，确保所有类别都有机会进入 LLM 输入。

```python
# 预算分配表（总计 = MAX_INPUT_CHARS = 8000）
_BUDGET_ALLOC = {
    "project_info":  2000,  # 项目基本信息（前 3 页精华）
    "scoring":       1500,  # 评分标准（倒排索引搜索结果）
    "qualification": 1500,  # 资质要求
    "tech_spec":     1500,  # 技术规格
    "format":        1000,  # 格式要求
    "reserve":        500,  # 保留/分隔符/截断提示
}


def _assemble_relevant_content(
    first_pages: str,
    scoring_pages: list[SearchResult],
    qual_pages: list[SearchResult],
    tech_pages: list[SearchResult],
    format_pages: list[SearchResult],
    max_chars: int = MAX_INPUT_CHARS,
) -> str:
    """将多组搜索结果合并，控制在 max_chars 内.
    
    NC1 修复：预算分配策略
    - 不再无条件 append first_pages（会导致后续搜索结果被截断丢弃）
    - 为每个信息类别分配固定字符预算
    - 每组只取 top-N 页面，每页截断到 per_page_limit
    - 确保所有类别都有机会进入 LLM 输入
    """
    parts: list[str] = []
    seen_pages: set[int] = set()

    def add_group(
        label: str,
        pages: list[SearchResult],
        budget: int,
        max_pages: int = 3,
        per_page_limit: int = 800,
    ) -> None:
        """在字符预算内添加一组搜索结果（去重 + 截断）.
        
        P2 优化（v6）：per_page_limit 按类别差异化
        - scoring/qualification: 1200（评分表单页常 >800 字符，含多条评分项）
        - tech_spec: 800（技术规格适中）
        - format: 600（格式要求简短）
        """
        used = 0
        for page in sorted(pages, key=lambda p: p.hit_count, reverse=True)[:max_pages]:
            if page.page_num in seen_pages:
                continue
            remaining = budget - used
            if remaining <= 100:  # 预算耗尽
                break
            text = page.text[:min(per_page_limit, remaining)]
            seen_pages.add(page.page_num)
            parts.append(f"--- {label}（第 {page.page_num} 页，命中 {page.hit_count} 次）---\n{text}")
            used += len(text) + 50  # 50 = 分隔符和标签的预估长度

    # 1. 项目基本信息（前 N 页，截断到 budget）
    project_budget = _BUDGET_ALLOC["project_info"]
    project_text = first_pages[:project_budget]
    parts.append(f"--- 项目基本信息 ---\n{project_text}")

    # 2. 评分标准（倒排索引搜索结果）— P2: per_page_limit=1200
    add_group("评分标准", scoring_pages, _BUDGET_ALLOC["scoring"], per_page_limit=1200)

    # 3. 资质要求 — P2: per_page_limit=1200
    add_group("资质要求", qual_pages, _BUDGET_ALLOC["qualification"], per_page_limit=1200)

    # 4. 技术规格 — P2: per_page_limit=800
    add_group("技术规格", tech_pages, _BUDGET_ALLOC["tech_spec"], per_page_limit=800)

    # 5. 格式要求 — P2: per_page_limit=600
    add_group("格式要求", format_pages, _BUDGET_ALLOC["format"], per_page_limit=600)

    # 6. 合并并做最终安全截断
    combined = "\n\n".join(parts)
    if len(combined) > max_chars:
        combined = combined[:max_chars - 30] + "\n\n[... 内容已截断 ...]"

    return combined
```

**预算分配验证**：

| 类别 | 预算 | 来源 | 说明 |
|------|------|------|------|
| 项目基本信息 | 2000 | 前 3 页精华 | 项目名称、招标编号、招标人通常在此 |
| 评分标准 | 1500 | 倒排索引搜索 | top-3 页面 × 800 字符/页 |
| 资质要求 | 1500 | 倒排索引搜索 | top-3 页面 × 800 字符/页 |
| 技术规格 | 1500 | 倒排索引搜索 | top-3 页面 × 800 字符/页 |
| 格式要求 | 1000 | 倒排索引搜索 | top-2 页面 × 800 字符/页 |
| 保留 | 500 | 分隔符 + 截断提示 | — |
| **总计** | **8000** | | = MAX_INPUT_CHARS |

**对比 v3 修复前**：
- 修复前：`first_pages` 独占 30,000 字符 → 截断到 8,000 → 搜索结果全部丢弃
- 修复后：`first_pages` 限制在 2,000 字符 → 6,000 字符留给 4 组搜索结果 → 每组至少 1,000 字符有效内容

### 3.5 跨阶段持久化（两阶段管道）

#### 3.5.1 问题

- Phase 1（`build_extraction_graph`）与 Phase 2（`build_generation_graph`）之间，用户可能关闭浏览器
- 如果 `parsed_dir` 是临时路径（`/tmp`），Phase 2 可能找不到文件
- Streamlit `session_state` 有大小限制，无法存储全文

#### 3.5.2 策略

**非临时目录 + TTL 清理**：

```python
# 解析时使用非临时目录
# M2 修复：统一使用 _create_parsed_dir 生成 document_id（md5 hash），不再使用 uuid
from core.parse_helpers import _create_parsed_dir

parsed_dir = _create_parsed_dir(file_record)
# document_id 由 _create_parsed_dir 内部通过 md5(filename + file_path) 生成
# 同一文件重复上传会复用同一目录，支持断点续传

# TTL 清理（v7.1 M-V7-13: 统一使用 _cleanup_expired_parsed_dirs，定义见 §3.8）
# 原 _cleanup_old_parsed_dirs 已合并到 _cleanup_expired_parsed_dirs
# 统一使用 meta.json 的 parsed_at 为主，st_mtime 为兑底

# 启动和重置时调用
_cleanup_expired_parsed_dirs()
```

#### 3.5.3 LangGraph 状态传播（C5 修复：API 签名修正）

**C5 问题**：v2 写了 `build_generation_graph(llm_fns=llm_fns, initial_state=extraction_result)`，但实际代码中 `build_generation_graph` 的签名是 `(llm_fns, search_fn)` — 不接受 `initial_state` 参数。

**C5 修复**：State 是在 `app.invoke(state)` 时传入的，不是在构建 graph 时。以下与 `review_panel.py` 中的实际代码对齐：

```python
# review_panel.py — Phase 2 调用方式（与现有代码一致）

from core.graph import build_generation_graph

# 1. 构建 graph（不传 state）
generation_graph = build_generation_graph(llm_fns=llm_fns)

# 2. 用 Phase 1 的结果作为 initial_state 传入 invoke
phase2_state = {**extraction_result}
# 确保关键状态字段存在
phase2_state["info_verification_status"] = "confirmed"
phase2_state["review_status"] = ""

# 3. 执行 Phase 2
result = generation_graph.invoke(phase2_state)
```

**跨阶段持久化保障**：
- `parsed_dir` 存储在 `documents[].parsed_dir` 字段中
- `extraction_result` 通过 Streamlit `session_state["extraction_result"]` 在 Phase 1/2 之间传递
- 即使浏览器刷新，只要 `session_state` 未丢失，`parsed_dir` 路径仍指向磁盘上的持久化数据
- TTL 72 小时确保跨天使用时数据仍存在

### 3.6 OCR 完整集成策略（M1 + M3 修复）

#### 3.6.1 PaddleOCR 引擎管理 + 超时机制（v7 统一重构）

> **v7 重构说明**：v6 的 C-08（线程级实例池）和 C-09（子进程超时）存在架构矛盾：
> - C-08 的 `_ocr_engine_pool` 是进程内字典，子进程无法访问
> - C-09 每页启动新子进程，PaddleOCR 模型加载 10-30s/次，750 页 = 4.5+ 小时
>
> v7 统一为 **ProcessPoolExecutor + initializer** 方案：
> - worker 进程启动时通过 `initializer` 预加载 PaddleOCR，整个 OCR 批次内复用
> - 超时后 `terminate()` 真正回收子进程资源
> - 放弃线程级实例池（进程池自带隔离，不需要）

```python
# ── PaddleOCR 进程级单例 ──────────────────────────────────────────────

# v7: 每个 worker 进程的全局 PaddleOCR 实例（由 initializer 预加载）
_worker_ocr_engine = None

def _ocr_worker_init():
    """ProcessPoolExecutor initializer — 每个 worker 进程启动时加载一次 PaddleOCR.
    
    v7 NC-01/NC-02 修复核心：
    - 模型加载 10-30s 只在 worker 进程启动时发生一次
    - 后续所有 OCR 任务复用同一实例，无重复加载
    - max_workers=4 时仅加载 4 次模型（v6 C-09 方案加载 750 次）
    
    内存估算：
    - 每个 PaddleOCR 实例常驻 ~1.5GB（CPU 模式）
    - 4 个 worker = ~6GB（默认配置，可接受）
    - 极端场景 8 个 worker = ~12GB（_MAX_CONCURRENT_OCR=8，需确保内存充足）
    - 若内存不足，减少 max_workers 到 2（~3GB）
    
    v7.2 C-V7.1-02: 添加 logging.basicConfig，确保 spawn 方式下子进程日志可见。
    """
    global _worker_ocr_engine
    # v7.2 C-V7.1-02: spawn 方式下子进程不继承父进程 logger 配置
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [PID=%(process)d] %(levelname)s %(message)s',
    )
    try:
        from paddleocr import PaddleOCR
        _worker_ocr_engine = PaddleOCR(
            use_angle_cls=True,  # 方向分类
            lang="ch",           # 中文
            show_log=False,
            use_gpu=False,       # CPU 模式
            # 版本锁定：paddleocr==2.7.0.3 + paddlepaddle==2.6.1
        )
        import os
        logger.info(f"PaddleOCR engine loaded in worker PID={os.getpid()} (CPU mode)")
    except ImportError:
        logger.warning(f"PaddleOCR not installed — OCR disabled in worker PID={os.getpid()}")
        _worker_ocr_engine = None
    except Exception as e:
        logger.warning(f"Failed to init PaddleOCR in worker: {e}")
        _worker_ocr_engine = None


def _ocr_worker_task(
    page_idx: int,
    img_bytes: bytes,
    img_shape: tuple,
    img_dtype: str,
    parsed_dir_str: str,
    timeout_sec: int,
) -> tuple[int, str]:
    """在已初始化的 worker 进程中执行 OCR（复用预加载的引擎）.
    
    v7 NC-01 修复：不再每页创建新 PaddleOCR 实例，直接使用 _worker_ocr_engine。
    v7 NC-02 修复：不再需要 _ocr_engine_pool（进程池自带隔离）。
    
    v7.1 M-V7-05: 统一使用 logger（_ocr_worker_init 中已配置 basicConfig）。
    v7.1 m-V7-01: timeout_sec 参数仅供文档参考，实际超时由父进程 future.result() 控制。
    """
    global _worker_ocr_engine
    
    if _worker_ocr_engine is None:
        return (page_idx, "")
    
    page_num = page_idx + 1
    parsed_dir = Path(parsed_dir_str)
    
    # 检查 OCR 缓存
    ocr_cache_file = parsed_dir / "pages" / f"ocr_{page_num:04d}.txt"
    if ocr_cache_file.exists():
        return (page_idx, ocr_cache_file.read_text(encoding="utf-8"))
    
    try:
        import numpy as np
        # 重建 numpy 数组
        img_array = np.frombuffer(img_bytes, dtype=img_dtype).reshape(img_shape)
        
        # 使用预加载的引擎执行 OCR
        result = _worker_ocr_engine.ocr(img_array)
        
        if result is None:
            return (page_idx, "")
        
        text_lines = []
        for line in result:
            # v7.1 m-V7-04: 增强 None 结果健壮性
            if line is None:
                continue
            if isinstance(line, list) and len(line) >= 2:
                text_lines.append(line[1][0])  # PaddleOCR: [bbox, (text, confidence)]
        
        ocr_text = "\n".join(text_lines)
        
        # 写入 OCR 缓存
        ocr_cache_file.write_text(ocr_text, encoding="utf-8")
        
        return (page_idx, ocr_text)
        
    except Exception as e:
        logger.warning(f"[OCR Worker] page {page_num} failed: {e}")
        return (page_idx, "")
```

> **NC-06 修复（v7）**：v6 的 `_ocr_page` 函数（~80 行）是死代码——
> 整个 spec 中没有任何地方调用它（`_stream_parse_pdf` 只收集 `scan_page_indices`，
> `_ocr_batch_parallel_safe` 有自己的内联 OCR 逻辑）。
> v7 已删除 `_ocr_page`，OCR 逻辑统一在 `_ocr_batch_parallel_safe` 中。
>
> **版本锁定（v6 保留）**：
> - requirements: `paddleocr==2.7.0.3`, `paddlepaddle==2.6.1`
> - 避免使用 `use_gpu`（3.x 已更名为 `device`）
> - 离线模型预置：提供 `scripts/download_ocr_models.py`

#### 3.6.2 并行 OCR 策略（NC2 修复：预渲染 + 多进程 OCR）

**NC2 问题**：v3 的 `_ocr_batch_parallel` 在线程池中直接调用 `doc[page_idx]` 和 `page.get_pixmap()`，但 PyMuPDF（fitz）的 `Document` 对象**不是线程安全的**。多线程同时访问同一 Document 会导致段错误（segfault）。

**NC2 修复方案**：**预渲染 + 多进程 OCR**（v7: 线程池→进程池）
1. **主线程**（单线程）：遍历所有扫描页，用 fitz 渲染为 numpy 数组（fitz 单线程安全）
2. **进程池**（多进程）：只对预渲染的 numpy 数组做 PaddleOCR 推理（进程级隔离，天然线程安全）
3. 渲染后立即释放 pixmap 内存，只保留 numpy 数组

```python
from concurrent.futures import ProcessPoolExecutor, as_completed, TimeoutError as FutureTimeoutError


def _ocr_batch_parallel_safe(
    doc: "fitz.Document",
    scan_page_indices: list[int],
    parsed_dir: Path,
    max_workers: int = 4,
    dpi: int = 200,
    timeout_sec: int = 30,
    batch_size: int = 50,
) -> dict[int, str]:
    """并行 OCR 多个扫描页（NC2 修复 + NC4 修复 + v7 ProcessPool 重构）.
    
    v7 NC-01/NC-02 修复核心：
    - 改用 ProcessPoolExecutor + initializer，替代 v6 的 ThreadPoolExecutor + 子进程
    - worker 进程启动时预加载 PaddleOCR，整个批次内复用
    - 消除 v6 C-09 每页重新加载模型的性能回归
    - 消除 v6 C-08 线程级实例池与子进程的架构矛盾
    
    NC2 修复核心（保留）：
    - Phase A（主线程，单线程）：用 fitz 渲染扫描页为 numpy 数组
    - Phase B（进程池，多进程）：只对 numpy 数组做 PaddleOCR 推理
    - fitz Document 对象永远只被主线程访问，杜绝段错误
    
    NC4 修复核心（保留）：
    - 分批处理：每批最多 batch_size 页（默认 50 页）
    - 每批：渲染 → OCR → 释放内存 → 下一批
    - 单批内存峰值：50 × 11.6MB ≈ 580MB（可控）
    - 避免 750 页全部预渲染导致 8.7GB OOM
    
    Args:
        doc: fitz.Document 对象（必须在调用方保持打开状态）
        scan_page_indices: 需要 OCR 的页面索引列表（0-based）
        parsed_dir: 解析结果目录（用于 OCR 结果缓存）
        max_workers: OCR 进程池大小
        dpi: 渲染分辨率
        timeout_sec: 单页 OCR 超时
        batch_size: 每批预渲染页数（控制内存峰值）
    
    Returns:
        {page_idx_0based: ocr_text}
    """
    results: dict[int, str] = {}

    if not scan_page_indices:
        return results

    # v7: 检查 PaddleOCR 是否可用
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        logger.warning("PaddleOCR not installed — skipping batch OCR")
        return results

    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    # 过滤已有缓存的页面
    pending_indices: list[int] = []
    for page_idx in scan_page_indices:
        page_num = page_idx + 1
        ocr_cache_file = parsed_dir / "pages" / f"ocr_{page_num:04d}.txt"
        if ocr_cache_file.exists():
            results[page_idx] = ocr_cache_file.read_text(encoding="utf-8")
        else:
            pending_indices.append(page_idx)

    if not pending_indices:
        logger.info("All scan pages already have OCR cache — skipping")
        return results

    # v7: 使用 ProcessPoolExecutor + initializer 预加载 PaddleOCR
    # 每个 worker 进程启动时调用 _ocr_worker_init 加载模型（仅一次）
    # 后续所有任务复用该实例，无重复加载
    # v7.1 C-V7-02: _calc_ocr_workers 内部 acquire 信号量，在 finally 中 release
    actual_workers = _calc_ocr_workers(requested=max_workers)
    pool = ProcessPoolExecutor(
        max_workers=actual_workers,
        initializer=_ocr_worker_init,
    )
    
    try:
        # ── 分批处理：每批 batch_size 页 ──────────────────────────
        total_batches = (len(pending_indices) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            batch_start = batch_idx * batch_size
            batch_end = min(batch_start + batch_size, len(pending_indices))
            batch_indices = pending_indices[batch_start:batch_end]

            logger.info(
                f"Batch {batch_idx + 1}/{total_batches}: "
                f"渲染 {len(batch_indices)} 页 (indices {batch_indices[0]}-{batch_indices[-1]})"
            )

            # Phase A: 主线程预渲染本批次的扫描页
            pre_rendered: list[tuple[int, bytes, tuple, str]] = []  # (idx, img_bytes, shape, dtype)
            for page_idx in batch_indices:
                page_num = page_idx + 1
                try:
                    page = doc[page_idx]
                    pixmap = page.get_pixmap(matrix=mat)

                    import numpy as np
                    img_array = np.frombuffer(pixmap.samples, dtype=np.uint8)
                    img_array = img_array.reshape(pixmap.height, pixmap.width, pixmap.n)
                    if pixmap.n == 4:
                        img_array = img_array[:, :, :3]  # RGBA → RGB

                    pixmap = None  # 释放 pixmap
                    # 序列化 img_array 供子进程使用
                    pre_rendered.append((
                        page_idx,
                        img_array.tobytes(),
                        img_array.shape,
                        str(img_array.dtype),
                    ))
                except Exception as e:
                    logger.warning(f"预渲染失败 page {page_num}: {e}")
                    results[page_idx] = ""

            if not pre_rendered:
                continue

            # Phase B: 进程池并行 OCR（只操作 numpy 数组，不接触 fitz）
            logger.info(
                f"Batch {batch_idx + 1}: 并行 OCR {len(pre_rendered)} 页, {actual_workers} workers"
            )

            futures = {
                pool.submit(
                    _ocr_worker_task,
                    idx, img_bytes, img_shape, img_dtype,
                    str(parsed_dir), timeout_sec,
                ): idx
                for idx, img_bytes, img_shape, img_dtype in pre_rendered
            }
            
            for future in as_completed(futures):
                page_idx = futures[future]
                try:
                    # v7: 使用 future.result(timeout=timeout_sec) 实现单页超时
                    result = future.result(timeout=timeout_sec)
                    results[result[0]] = result[1]
                except FutureTimeoutError:
                    logger.warning(f"OCR timed out after {timeout_sec}s for page {page_idx + 1}")
                    results[page_idx] = ""
                except Exception as e:
                    logger.warning(f"OCR process failed for page {page_idx + 1}: {e}")
                    results[page_idx] = ""

            # 释放本批次预渲染的数据
            pre_rendered.clear()

    finally:
        # v7: 关闭进程池，回收 worker 进程
        # v7.1 C-V7-03: shutdown(wait=False) 不会终止正在运行的 worker，
        # 需要手动 terminate 卡死的进程
        pool.shutdown(wait=False, cancel_futures=True)  # v7.1 m-V7-05: cancel_futures 需 Python 3.9+
        
        # v7.1 C-V7-03: 强制终止仍在运行的 worker 进程
        # ProcessPoolExecutor 的 _processes 属性记录了所有 worker 进程
        for pid, proc in getattr(pool, '_processes', {}).items():
            if proc.is_alive():
                logger.warning(f"Terminating stuck OCR worker PID={pid}")
                proc.terminate()
                proc.join(timeout=5)
                if proc.is_alive():
                    proc.kill()  # SIGKILL，最后手段
                    logger.warning(f"Force-killed OCR worker PID={pid}")
        
        # v7.1 C-V7-02: 释放信号量槽位
        for _ in range(actual_workers):
            _ocr_semaphore.release()
    
    success_count = sum(1 for v in results.values() if v)
    logger.info(
        f"OCR 完成: {success_count}/{len(scan_page_indices)} 页成功"
    )

    return results
```

#### 3.6.3 性能重估（v7: ProcessPool + initializer 后）

**v7 性能改进**：v6 C-09 每页启动子进程加载模型 10-30s，750 页 = 4.5+ 小时。
v7 ProcessPoolExecutor + initializer 仅在 worker 启动时加载模型（4 worker = 4 次加载 = ~2 分钟），
后续 750 页 OCR 推理 ~1-3 秒/页（CPU 模式），总时间回到 ~25-40 分钟。

**两阶段 OCR 时间模型**（v7 修正）：
- Phase 0（进程池初始化）：4 worker × 10-30s 模型加载 = ~40-120s（仅一次）
- Phase A（预渲染）：主线程单线程渲染，~0.3 秒/页
- Phase B（并行 OCR）：4 进程并行，~1-3 秒/页（CPU 模式）
- 总时间 = 流式解析时间 + Phase 0 + Phase A 时间 + Phase B 时间

| 场景 | 文件大小 | 页数 | 扫描页数 | Phase 0 初始化 | Phase A 预渲染 | Phase B 并行 OCR | 总预计时间 |
|------|---------|------|---------|---------------|---------------|-----------------|-----------|
| 纯文本 PDF | 200MB | ~1500 | 0 | 0s | 0s | 0s | 3-5 分钟 |
| 少量扫描 | 200MB | ~1500 | 30 (2%) | ~60s | ~9s | ~10-23s | 6-8 分钟 |
| 中等扫描 | 200MB | ~1500 | 150 (10%) | ~60s | ~45s | ~38-113s | 9-13 分钟 |
| 大量扫描 | 200MB | ~1500 | 300 (20%) | ~60s | ~90s | ~75-225s | 12-20 分钟 |
| 极端扫描 | 200MB | ~1500 | 750 (50%) | ~60s | ~225s | ~188-563s | 25-40 分钟 |

**修正后的成功标准**：
- 纯文本 PDF ≤ 10 分钟 ✅（与 v2 一致）
- 含 10% 扫描页 PDF ≤ 15 分钟
- 含 50% 扫描页 PDF ≤ 40 分钟（放宽到 30 分钟需 GPU 加速）

> **v6→v7 性能对比**：
> - v6 C-09 方案：750 页 OCR = 750 × (10s 加载 + 2s 推理) = ~3 小时（不可接受）
> - v7 ProcessPool 方案：750 页 OCR = 4 × 10s 加载 + 750/4 × 2s 推理 = ~6.5 分钟（可接受）
> - 性能提升 **~27x**，回到 v5 预期水平

#### 3.6.4 PaddleOCR 安装评估

| 项目 | 说明 |
|------|------|
| 依赖 | `paddlepaddle`（~1.5GB CPU 版）+ `paddleocr`（~100MB） |
| 安装命令 | `pip install paddlepaddle==2.6.1 paddleocr==2.7.0.3` |
| 冲突风险 | paddlepaddle 依赖 numpy/pyyaml，可能与现有环境冲突，建议用独立 venv 或 Docker |
| GPU 加速 | 安装 `paddlepaddle-gpu` 可将 OCR 速度提升 5-10x，但需要 CUDA 环境 |
| 首次启动 | PaddleOCR 首次运行会下载模型文件（~200MB），需联网 |

**v7 遗留-01：PaddleOCR 依赖冲突解决方案 — Dockerfile**

paddlepaddle 对 numpy 版本敏感（要求 <2.0），与项目其他依赖可能冲突。
推荐使用 Docker 隔离 PaddleOCR 环境：

```dockerfile
# docker/Dockerfile.ocr
# v7 遗留-01: PaddleOCR 独立运行环境
FROM python:3.10-slim

# 安装系统依赖（PaddleOCR 需要 libgomp, libgl）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 安装版本锁定的 PaddleOCR
RUN pip install --no-cache-dir \
    paddlepaddle==2.6.1 \
    paddleocr==2.7.0.3 \
    numpy==1.24.3 \
    PyYAML==6.0.1

# 预下载模型（避免首次运行下载）
# 提供 scripts/download_ocr_models.py 脚本
COPY scripts/download_ocr_models.py /tmp/
RUN python /tmp/download_ocr_models.py && rm /tmp/download_ocr_models.py

# OCR worker 入口点
WORKDIR /app
COPY bid-agent/ /app/bid-agent/

# 暴露 OCR RPC 端口（可选：如果 OCR 作为独立微服务）
# EXPOSE 50051
```

**部署方案选择**：

| 方案 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| **方案 A: 同进程** | 开发/测试 | 无序列化开销，代码简单 | 依赖冲突风险 |
| **方案 B: Docker 隔离** | 生产 | 无依赖冲突，可独立升级 | 需 Docker 环境 |
| **方案 C: 微服务** | 高并发 | 可独立扩缩容，资源隔离 | 序列化开销，网络延迟 |

> v7 推荐：开发期用方案 A（venv 隔离），生产用方案 B（Docker）。
> 方案 C（gRPC 微服务）作为未来扩展，当前 YAGNI。

#### 3.6.5 OCR 策略决策表

| 扫描页比例 | OCR 策略 | 并行度 |
|------------|---------|--------|
| < 5% | 跳过 + 标注（少量扫描页影响不大） | — |
| 5%-15% | 自动启用 OCR + 标注 | 4 进程 |
| 15%-30% | 自动启用 OCR + 提示用户预计时间 | 4 进程 |
| > 30% | 强制 OCR + 警告用户可能耗时较长 | 4 进程 + 提示 GPU 加速 |

### 3.7 向后兼容策略

**双模式设计**：

- 文件大小 ≤ 50MB → inline 模式（现有逻辑）
- 文件大小 > 50MB → streaming 模式（新逻辑）

下游节点通过 `doc.get("parse_mode")` 判断走哪条路径。

#### C-06 修复：_serialize_state 跳过全文深拷贝

**问题**：`graph.py:588` 的 `_serialize_state` 使用 `copy.deepcopy(snapshot)` 无条件深拷贝整个 AgentState。大文件模式下即使 `parsed_content=""`，若 `tables` 或 `meta` 仍大，deepcopy 仍有开销甚至 OOM。

**修复**：检测 streaming 模式时，跳过大字段的 deepcopy：

```python
import copy

def _serialize_state(state: dict) -> dict:
    """序列化 AgentState 用于快照/导出.
    
    C-06 修复（v6）：大文件模式（streaming）跳过 parsed_content/raw_text/chunks
    的深拷贝，避免 OOM。tables 字段也跳过（已在 tables.json 中持久化）。
    """
    snapshot = {}
    for key, value in state.items():
        if key == "documents":
            # 对 documents 列表做特殊处理
            docs_copy = []
            for doc in value:
                parse_mode = doc.get("parse_mode", "inline")
                if parse_mode == "streaming":
                    # C-06: streaming 模式跳过大字段深拷贝
                    doc_copy = dict(doc)  # v7.1 m-V7-08: 仅 streaming 时浅拷贝
                    doc_copy["parsed_content"] = ""  # 已经是空，确保
                    doc_copy["raw_text"] = ""
                    doc_copy["chunks"] = []
                    # tables 不深拷贝，只保留引用（已在 tables.json 中）
                    doc_copy["tables"] = doc.get("tables", [])  # 浅引用
                else:
                    # inline 模式正常深拷贝
                    doc_copy = copy.deepcopy(doc)
                docs_copy.append(doc_copy)
            snapshot[key] = docs_copy
        else:
            # 非 documents 字段正常深拷贝
            snapshot[key] = copy.deepcopy(value)
    return snapshot
```

### 3.8 清理策略

```python
def reset_pipeline_state():
    # ... 现有清理逻辑 ...
    
    # 清理解析结果目录
    docs = st.session_state.get("uploaded_docs", [])
    for doc in docs:
        parsed_dir = doc.get("parsed_dir", "")
        if parsed_dir and Path(parsed_dir).exists():
            shutil.rmtree(parsed_dir, ignore_errors=True)
    
    # v7: 清理 OCR 进程池（如有残留）
    # v6 的 _ocr_engine_pool 已废弃，v7 使用 ProcessPoolExecutor
    # ProcessPoolExecutor 在 _ocr_batch_parallel_safe 的 finally 中已 shutdown
    
    # v7 遗留-05: 定时清理过期解析目录
    _cleanup_expired_parsed_dirs()
```

```python
# v7 遗留-05: TTL 定时清理
import time

_PARSED_TTL_HOURS = 72  # 解析目录过期时间
_CLEANUP_INTERVAL_HOURS = 6  # 清理间隔
_last_cleanup_time = 0.0

def _cleanup_expired_parsed_dirs():
    """清理超过 TTL 的解析目录（v7 遗留-05 + v7.1 M-V7-13 合并统一）.
    
    v7.1 M-V7-13: 合并 §3.5 的 _cleanup_old_parsed_dirs 和此函数。
    时间判断策略：
    - 优先使用 meta.json 的 parsed_at（记录实际解析时间，最准确）
    - meta.json 不存在时回退到目录 st_mtime（兑底）
    
    触发时机：
    1. 每次上传前调用
    2. 每 6 小时自动触发
    3. reset_pipeline_state 时触发
    4. 启动时调用（原 §3.5 的 _cleanup_old_parsed_dirs 调用点）
    """
    global _last_cleanup_time
    now = time.time()
    
    # 限制清理频率：距上次清理不足 6 小时则跳过
    if now - _last_cleanup_time < _CLEANUP_INTERVAL_HOURS * 3600:
        return
    
    parsed_root = Path("data/parsed")
    if not parsed_root.exists():
        return
    
    cutoff = now - _PARSED_TTL_HOURS * 3600
    cleaned = 0
    for doc_dir in parsed_root.iterdir():
        if not doc_dir.is_dir():
            continue
        
        # v7.1 M-V7-13: 优先使用 meta.json 的 parsed_at
        meta_path = doc_dir / "meta.json"
        dir_time = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                parsed_at = meta.get("parsed_at", "")
                if parsed_at:
                    dir_time = datetime.fromisoformat(parsed_at).timestamp()
            except Exception:
                pass  # meta.json 损坏，回退到 st_mtime
        
        # 兑底：使用目录修改时间
        if dir_time is None:
            dir_time = doc_dir.stat().st_mtime
        
        if dir_time < cutoff:
            shutil.rmtree(doc_dir, ignore_errors=True)
            cleaned += 1
    
    _last_cleanup_time = now
    if cleaned > 0:
        logger.info(f"TTL cleanup: removed {cleaned} expired parsed directories")
```

#### C-04 修复：AgentState TypedDict 扩展

**问题**：`state.py` 的 `AgentState` TypedDict 未声明 `parse_mode`/`parsed_dir`/`meta`/`chapter_index` 字段，`factory_state` 也未初始化默认值，LangGraph 会静默丢弃未声明字段。

**修复**：扩展 TypedDict + factory_state 默认值：

```python
# state.py 修改

class AgentState(TypedDict):
    # ... 现有字段 ...
    documents: list[dict]
    requirements: dict
    sections: dict[str, str]  # 生成内容（原字段保留）
    
    # C-04 修复（v6）：新增大文件模式字段
    parse_mode: str  # "inline" | "streaming"
    parsed_dir: str  # 解析结果目录路径
    meta: dict       # 文档元信息
    chapter_index: list[dict]  # C-02: 章节索引（非 sections）

# factory_state 修改
def create_factory_state() -> AgentState:
    return {
        # ... 现有字段 ...
        "documents": [],
        "requirements": {},
        "sections": {},
        # C-04: 新增字段默认值
        "parse_mode": "inline",     # 默认 inline 模式
        "parsed_dir": "",           # 默认空
        "meta": {},                 # 默认空
        "chapter_index": [],        # C-02: 默认空列表
    }
```

### 3.9 辅助函数定义（Major 修复：补充缺失函数）

以下函数在上述设计中多次引用但未定义，现统一定义。

#### 3.9.1 _create_parsed_dir

```python
def _create_parsed_dir(file_record: dict) -> Path:
    """为解析结果创建唯一目录.
    
    目录格式：data/parsed/{document_id}/
    document_id = doc_{md5_hash}
    
    M2 修复：统一 document_id 生成策略。
    C-01 修复（v6）：原方案使用 md5(filename + file_path)，但 file_path 是
    tempfile.NamedTemporaryFile 的路径（/tmp/tmpXXXX.pdf），每次上传都不同，
    导致同一文件重复上传生成不同 document_id，断点续传和目录复用完全失效。
    
    修复方案：改用 md5(filename + file_size + 前1MB内容hash)，不依赖临时路径。
    - filename：文件名（用户可识别）
    - file_size：文件大小（快速区分不同文件）
    - 前1MB内容hash：防止同名同大小但内容不同的情况
    
    同一文件重复上传会生成相同的 document_id，复用解析目录，支持断点续传。
    """
    import hashlib
    
    filename = file_record.get("filename", "unknown")
    file_path = file_record.get("path", "")
    file_size = Path(file_path).stat().st_size if Path(file_path).exists() else 0
    
    # C-01: 读取前 1MB 内容做 hash（避免依赖临时路径）
    content_hash = ""
    if Path(file_path).exists():
        with open(file_path, "rb") as f:
            content_hash = hashlib.md5(f.read(1024 * 1024)).hexdigest()[:8]
    
    # C-01: md5(filename + file_size + content_hash) 作为 document_id
    raw = f"{filename}_{file_size}_{content_hash}"
    doc_hash = hashlib.md5(raw.encode()).hexdigest()[:8]
    document_id = f"doc_{doc_hash}"
    
    parsed_dir = Path("data/parsed") / document_id
    parsed_dir.mkdir(parents=True, exist_ok=True)
    
    (parsed_dir / "pages").mkdir(exist_ok=True)
    (parsed_dir / "chapters").mkdir(exist_ok=True)  # C-02: 重命名 sections→chapters
    
    return parsed_dir


def _gen_document_id(file_path: str) -> str:
    """M2: 统一 document_id 生成逻辑，供 _build_meta 使用.
    
    C-01 修复（v6）：与 _create_parsed_dir 保持一致的 hash 策略。
    """
    import hashlib
    filename = Path(file_path).name
    file_size = Path(file_path).stat().st_size
    
    content_hash = ""
    with open(file_path, "rb") as f:
        content_hash = hashlib.md5(f.read(1024 * 1024)).hexdigest()[:8]
    
    raw = f"{filename}_{file_size}_{content_hash}"
    doc_hash = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"doc_{doc_hash}"
```

#### 3.9.2 _build_meta

```python
def _build_meta(
    file_path: str,
    tables: list[dict],
    index_builder: IncrementalIndexBuilder,
    probe_info: dict | None = None,
    scan_page_count: int = 0,
    ocr_page_count: int = 0,
    ocr_failed_pages: list[int] | None = None,
    parse_duration_sec: float = 0.0,
    total_chars: int = 0,
) -> dict:
    """构建文档元信息（meta.json 内容）.
    
    M1 修复：total_chars 由 _stream_parse_pdf 流式累加后传入，
    不再使用索引估算（原估算 sum(len(pages)) * 100 严重不准）。
    """
    from datetime import datetime, timezone
    
    probe = probe_info or {}
    
    scoring_count = sum(1 for t in tables if t.get("type") == "scoring")
    qual_count = sum(1 for t in tables if t.get("type") == "qualification")
    
    return {
        "document_id": _gen_document_id(file_path),
        "filename": Path(file_path).name,
        "file_size_mb": round(Path(file_path).stat().st_size / 1024 / 1024, 1),
        "file_type": "pdf",
        "page_count": probe.get("page_count", 0),
        "total_chars": total_chars,
        "total_tables": len(tables),
        "scoring_tables": scoring_count,
        "qualification_tables": qual_count,
        "scan_page_count": scan_page_count,
        "scan_page_ratio": round(scan_page_count / max(probe.get("page_count", 1), 1), 4),
        "ocr_page_count": ocr_page_count,
        "ocr_page_ratio": round(ocr_page_count / max(probe.get("page_count", 1), 1), 4),
        "ocr_failed_pages": ocr_failed_pages or [],
        "parse_mode": "streaming",
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "parse_duration_sec": round(parse_duration_sec, 1),
    }
```

#### 3.9.3 _clean_table

```python
def _clean_table(rows: list[list]) -> list[list[str]]:
    """清洗 fitz 表格提取结果.
    
    - 将所有单元格转为字符串
    - 去除 None 值
    - 去除完全空的行
    - 去除首尾空白
    """
    cleaned: list[list[str]] = []
    for row in rows:
        clean_row = [
            str(cell).strip() if cell is not None else ""
            for cell in row
        ]
        # 跳过完全空的行
        if any(cell for cell in clean_row):
            cleaned.append(clean_row)
    return cleaned
```

#### 3.9.4 _classify_table

> **v7 遗留-03 修复**：此函数在 spec 中定义是为了展示设计意图。
> 实际实现应直接复用 `bid-agent/core/nodes/doc_parser.py` 中的 `_classify_table`，
> 该实现已包含完整的关键词集合和分类逻辑。以下代码仅作为参考，
> 实现时应 `from core.nodes.doc_parser import _classify_table`。

```python
# 关键词集合（与 doc_parser.py 现有定义对齐）
# v7 遗留-03: 实际使用 doc_parser.py 中的定义，此处仅展示设计参考
_SCORING_KEYWORDS = {"评分", "分值", "得分", "满分", "评分项", "评分标准", "评议"}
_QUALIFICATION_KEYWORDS = {"资质", "资格", "证书", "许可", "认证", "等级"}


def _classify_table(header: list[str]) -> str:
    """根据表头内容分类表格.
    
    Returns:
        "scoring" | "qualification" | "unknown"
    """
    header_text = " ".join(header)
    if any(kw in header_text for kw in _SCORING_KEYWORDS):
        return "scoring"
    if any(kw in header_text for kw in _QUALIFICATION_KEYWORDS):
        return "qualification"
    return "unknown"
```

#### 3.9.5 _extract_company_names

> **v7 遗留-02 修复**：此函数在 spec 中的定义是简化版。
> 实际实现应直接复用 `bid-agent/core/nodes/quality_checker.py` 中的 `_extract_company_names`，
> 该实现比 spec 版本更健壮，包含：
> - 前缀剥离（去除「投标人/供应商/承包商」等前缀）
> - 句子片段过滤（避免跨标点符号的误匹配）
> - 投标人模式匹配（从「投标人：XXX」格式提取）
>
> 实现时应 `from core.nodes.quality_checker import _extract_company_names`，
> 以下代码仅作为设计参考。

```python
import re

# v7 遗留-02: 以下为简化版，实际使用 quality_checker.py 中的完整版
# 公司名称正则模式
_COMPANY_PATTERNS = [
    # XXX有限公司 / XXX有限责任公司
    re.compile(r'([\u4e00-\u9fa5A-Za-z（）()]{2,30}(?:有限公司|有限责任公司|股份有限公司|股份公司))'),
    # XXX集团
    re.compile(r'([\u4e00-\u9fa5A-Za-z]{2,20}集团(?:有限公司|有限责任公司)?)'),
    # P2 新增：合伙企业 / 事务所 / 研究院 / 设计院 / 大学 / 分院
    re.compile(r'([\u4e00-\u9fa5A-Za-z]{2,20}(?:合伙企业|事务所|研究院|设计院|大学|分院|分公司|子公司))'),
]


def _extract_company_names(text: str) -> set[str]:
    """从文本中提取公司名称.
    
    用于 QualityChecker 构建公司名白名单。
    匹配常见中文公司名后缀模式。
    """
    names: set[str] = set()
    for pattern in _COMPANY_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip()
            if len(name) >= 4:  # 过滤过短的误匹配
                names.add(name)
    return names
```

### 3.10 多用户并发控制策略（Major 修复）

**问题**：v3 完全未考虑多用户同时上传大文件的场景。如果 3 个用户同时上传 200MB 文件：
- 内存：3 × 流式解析 = 3 × ~50MB = ~150MB（可控）
- 磁盘 I/O：3 × 2000 页写盘 = 6000 次文件写入（I/O 争抢）
- OCR：3 × 4 进程 = 12 个 OCR 进程（CPU 过载）
- 磁盘空间：3 × 600MB 解析结果 = ~1.8GB（需预检）

**方案：信号量限流 + 队列排队**

```python
import threading

# 全局并发限制
_MAX_CONCURRENT_PARSE = 2     # 最多同时解析 2 个大文件
_MAX_CONCURRENT_OCR = 8       # 最多 8 个 OCR 进程（跨所有用户共享）

_parse_semaphore = threading.Semaphore(_MAX_CONCURRENT_PARSE)
_ocr_semaphore = threading.Semaphore(_MAX_CONCURRENT_OCR)


def _stream_parse_pdf_with_limit(
    file_path: str,
    parsed_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[IncrementalIndexBuilder, list[dict], list[dict], int, dict]:
    """带并发限制的流式解析.
    
    超过 _MAX_CONCURRENT_PARSE 的请求会阻塞等待，
    前端通过 progress_callback 显示排队状态。
    
    C-11: 返回值适配 5 元组（含元信息 dict）。
    """
    # 尝试获取信号量（非阻塞）
    acquired = _parse_semaphore.acquire(blocking=False)
    if not acquired:
        logger.info("大文件解析队列已满，等待中...")
        if progress_callback:
            progress_callback(-1, -1)  # 特殊值：通知前端显示"排队中"
        # P2: 增加超时避免无限阻塞
        acquired = _parse_semaphore.acquire(timeout=300)
        if not acquired:
            raise TimeoutError("大文件解析队列等待超时（300s），请稍后重试")
    
    try:
        return _stream_parse_pdf(file_path, parsed_dir, progress_callback)  # returns 5-tuple
    finally:
        _parse_semaphore.release()
```

**OCR 并发调整**：`_ocr_batch_parallel_safe` 中的 `max_workers` 改为动态计算：

```python
def _calc_ocr_workers(requested: int = 4) -> int:
    """获取 OCR 进程槽位，避免跨用户 CPU 过载.
    
    v7.2 C-V7.1-01 + M-V7.1-01 修复：
    - 消除信号量双重获取（调用方不再调用此函数）
    - 消除 TOCTOU 竞态：直接 acquire requested 个槽位，不预读 _value
    - acquire 是原子操作且线程安全的，多线程同时调用时自动排队
    - 返回值始终等于 requested（信号量不足时阻塞等待）
    
    信号量在 _ocr_batch_parallel_safe 的 finally 中 release。
    """
    for _ in range(requested):
        _ocr_semaphore.acquire()
    return requested
```

**并发限制参数表**：

| 资源 | 限制值 | 策略 |
|------|--------|------|
| 同时解析大文件 | 2 | 信号量排队，超限请求等待 |
| 全局 OCR 进程 | 8 | 跨用户共享，动态分配（v7.1 C-V7-02: _calc_ocr_workers 内 acquire 信号量） |
| 单用户磁盘空间 | 3GB | 预检，不足时拒绝并提示（P2: 原2GB偏紧，200MB PDF解析后pages.blob+索引+OCR缓存约600MB-1GB，2用户+断点续传残留可能不足） |
| TTL 清理频率 | 每次上传前 + 每 6 小时 | 自动清理 >72h 的解析目录（v7 遗留-05: 增加定时清理） |

---

## 四、14 个节点的大文件模式适配分析

| 节点 | 直接依赖 `parsed_content` | 间接依赖（通过 `requirements`） | 大文件模式影响 | 适配优先级 |
|------|-------------------------|------------------------------|---------------|-----------|
| **DocumentParser** | ✅ 生成 | — | **核心改动**（流式解析 + 磁盘存储） | P0 |
| **ReqExtractor** | ✅ 拼接全文给 LLM | — | **需适配**（用倒排索引替代全文拼接） | P0 |
| **ContractExtractor** | ❌ | ✅ | 无需改动 | — |
| **InfoVerificationGate** | ❌ | ✅ | 无需改动 | — |
| **EligibilityChecker** | ❌ | ✅ | 无需改动 | — |
| **TemplateMatcher** | ❌ | ❌ | 无需改动 | — |
| **SectionGenerator** | ❌ | ✅ | **无需改动**（依赖结构化 `requirements`） | — |
| **QualityChecker** | ✅ 提取公司名白名单 | ✅ | **需适配**（用 Reader 提取公司名） | P1 |
| **ComplianceChecker** | ❌ | ✅ | 无需改动 | — |
| **CrossReferenceChecker** | ❌ | ✅ | 无需改动 | — |
| **ScoreSimulator** | ❌ | ❌ | 无需改动 | — |
| **HumanReviewGate** | ❌ | ❌ | 无需改动 | — |
| **FeedbackProcessor** | ❌ | ❌ | 无需改动 | — |
| **DocumentAssembler** | ❌ | ❌ | 无需改动 | — |

### 4.1 QualityChecker 适配

```python
def _build_known_companies(state: AgentState) -> set[str]:
    """从招标文件提取公司名，构建白名单.
    
    P2 优化（v6）：原方案仅从前20页提取公司名，但工程类标书中分包商、
    技术合作方、材料供应商公司名常出现在技术规格书（中后段）和附录。
    改用倒排索引搜索公司名后缀关键词定位页面，再提取公司名。
    """
    known: set[str] = set()
    documents = state.get("documents", [])
    for doc in documents:
        if doc.get("parse_mode") == "inline":
            parsed = doc.get("parsed_content", "")
            if parsed:
                known.update(_extract_company_names(parsed))
        elif doc.get("parse_mode") == "streaming" and doc.get("parsed_dir"):
            reader = ParsedDocumentReader(doc["parsed_dir"])
            # P2: 改用倒排索引搜索公司名后缀关键词
            company_suffixes = ["有限公司", "有限责任公司", "股份有限公司", "集团", "合伙企业", "事务所"]
            # 搜索包含公司名后缀的页面（top 50 页，覆盖全文）
            company_pages = reader.search_pages(company_suffixes, top_k=50)
            for page in company_pages:
                known.update(_extract_company_names(page.text))
            # 兜底：前 20 页也扫描一遍（防止倒排索引遗漏）
            for i in range(1, min(21, reader.meta.get("page_count", 0) + 1)):
                known.update(_extract_company_names(reader.get_page(i)))
    return known
```

### 4.2 关键发现

- **仅 3 个节点直接读取 `parsed_content`**：DocumentParser、ReqExtractor、QualityChecker
- **所有其他节点依赖 `state.requirements`（结构化数据），而非 `parsed_content`**
- **SectionGenerator 无需改动**：它已经依赖结构化 `requirements`

---

## 五、数据流对比

### 5.1 当前数据流（小文件模式）

```
上传 → 全文加载到内存 → 全文存入 AgentState → 全文流经 14 个节点
                                        ↓
                               ReqExtractor 截断到 8000 字符
```

内存峰值：~3-5x 文件大小（原始字符串 + chunks 列表 + State 副本 + deepcopy）

### 5.2 新数据流（大文件模式）

```
上传 → 流式逐页解析 → 逐页写磁盘 + 建倒排索引 → AgentState 只存引用
                                        ↓
                               ReqExtractor 倒排索引定位关键页面 → 合并为 8000 字符
                                        ↓
                               其他节点通过 requirements 间接依赖
```

内存峰值：~1 页文本 + 1 个表格 JSON + LRU 缓存（200 页）≈ 5-200MB

### 5.3 内存对比

| 文件大小 | 当前模式 | 新模式 | 降幅 |
|---------|---------|--------|------|
| 50MB | ~250MB | ~250MB（inline） | 0% |
| 100MB | ~500MB-1GB | ~50MB（streaming） | 90%+ |
| 200MB | ~2-4GB | ~50MB（streaming） | 97%+ |
| 500MB | ~8-16GB | ~50MB（streaming） | 99%+ |

---

## 六、边界情况处理

### 6.1 加密 PDF

```python
if doc.is_encrypted:
    if not doc.authenticate(""):
        return {
            "status": "failed",
            "error": "PDF 已加密，请提供密码或去除密码后重新上传"
        }
```

### 6.2 损坏的 PDF

```python
try:
    doc = fitz.open(file_path)
except Exception as e:
    logger.warning(f"PyMuPDF 打开失败: {e}，尝试 pdfminer 回退")
    # pdfminer 的流式解析接口
```

### 6.3 零文本 PDF（纯图片）

```python
if total_chars == 0 and page_count > 0:
    return {
        "status": "failed",
        "error": "未提取到任何文字内容。该文件可能是纯扫描件，需要 OCR 支持。"
    }
```

### 6.4 磁盘空间不足

```python
import shutil
free_space = shutil.disk_usage(parsed_dir_parent).free
estimated_size = file_size * 3

if free_space < estimated_size:
    raise RuntimeError(
        f"磁盘空间不足: 需要 ~{estimated_size/1024/1024:.0f}MB, "
        f"可用 {free_space/1024/1024:.0f}MB"
    )
```

---

## 七、实施路径

| 阶段 | 内容 | 优先级 | 预计工作量 |
|------|------|--------|----------|
| **Phase 0（v6新增）** | 修复 13 个 Critical 问题（C-01~C-13）+ AgentState TypedDict 扩展 + factory_state 默认值 | P0 | 3-4 天 |
| Phase 1 | 流式解析核心 + 磁盘持久化 + Reader 接口 + C1-C5 修复 | P0 | 4-6 天 |
| Phase 2 | ReqExtractor 适配 + 倒排索引 bigram + search_pages + M8 修复 + NC1 预算分配 | P0 | 2-3 天 |
| Phase 3 | QualityChecker 适配 + Reader 公司名提取（P2: 改用倒排索引替代前20页） | P1 | 1-1.5 天 |
| Phase 4 | OCR 完整实现（M1）+ NC2 预渲染并行 OCR + NC3 两阶段集成 + v7 ProcessPoolExecutor + initializer 预加载 | P1 | 7-9 天（P2: 原估5-7天偏乐观，PaddleOCR环境调试+进程池+子进程超时需额外2-3天） |
| Phase 5 | 两阶段管道跨阶段持久化 + C5 修复 | P1 | 1-2 天 |
| Phase 6 | 进度反馈（M5）+ 断点续传（M6）+ C-12 原子写入 + 边界情况 | P2 | 2-3 天 |
| Phase 7 | 快照序列化优化（C-06）+ 清理策略 + TTL 72h | P2 | 1.5 天 |
| Phase 8 | 章节自动切分（M2/C-10）+ Reader 生命周期（M4）+ 辅助函数 | P2 | 2 天 |
| Phase 9 | 召回率验证（M9）+ 测试策略实现（M10）| P2 | 2-3 天 |
| Phase 10 | 磁盘 I/O 优化 pages.blob（C-07）+ 多用户并发控制 + 倒排索引内存验证 | P3 | 2.5 天 |
| **Phase 11（v6新增）** | 端到端联调 + 性能调优 + 14 节点 streaming 模式协同验证 | — | 3-5 天 |

**总计**：~33-42 天（v6 修订：含 Phase 0 修复 3-4 天 + Phase 11 联调 3-5 天 + Phase 4 增加工程细节 2 天）

---

## 八、与现有架构的对接点

| 现有模块 | 改动类型 | 改动内容 |
|---------|---------|---------|
| `doc_parser.py` — `_load_pdf` | 新增 | `_stream_parse_pdf` 流式版本（含增量索引、断点续传、章节检测、两阶段 OCR） |
| `doc_parser.py` — `_parse_single_doc` | 修改 | 根据文件大小选择 inline/streaming；M7: streaming 模式 raw_text="" |
| `doc_parser.py` — `document_parser` | 修改 | 大文件模式返回引用而非全文；M5: 进度回调 |
| `state.py` — `AgentState` | 新增字段 | `parse_mode`, `parsed_dir`, `meta`, `chapter_index`（C-02: 重命名 sections→chapter_index） |
| `req_extractor.py` | 修改 | 倒排索引 bigram 替代全文拼接；M8: bid_subtype 用前 10 页识别；NC1: 预算分配策略 |
| `quality_checker.py` | 修改 | Reader 提取公司名白名单 |
| `graph.py` — `_serialize_state` | 修改 | 大文件模式跳过全文深拷贝 |
| `upload_panel.py` | 修改 | C4: 分块写盘替代 getvalue()；maxUploadSize 配置 |
| `reset.py` | 修改 | 增加解析结果目录清理 + clear_reader_cache() |
| `app.py` | 修改 | 启动时调用 TTL 清理（72h） |
| `.streamlit/config.toml` | 新增 | `maxUploadSize = 1024` |
| **新增** `parsed_doc_reader.py` | 新文件 | `ParsedDocumentReader` 类 + `get_reader()` 全局缓存 + `_tokenize_chinese()` bigram + `SearchResult` dataclass + `PageBlobReader` 合并文件读取 |
| **新增** `ocr_engine.py` | 新文件 | `_ocr_batch_parallel_safe()` + `_ocr_worker_init()` + `_ocr_worker_task()` + ProcessPoolExecutor 预加载 |
| **新增** `parse_helpers.py` | 新文件 | `_create_parsed_dir` / `_build_meta` / `_clean_table` / `_classify_table` / `_extract_company_names` |
| **新增** `concurrency_control.py` | 新文件 | 信号量限流（`_parse_semaphore` / `_ocr_semaphore`）+ `_calc_ocr_workers()` |

---

## 九、验收标准

### 9.1 功能验收

1. 200MB PDF 能完整解析，不 OOM，不超时
2. 关键信息提取完整率 ≥ 90%（P2: 原95%与bigram理论值90%不一致，统一为90%）
3. GUI 模式下有实时进度条
4. 小文件（≤50MB）行为完全不变
5. 工程类文件扫描页 OCR 识别率 ≥ 85%（P2: CPU模式PaddleOCR处理工程图纸70-85%准确率，95%不现实）

### 9.2 性能验收

| 指标 | 目标 |
|------|------|
| 200MB 纯文本 PDF 解析内存峰值 | ≤ 4GB（含上传阶段） |
| 200MB 纯文本 PDF 解析时间 | ≤ 10 分钟 |
| 200MB 含 10% 扫描页 PDF 解析时间 | ≤ 15 分钟（4 进程 OCR） |
| OCR 预渲染内存峰值（NC4） | ≤ 580MB（50页/批 × 11.6MB/页） |
| 单次检索延迟 | ≤ 100ms（LRU 命中） |
| 快照序列化时间 | ≤ 5 秒（不含全文） |
| 磁盘占用 | ≤ 原文件 3 倍 |
| 上传 500MB 文件内存峰值 | ≤ 20MB（分块写盘） |

### 9.3 健壮性验收

1. 加密 PDF → 友好错误提示
2. 损坏 PDF → 自动回退到 pdfminer
3. 纯扫描 PDF → 自动启用 OCR
4. 磁盘空间不足 → 提前检测并报错
5. 多文件上传 → 每个文件独立解析
6. 750 页扫描页 OCR → 不 OOM（NC4: 分批预渲染，峰值≤580MB）
7. OCR 回写后索引无残留占位 token（NC5: remove_page 清理）
8. 同一文件重复上传 → 复用解析目录（M2: md5 hash document_id）
9. pages.blob 存在时 → get_page 优先使用 blob 读取（M5）
10. pages.blob 不存在时 → get_page 回退到逐页文件读取（M5 向后兼容）

---

## 十、风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 关键词搜索遗漏关键信息 | 低 | 高 | bigram 分词保证召回率 ≥90% + 前 10 页兑底 + 二次检索（M9） |
| 倒排索引构建过慢 | 低 | 中 | 增量构建，逐页同步，无额外开销（C2 修复） |
| OCR 准确率不足 | 中 | 高 | PaddleOCR（中文优化）+ 并行 OCR + OCR 缓存 |
| OCR 耗时过长 | 中 | 高 | 并行 OCR（4 进程）+ 超时保护 + 性能分级目标 |
| LRU 缓存 bug | 已修复 | 低 | C3 已修复：cache hit 时 move_to_end |
| 跨阶段持久化失败 | 低 | 高 | 非临时目录 + TTL 72h + session_state 传递 |
| 向后兼容性破坏 | 低 | 高 | 双模式设计，小文件完全不变 |
| LangGraph 状态传播失败 | 已修复 | 低 | C5 已修复：invoke(state) 而非 build_graph(state) |
| 上传阶段 OOM | 已修复 | 低 | C4 已修复：分块写盘 |
| raw_text 导致内存泄露 | 已修复 | 低 | M7 已修复：streaming 模式下 raw_text="" |
| PaddleOCR 安装冲突 | 中 | 中 | 建议 venv/Docker 隔离 |
| 断点续传失败 | 低 | 中 | M6：progress.json 记录 + OCR 缓存避免重复 |
| bid_subtype 识别错误 | 已修复 | 低 | M8：大文件模式用前 10 页而非 combined[:3000] |
| **搜索结果被截断丢弃** | 已修复 | 高 | NC1 已修复：预算分配策略，每组分配固定字符预算 |
| **fitz Document 多线程 segfault** | 已修复 | 高 | NC2 已修复：预渲染 + 多进程 OCR，fitz 只在主线程访问 |
| **并行 OCR 未集成（死代码）** | 已修复 | 高 | NC3 已修复：两阶段 OCR，流式解析后批量并行执行 |
| **预渲染内存爆炸（750页×11.6MB=8.7GB）** | 已修复 | 高 | NC4 已修复：分批预渲染，每批≤50页，峰值≤580MB |
| **索引 token 残留（占位文本污染）** | 已修复 | 中 | NC5 已修复：OCR 回写前 remove_page 清除旧 token |
| **ParsedDocumentReader 初始化矛盾** | 已修复 | 中 | NC6 已修复：统一为懒加载版本，删除重复定义 |
| **total_chars 估算严重不准** | 已修复 | 中 | M1 已修复：流式累加实际字符数，不用索引估算 |
| **document_id 生成不统一** | 已修复 | 低 | M2 已修复→C-01 v6进一步修复：改用 md5(filename+file_size+内容hash)，不依赖临时路径 |
| **suffix 作用域 bug** | 已修复 | 低 | M3 已修复：从文件名重新提取 |
| **_calc_ocr_workers 未调用** | 已修复 | 中 | M4 已修复：集成到 _ocr_batch_parallel_safe 调用处 |
| **PageBlobReader 未集成到 get_page** | 已修复 | 中 | M5 已修复：get_page 优先使用 blob，回退逐页文件 |
| **C-01: document_id 依赖临时路径** | 已修复 | 高 | v6：改用 md5(filename+file_size+前1MB内容hash)，断点续传不再失效 |
| **C-02: sections 字段类型冲突** | 已修复 | 高 | v6：重命名为 chapter_index，避免与 AgentState 顶层 sections:dict 冲突 |
| **C-03: _calc_ocr_workers 竞态缺陷** | 已修复 | 高 | v6：per-task 信号量限流，acquire 后持有，finally release |
| **C-04: AgentState TypedDict 未更新** | 已修复 | 高 | v6：扩展 TypedDict + factory_state 默认值 |
| **C-05: bigram 英数处理 bug** | 已修复 | 高 | v6：统一 _tokenize_query 切片逻辑，英数整词查询与索引一致 |
| **C-06: _serialize_state deepcopy 未改** | 已修复 | 高 | v6：检测 streaming 模式跳过全文深拷贝 |
| **C-07: PageBlobReader 写入未集成** | 已修复 | 高 | v6：_stream_parse_pdf 统一使用 PageBlobReader.write_page |
| **C-08: PaddleOCR 线程安全** | 已修复 | 高 | v6：线程级实例池 → v7：进程池（ProcessPoolExecutor + initializer） |
| **C-09: _ocr_with_timeout 不可靠** | 已修复 | 高 | v6：multiprocessing.Process → v7：ProcessPoolExecutor（消除每页重新加载模型） |
| **C-10: _detect_chapter_boundary 未定义** | 已修复 | 高 | v6：补全实现 + probe_info 参数 + TOC 优先 + 降级策略 |
| **C-11: _build_meta 调用链断裂** | 已修复 | 高 | v6：_stream_parse_pdf 返回元信息 dict，透传给 _build_meta |
| **C-12: 进度文件非原子写** | 已修复 | 中 | v6：tmp + os.replace 原子替换 + 每50页增量落盘索引 |
| **C-13: remove_page O(N) 性能** | 已修复 | 中 | v6：反向索引 _page_to_tokens，O(K) 替代 O(N) |
| **PaddleOCR API 版本漂移** | 已缓解 | 中 | v6：版本锁定 paddleocr==2.7.0.3 + paddlepaddle==2.6.1 |
| **内网无法下载 OCR 模型** | 已缓解 | 中 | v6：提供 scripts/download_ocr_models.py 离线预置 |

---

## 十一、章节自动切分方案（M2 修复）

### 11.1 问题

存储结构中有 `chapters/` 目录（C-02: 原 sections 重命名），AgentState 有 `chapter_index` 字段，但 v2 完全没有描述如何从 2000 页 PDF 中自动检测章节边界。

### 11.2 方案：TOC 优先 + 正则回退

```python
import re

# 章节标题正则模式（按优先级排序）
_CHAPTER_PATTERNS = [
    # 「第一章 投标须知」/「第二章 项目概况」
    re.compile(r'第[一二三四五六七八九十百千]+章\s*(.{2,30})'),
    # 「第一章 投标须知」的变体（带书名号或括号）
    re.compile(r'第([一二三四五六七八九十百千]+)章[　\s]*[《]?([^《》\n]{2,30})[》]?'),
    # 「一、投标须知」/「二、项目概况」（一级编号）
    re.compile(r'^[一二三四五六七八九十]+[、．.]\s*(.{2,30})', re.MULTILINE),
    # 「1. 投标须知」/「2. 项目概况」（阿拉伯数字编号）
    re.compile(r'^(\d+)[.、]\s*(.{2,30})', re.MULTILINE),
    # 「Section 1: ...」/「PART I ...」（英文格式）
    re.compile(r'(?:Section|Part|CHAPTER)\s*[IVX\d]+[:\.\s]*(.{2,50})', re.IGNORECASE),
]


def _detect_chapter_boundary(page_text: str, page_num: int, probe_info: dict | None = None) -> dict | None:
    """检测页面是否包含章节边界.
    
    M2 设计：
    1. 优先使用 PDF 书签（TOC）— 在 _probe_pdf 阶段已获取（C-10: 通过 probe_info 传入）
    2. 无书签时，使用正则匹配章节标题
    3. 返回章节信息 dict 或 None
    
    C-10 修复（v6）：原方案在 _stream_parse_pdf 中调用但未定义，现补全实现。
    增加 probe_info 参数支持 TOC 优先策略。
    
    降级策略：无书签且正则无匹配时，不切分章节，chapter_index=[]，不影响主流程。
    
    Returns:
        {"name": "第一章 投标须知", "key": "ch01", "page_range": [page_num, None]}
        或 None（本页不是章节起始页）
    """
    # C-10: 优先使用 TOC（书签）
    if probe_info:
        toc = probe_info.get("toc", [])
        for entry in toc:
            # fitz TOC 格式: [level, title, page_num]
            if len(entry) >= 3 and entry[2] == page_num and entry[0] == 1:
                name = entry[1].strip()
                key = f"ch{page_num:04d}"
                return {
                    "name": name,
                    "key": key,
                    "page_range": [page_num, None],
                }
    
    # C-10: 无 TOC 或 TOC 未命中，使用正则匹配
    # 只检查页面前 500 字符（章节标题通常在页面开头）
    head = page_text[:500]
    
    for pattern in _CHAPTER_PATTERNS:
        match = pattern.search(head)
        if match:
            name = match.group(0).strip()
            # 生成章节 key
            key = f"ch{page_num:04d}"
            return {
                "name": name,
                "key": key,
                "page_range": [page_num, None],  # 结束页在下一章节检测时更新
            }
    
    # C-10: 降级 — 不切分章节，返回 None
    return None


def _save_chapter(parsed_dir: Path, chapter: dict, text_parts: list[str]) -> None:
    """将章节文本合并写入文件."""
    chapters_dir = parsed_dir / "chapters"  # C-02: sections→chapters; v7.1 m-V7-02: 变量名同步
    chapters_dir.mkdir(exist_ok=True)
    
    combined = "\n\n".join(text_parts)
    (chapters_dir / f"{chapter['key']}.txt").write_text(combined, encoding="utf-8")


def _build_chapter_index_from_toc(toc: list) -> list[dict]:
    """从 PDF 书签（TOC）构建章节索引.
    
    fitz 的 doc.get_toc() 返回格式：
    [[level, title, page_num], ...]
    
    例如：
    [[1, "第一章 投标须知", 1],
     [2, "1.1 项目概况", 3],
     [1, "第二章 招标范围", 45]]
    
    我们只取 level=1 的条目作为章节边界。
    """
    if not toc:
        return []
    
    chapters = []
    for i, (level, title, page_num) in enumerate(toc):
        if level != 1:
            continue
        # 结束页 = 下一章节起始页 - 1
        end_page = None
        for j in range(i + 1, len(toc)):
            if toc[j][0] == 1:
                end_page = toc[j][2] - 1
                break
        
        chapters.append({
            "name": title,
            "key": f"ch{page_num:04d}",
            "page_range": [page_num, end_page],
        })
    
    return chapters
```

### 11.3 章节切分策略决策

| 条件 | 策略 |
|------|------|
| PDF 有书签（`has_bookmarks=True`） | 使用 `_build_chapter_index_from_toc(toc)` |
| PDF 无书签 | 流式解析时逐页调用 `_detect_chapter_boundary()` |
| 无书签且正则无匹配 | 不切分章节，`chapters/` 目录为空，AgentState 中 `chapter_index=[]` |

---

## 十二、Reader 生命周期和实例化策略（M4 修复）

### 12.1 问题

`ParsedDocumentReader` 在 `req_extractor` 和 `quality_checker` 中各自实例化。每次实例化都要从磁盘加载 3 个 JSON 文件。对于大文档，倒排索引可能有 50,000+ 个 key，反序列化耗时 1-3 秒。

### 12.2 方案：全局缓存 + 懒加载

```python
# parsed_doc_reader.py

_reader_cache: dict[str, ParsedDocumentReader] = {}


def get_reader(parsed_dir: str) -> ParsedDocumentReader:
    """获取 Reader 实例（带全局缓存）.
    
    M4 设计：
    - 同一个 parsed_dir 在整个管道生命周期内只实例化一次
    - 不同节点（ReqExtractor, QualityChecker）共享同一个 Reader
    - Reader 内部的 LRU 缓存和 _tables_cache 也被共享
    
    生命周期：
    - 创建：首次调用 get_reader() 时
    - 复用：后续节点调用 get_reader() 时
    - 清理：reset_pipeline_state() 时调用 clear_reader_cache()
    """
    if parsed_dir not in _reader_cache:
        _reader_cache[parsed_dir] = ParsedDocumentReader(parsed_dir)
        logger.info(f"Created ParsedDocumentReader for {parsed_dir}")
    return _reader_cache[parsed_dir]


def clear_reader_cache() -> None:
    """清空所有缓存的 Reader 实例（在 reset_pipeline_state 时调用）."""
    global _reader_cache
    _reader_cache.clear()
    logger.info("Reader cache cleared")
```

### 12.3 节点适配

```python
# req_extractor.py 中
from core.parsed_doc_reader import get_reader

# 替换原来的直接实例化
reader = get_reader(doc["parsed_dir"])  # 而非 ParsedDocumentReader(doc["parsed_dir"])

# quality_checker.py 中
from core.parsed_doc_reader import get_reader

reader = get_reader(doc["parsed_dir"])  # 复用同一个 Reader 实例
```

### 12.4 ParsedDocumentReader 懒加载优化

> **NC6 修复**：已统一到 §3.4.1 的 `ParsedDocumentReader` 定义中。
> 
> 原 v3 存在两个版本的 `ParsedDocumentReader`：
> - §3.4.1（行 780）：eager 加载 — `__init__` 中一次性读取 4 个 JSON 文件
> - §12.4（此处）：lazy 加载 — `@property` 懒加载
> 
> v4/v5 统一为懒加载版本（NC6 修复），删除此处的重复定义，以 §3.4.1 为准。

---

## 十三、进度反馈机制（M5 修复）

### 13.1 问题

Streamlit 是同步框架，长时间解析会阻塞 UI。成功标准要求「解析过程有进度反馈」，但 v2 没有任何设计。

### 13.2 方案：进度回调 + Streamlit progress bar

```python
# document_parser 节点适配（大文件模式）

def document_parser(state: AgentState) -> dict:
    documents = state.get("documents", [])
    
    for doc in documents:
        if doc.get("parse_mode") == "streaming":
            # M5: 在 GUI 模式下使用 Streamlit 进度条
            import streamlit as st
            
            progress_bar = st.progress(0.0, text=f"正在解析 {doc['filename']}...")
            status_text = st.empty()
            
            def progress_callback(page_num: int, total_pages: int):
                # v7 NC-05 修复：检查特殊值 -1,-1（排队等待）
                if page_num == -1 and total_pages == -1:
                    progress_bar.progress(0.0, text="排队等待中...前方有其他文件正在解析")
                    return
                
                ratio = page_num / total_pages
                progress_bar.progress(ratio, text=f"解析中... 第 {page_num}/{total_pages} 页")
                if page_num % 100 == 0:
                    status_text.text(f"已完成 {page_num}/{total_pages} 页 ({ratio:.0%})")
            
            # 执行流式解析（带进度回调）
            # v7 NC-04 修复：_stream_parse_pdf 返回 5 元组（含 meta dict）
            index_builder, tables, chapter_index, total_chars, meta = _stream_parse_pdf(
                file_path=doc["path"],
                parsed_dir=parsed_dir,
                progress_callback=progress_callback,
            )
            
            progress_bar.progress(1.0, text="解析完成！")
            status_text.empty()
```

### 13.3 headless 模式（无 GUI）

```python
# headless 模式下，progress_callback 为 None，不影响解析逻辑
# v7 NC-04: 同样使用 5 元组解包
index_builder, tables, chapter_index, total_chars, meta = _stream_parse_pdf(
    file_path=file_path,
    parsed_dir=parsed_dir,
    progress_callback=None,  # headless 模式不需要进度反馈
)
```

### 13.4 进度信息内容

| 阶段 | 进度信息 |
|------|---------|
| 0-10% | 文件预检（页数、加密检测） |
| 10-90% | 逐页解析（页码/总页数） |
| 90-95% | 倒排索引写入 + 表格写入 |
| 95-100% | 章节切分 + 元信息生成 |

---

## 十四、召回率量化与验证（M9 修复）

### 14.1 量化方法

```python
def _measure_search_recall(
    reader: ParsedDocumentReader,
    test_keywords: list[str],
    ground_truth_pages: list[int],
) -> float:
    """量化搜索召回率.
    
    M9 设计：
    - 对每组关键词，用 search_pages 搜索
    - 与人工标注的 ground truth 页码对比
    - 返回召回率 = 命中的 ground truth 页数 / 总 ground truth 页数
    
    用于验收标准 #4：关键信息提取完整率 ≥ 90%
    """
    results = reader.search_pages(test_keywords, top_k=50)
    found_pages = {r.page_num for r in results}
    gt_set = set(ground_truth_pages)
    
    hit_count = len(found_pages & gt_set)
    recall = hit_count / len(gt_set) if gt_set else 1.0
    
    return recall
```

### 14.2 测试数据集

| 类别 | 测试关键词 | 预期页面特征 |
|------|----------|------------|
| 评分标准 | `["评分", "分值", "得分", "评标"]` | 含评分表的页面 |
| 资质要求 | `["资质", "资格", "证书", "许可"]` | 含资质要求的页面 |
| 技术规格 | `["技术参数", "规格", "技术要求"]` | 含技术规格的页面 |
| 格式要求 | `["格式", "密封", "装订", "份数"]` | 含格式要求的页面 |
| 项目信息 | `["项目名称", "招标编号", "招标人"]` | 前 10 页 |

### 14.3 二次检索兜底机制

已在 `search_pages` 中实现（见 §3.4.1）：
- 倒排索引命中 < 3 页时，自动线性扫描前 100 页
- 对扫描的页面做 `keyword in text` 子串匹配
- 兜底机制保证即使倒排索引失效，仍能找到关键信息

---

## 十五、测试策略（M10 修复）

### 15.1 测试架构

```
tests/
├── unit/
│   ├── test_tokenizer.py          # C1: bigram 分词器测试
│   ├── test_incremental_index.py  # C2: 增量索引构建测试
│   ├── test_lru_cache.py          # C3: LRU 缓存测试
│   ├── test_chunked_upload.py     # C4: 分块上传测试
│   ├── test_ocr_page.py           # M1: OCR 单页测试（mock PaddleOCR）
│   ├── test_chapter_detection.py  # M2: 章节切分测试
│   ├── test_reader_lifecycle.py   # M4: Reader 生命周期测试
│   ├── test_budget_allocation.py  # NC1: 预算分配策略测试
│   ├── test_ocr_process_safety.py  # NC2: 预渲染并行 OCR 进程安全测试
│   ├── test_sample_ratio.py       # Major: 均匀采样测试
│   ├── test_page_blob.py          # Major: pages.blob 合并文件测试
│   └── test_concurrency.py        # Major: 并发限流测试
├── integration/
│   ├── test_stream_parse.py       # 流式解析集成测试
│   ├── test_req_extractor_large.py # ReqExtractor 大文件模式集成测试
│   ├── test_cross_phase.py        # 跨阶段持久化测试
│   ├── test_checkpoint_resume.py  # M6: 断点续传测试
│   └── test_two_phase_ocr.py      # NC3: 两阶段 OCR 集成测试
└── contract/
    └── test_large_file_contract.py # 端到端契约测试
```

### 15.2 关键测试用例

#### C1: bigram 分词器测试

```python
def test_bigram_tokenizer():
    # 基本分词
    tokens = _tokenize_chinese("评分标准")
    assert "评分" in tokens
    assert "分标" in tokens
    assert "标准" in tokens
    
    # 混合中英文
    tokens = _tokenize_chinese("ISO9001质量管理体系认证")
    assert "ISO9" not in tokens  # 不混切中英文
    assert "质量" in tokens
    assert "体系" in tokens
    
    # 短文本
    tokens = _tokenize_chinese("评分")
    assert "评分" in tokens
```

#### C3: LRU 缓存测试

```python
def test_lru_cache_hit_updates_order():
    reader = ParsedDocumentReader("/tmp/test_parsed")
    reader._cache_max_size = 3
    
    # 填满缓存
    for i in [1, 2, 3]:
        reader._page_cache[i] = f"page_{i}"
    
    # 访问 page 1（应移到末尾）
    reader.get_page = lambda x: reader._page_cache[x]
    reader.get_page(1)
    
    # 添加 page 4（应淘汰 page 2，而非 page 1）
    reader._page_cache[4] = "page_4"
    reader._evict_if_needed()
    
    assert 1 in reader._page_cache  # page 1 仍在（因为刚访问过）
    assert 2 not in reader._page_cache  # page 2 被淘汰
```

#### M6: 断点续传测试

```python
def test_checkpoint_resume(tmp_path):
    # 模拟解析到第 500 页中断
    progress_file = tmp_path / "progress.json"
    progress_file.write_text(json.dumps({
        "last_completed_page": 500,
        "total_pages": 2000,
    }))
    
    # 重新解析时，应从第 501 页开始
    # （需要 mock fitz.open 返回一个 2000 页的 doc）
    start_page = _read_progress(tmp_path)
    assert start_page == 500
```

#### NC1: 预算分配测试

```python
def test_budget_allocation_no_truncation():
    """NC1: 验证 _assemble_relevant_content 不会截断搜索结果."""
    # 构造 10 页 × 3000 字符的 first_pages（共 30,000 字符，远超 8000 预算）
    first_pages = "项目名称：测试项目\n" + "x" * 29900
    
    scoring = [SearchResult(page_num=100, text="评分标准" + "y" * 2000, hit_count=5)]
    qual = [SearchResult(page_num=200, text="资质要求" + "z" * 2000, hit_count=4)]
    tech = [SearchResult(page_num=300, text="技术参数" + "w" * 2000, hit_count=3)]
    fmt = [SearchResult(page_num=400, text="格式要求" + "v" * 2000, hit_count=2)]
    
    combined = _assemble_relevant_content(
        first_pages=first_pages,
        scoring_pages=scoring,
        qual_pages=qual,
        tech_pages=tech,
        format_pages=fmt,
        max_chars=8000,
    )
    
    # 验证 1: 总长度不超过 max_chars
    assert len(combined) <= 8000
    
    # 验证 2: 包含所有 5 类内容（没有被截断丢弃）
    assert "项目名称" in combined          # project_info
    assert "评分标准" in combined          # scoring
    assert "资质要求" in combined          # qualification
    assert "技术参数" in combined          # tech_spec
    assert "格式要求" in combined          # format
```

#### NC2: 进程安全测试

```python
def test_ocr_batch_parallel_no_segfault(tmp_path):
    """NC2: 验证预渲染 + 多进程 OCR 不触发 segfault.
    
    v7.1 M-V7-11: 原 v7 测试 mock `_get_ocr_engine`（v7 已删除该函数），
    且 patch 在子进程中不生效。重构为 mock ProcessPoolExecutor 本身，
    在同进程内模拟 OCR 结果。
    """
    import numpy as np
    from unittest.mock import MagicMock, patch
    
    # mock fitz Document（模拟 10 页扫描件）
    mock_doc = MagicMock()
    mock_doc.__len__ = lambda self: 10
    mock_page = MagicMock()
    mock_pixmap = MagicMock()
    mock_pixmap.height = 100
    mock_pixmap.width = 100
    mock_pixmap.n = 3
    mock_pixmap.samples = b'\x00' * (100 * 100 * 3)
    mock_page.get_pixmap.return_value = mock_pixmap
    mock_doc.__getitem__ = lambda self, idx: mock_page
    
    # v7.1 M-V7-11: mock ProcessPoolExecutor 本身，避免子进程复杂度
    # 模拟 pool.submit 返回的 future 直接包含结果
    def mock_submit(fn, *args, **kwargs):
        future = MagicMock()
        # 模拟 OCR 结果
        page_idx = args[0] if args else kwargs.get('page_idx', 0)
        future.result.return_value = (page_idx, "测试文本")
        return future
    
    mock_pool = MagicMock()
    mock_pool.submit = mock_submit
    
    # v7.2 M-V7.1-04: 同时 mock as_completed，因为 MagicMock future 不兼容
    mock_futures = []
    
    def mock_submit_and_track(fn, *args, **kwargs):
        f = mock_submit(fn, *args, **kwargs)
        mock_futures.append(f)
        return f
    
    mock_pool.submit = mock_submit_and_track
    
    with patch('core.large_file.ProcessPoolExecutor', return_value=mock_pool), \
         patch('core.large_file._calc_ocr_workers', return_value=2), \
         patch('core.large_file._ocr_worker_init'), \
         patch('core.large_file.PaddleOCR'), \
         patch('core.large_file.as_completed', side_effect=lambda fs: list(fs)):
        
        results = _ocr_batch_parallel_safe(
            doc=mock_doc,
            scan_page_indices=list(range(10)),
            parsed_dir=tmp_path,
            max_workers=2,
        )
    
    # 验证：所有 10 页都返回了结果，没有 segfault
    assert len(results) == 10
    assert all(v for v in results.values())  # 所有页都有 OCR 文本
```

#### NC3: 两阶段 OCR 集成测试

```python
def test_two_phase_ocr_integration(tmp_path):
    """NC3: 验证流式解析后自动触发批量 OCR."""
    from unittest.mock import patch, MagicMock
    
    # 构造 mock parsed_dir（模拟部分页面有占位文本）
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True)
    
    # 前 3 页有正常文本
    for i in range(1, 4):
        (pages_dir / f"page_{i:04d}.txt").write_text(f"第{i}页正常文本内容")
    
    # 第 4-6 页是扫描页占位
    for i in range(4, 7):
        (pages_dir / f"page_{i:04d}.txt").write_text(f"[扫描页 — 待 OCR: 第 {i} 页]")
    
    # mock _ocr_batch_parallel_safe 返回 OCR 结果
    # v7.2 m-V7.1-03: 修正 patch 路径
    with patch('core.large_file._ocr_batch_parallel_safe') as mock_ocr:
        mock_ocr.return_value = {
            3: "第4页OCR文本",
            4: "第5页OCR文本",
            5: "第6页OCR文本",
        }
        # 执行流式解析（mock fitz）
        # ... (mock setup omitted for brevity)
        
        # 验证 _ocr_batch_parallel_safe 被调用
        assert mock_ocr.called
        
        # 验证 OCR 结果回写到 page 文件
        assert "OCR文本" in (pages_dir / "page_0004.txt").read_text()
        assert "扫描页 — 待 OCR" not in (pages_dir / "page_0004.txt").read_text()
```

### 15.3 测试数据生成

由于没有真实的 200MB PDF，使用以下策略生成测试数据：

```python
def generate_large_pdf_mock(tmp_path, page_count=2000, chars_per_page=3000):
    """生成模拟大文件解析结果目录（不创建真实 PDF）."""
    parsed_dir = tmp_path / "parsed" / "doc_test"
    pages_dir = parsed_dir / "pages"
    pages_dir.mkdir(parents=True)
    
    for i in range(1, page_count + 1):
        # 每 100 页包含一次「评分标准」
        if i % 100 == 0:
            text = f"第{i}页 评分标准 分值分配 得分计算..."
        else:
            text = f"第{i}页 普通内容文本..."
        (pages_dir / f"page_{i:04d}.txt").write_text(text)
    
    # 生成倒排索引
    index = {"评分": [100, 200, 300], "分值": [100, 200], "得分": [100]}
    (parsed_dir / "inverted_index.json").write_text(json.dumps(index))
    
    # 生成 meta
    (parsed_dir / "meta.json").write_text(json.dumps({
        "page_count": page_count,
        "total_chars": page_count * chars_per_page,
    }))
    
    return parsed_dir
```

### 15.4 验收测试清单

| 测试项 | 测试方法 | 通过标准 |
|--------|---------|---------|
| C1 倒排索引召回 | 用测试关键词搜索，对比标注页面 | 召回率 ≥ 90% |
| C2 增量索引 | 解析 100 页后检查内存 | 内存 < 50MB |
| C3 LRU 正确性 | 填满缓存后访问旧页面再添加新页面 | 淘汰的是最久未访问的 |
| C4 上传内存 | 上传 100MB mock 文件 | 峰值内存 < 20MB |
| C5 API 调用 | 调用 build_generation_graph + invoke | 不抛参数错误 |
| M1 OCR 单页 | mock PaddleOCR，调用 _ocr_worker_task | 返回文本字符串 |
| M2 章节检测 | 给定含「第一章」的页面文本 | 正确返回章节信息 |
| M6 断点续传 | 写入 progress.json 后重新解析 | 从断点页继续 |
| M9 召回率 | 5 类关键词搜索测试 | 平均召回率 ≥ 90% |
| 端到端 | 200MB mock 文件完整流程 | 不 OOM，不超时 |
| **NC1 预算分配** | 构造 10 页 × 3000 字符 + 搜索结果，调用 _assemble_relevant_content | combined ≤ 8000 字符且含所有 5 类内容 |
| **NC2 进程安全** | mock fitz + PaddleOCR，4 进程并行 OCR 10 页 | 无 segfault，所有进程正常完成 |
| **NC3 两阶段集成** | mock 2000 页（20 页扫描），流式解析 + 批量 OCR | 扫描页 OCR 结果回写 blob + 索引更新 |
| **采样均匀性** | 构造前 1990 页有文本 + 后 10 页扫描的 mock | text_ratio < 0.1（非 0.0） |
| **并发限流** | 同时提交 3 个解析请求 | 第 3 个排队等待，前 2 个正常完成 |
| **pages.blob** | 生成 2000 页 blob + 偏移量索引，随机读取 100 页 | 读取内容与写入一致 |

### 15.5 性能与回归测试（v7 遗留-06 补充）

```python
# tests/performance/test_memory_peak.py
"""大文件解析内存峰值测试（v7 遗留-06）."""

def test_streaming_parse_memory_under_100mb():
    """流式解析 2000 页 mock PDF，内存峰值应 < 100MB.
    
    验证点：
    - 流式解析不一次性加载全部页面文本
    - PageBlobReader 写入后释放页面文本引用
    - IncrementalIndexBuilder 内存增长可控
    """
    import tracemalloc
    tracemalloc.start()
    
    parsed_dir = generate_large_pdf_mock(tmp_path, page_count=2000)
    
    # 模拟流式解析（mock fitz）
    blob_reader = PageBlobReader(parsed_dir)
    index_builder = IncrementalIndexBuilder()  # v7.1 m-V7-06: dataclass 无构造参数
    
    for i in range(1, 2001):
        text = f"第{i}页 测试内容文本评分标准..."
        blob_reader.write_page(i, text)
        index_builder.add_page(i, text)
        
        if i % 500 == 0:
            current, peak = tracemalloc.get_traced_memory()
            assert peak < 100 * 1024 * 1024, f"Memory peak {peak/1MB:.1f}MB at page {i}, expected < 100MB"
    
    tracemalloc.stop()


def test_ocr_process_pool_model_loading_once():
    """验证 ProcessPoolExecutor initializer 只加载模型一次（v7 NC-01 回归）.
    
    验证点：
    - _ocr_worker_init 只被调用 max_workers 次（不是每页一次）
    - OCR 100 页的模型加载时间应 < 60s（4 worker × 10-15s）
    
    v7.1 M-V7-12: 原 v7 测试使用 nonlocal call_count，但子进程不共享父进程
    内存（spawn 启动方式），导致计数器始终为 0。改用 multiprocessing.Value
    跨进程共享计数器。macOS 默认 spawn，Linux 默认 fork（fork 时子进程
    继承父进程内存快照，nonlocal 可能看到值，但 spawn 不行）。
    """
    import time
    from multiprocessing import Value, ProcessPoolExecutor
    from concurrent.futures import as_completed
    
    # v7.1 M-V7-12: 使用 multiprocessing.Value 跨进程共享计数器
    init_count = Value('i', 0)  # ctypes int, 初始值 0
    
    def mock_init():
        with init_count.get_lock():
            init_count.value += 1
        # 模拟模型加载耗时
        time.sleep(0.1)
    
    def dummy_task():
        return "ok"
    
    start = time.time()
    # 使用 'spawn' 启动方式确保测试一致性（macOS 默认即为 spawn）
    ctx = multiprocessing.get_context('spawn')
    with ctx.ProcessPoolExecutor(max_workers=4, initializer=mock_init) as pool:
        futures = [pool.submit(dummy_task) for _ in range(100)]
        for f in as_completed(futures):
            f.result()
    elapsed = time.time() - start
    
    # initializer 应只调用 4 次（max_workers），不是 100 次
    assert init_count.value == 4, f"Expected 4 init calls, got {init_count.value}"
    assert elapsed < 2.0, f"100 tasks took {elapsed:.1f}s, expected < 2s"


# tests/regression/test_small_file_unchanged.py
"""小文件模式回归测试（v7 遗留-06）."""

def test_small_file_uses_inline_mode():
    """小于 50MB 的文件应走 inline 模式，不受大文件策略影响."""
    state = factory_state()
    state["documents"] = [{
        "path": "/tmp/small.pdf",
        "filename": "small.pdf",
        "size": 30 * 1024 * 1024,  # 30MB
    }]
    
    result = document_parser(state)
    
    # 验证使用 inline 模式
    assert result["parse_mode"] == "inline"
    assert result["parsed_dir"] == ""  # 不创建 parsed_dir
    # 验证 parsed_content 正常填充
    assert result["documents"][0].get("parsed_content")
    assert result["documents"][0].get("parse_mode") == "inline"  # v7.1 m-V7-07: 原断言 .get("streaming") 无效


def test_small_file_no_blob_created():
    """小文件不应创建 pages.blob 文件."""
    state = factory_state()
    state["documents"] = [{
        "path": "/tmp/small.pdf",
        "filename": "small.pdf",
        "size": 10 * 1024 * 1024,  # 10MB
    }]
    
    result = document_parser(state)
    
    assert "parsed_dir" not in result or not result["parsed_dir"]
    assert not Path("data/parsed").exists() or not any(Path("data/parsed").iterdir())


# tests/performance/test_concurrent_users.py
"""多用户并发测试（v7 遗留-06）."""

def test_three_concurrent_large_files():
    """3 个用户同时上传 200MB 文件，验证并发限流."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    results = []
    errors = []
    
    def upload_and_parse(user_id: int):
        try:
            state = factory_state()
            state["documents"] = [{
                "path": f"/tmp/test_{user_id}.pdf",
                "filename": f"test_{user_id}.pdf",
                "size": 200 * 1024 * 1024,
            }]
            result = document_parser(state)
            results.append((user_id, result["parse_mode"]))
        except Exception as e:
            errors.append((user_id, str(e)))
    
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(upload_and_parse, i) for i in range(3)]
        for f in as_completed(futures):
            f.result()  # 触发异常
    
    # 验证：至少 2 个成功，第 3 个可能排队等待或超时
    assert len(results) >= 2, f"Expected ≥2 successes, got {len(results)}"
    assert len(errors) <= 1, f"Expected ≤1 errors, got {len(errors)}"
```