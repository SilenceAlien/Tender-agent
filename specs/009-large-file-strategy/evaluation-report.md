# 超大招标文件读取策略 — 综合评估报告

> **评估日期**：2026-07-12
> **评估方式**：7 个专业 agent 并行评估，覆盖方案全部 9 大章节
> **评估范围**：只做评估，不做执行
> **方案版本**：v5 修订版

---

## 一、评分总览

| # | 评估模块 | 负责章节 | 评分 | 核心结论 |
|---|---------|---------|------|---------|
| 1 | 上传与预检 | §3.1 + §3.7 | **7/10** | 核心思路正确，但 document_id 生成 bug 瓦解断点续传 |
| 2 | 流式解析与倒排索引 | §3.2 | **6/10** | bigram 英数处理致命 bug + 2 个核心函数未定义 |
| 3 | 磁盘持久化 | §3.3 | **6/10** | PageBlobReader 写入路径未集成 + _serialize_state 未改 |
| 4 | 按需检索与 ReqExtractor | §3.4 + §3.5 | **7.5/10** | 设计最扎实，LRU/预算分配/C5 修复均正确 |
| 5 | OCR 集成 | §3.6 | **6.5/10** | 架构合理，但 PaddleOCR 线程安全 + 超时机制有缺陷 |
| 6 | 并发控制与节点适配 | §3.10 + §四 + §五 | **6/10** | _calc_ocr_workers 竞态 bug 导致限流失效 |
| 7 | 辅助函数与实施路径 | §3.8-3.9 + §六-九 | **7/10** | 函数完整但调用链断裂，验收标准目标值不一致 |

**综合评分：6.6/10** — 架构方向正确，但存在 13 个 Critical 级硬伤需在实施前修复。

---

## 二、Critical 级问题汇总（必须在实施前修复）

### C-01：document_id 生成 bug — 断点续传完全失效 🔴
**发现者**：Agent 1, 2, 7（3 个 agent 独立发现）

`_create_parsed_dir` 用 `md5(filename + file_path)` 生成 ID，但 `file_path` 是 `tempfile.NamedTemporaryFile` 的路径（`/tmp/tmpXXXX.pdf`），每次上传都不同。

**影响**：同一文件重复上传生成不同 ID，断点续传和目录复用完全失效。
**修复**：改用 `md5(filename + file_size + 前1MB内容hash)`，不依赖临时路径。

---

### C-02：`sections` 字段类型冲突 — 破坏 11 个下游节点 🔴
**发现者**：Agent 1, 2, 3（3 个 agent 独立发现）

`state.py:141` 已定义 `sections: dict[str, str]`（章节名→生成内容），spec §3.3.4 又用 `sections: list[dict]`（章节索引），类型冲突。

**影响**：LangGraph reducer 行为不可预测，可能破坏 SectionGenerator 等 11 个下游节点。
**修复**：document record 内改用 `chapter_index` 字段名，避免与 AgentState 顶层 `sections` 冲突。

---

### C-03：`_calc_ocr_workers` 竞态缺陷 — 限流实际无效 🔴
**发现者**：Agent 2, 6（2 个 agent 独立发现）

函数"试探性 acquire 全部、再 release"来计算可用数，但返回前已释放所有槽位。计算到的 `available` 仅是瞬时快照，函数返回后其他线程可再次抢占。

**影响**：OCR 并发限制形同虚设，多用户场景 CPU 过载。
**修复**：改为 acquire 后持有，在 OCR 完成的 finally 中 release。

---

### C-04：AgentState TypedDict 未更新 — 状态字段被丢弃 🔴
**发现者**：Agent 1, 3, 4, 6, 7（5 个 agent 独立发现）

`state.py` 的 `AgentState` TypedDict 未声明 `parse_mode`/`parsed_dir`/`meta` 字段，`factory_state` 也未初始化默认值。

**影响**：LangGraph 静默丢弃未声明字段，下游 `doc.get("parse_mode")` 返回 None 而非 "inline"。
**修复**：扩展 AgentState TypedDict + factory_state 初始化默认值。

---

### C-05：bigram 分词器英数处理 bug — 英数搜索 100% 失败 🔴
**发现者**：Agent 2

`_tokenize_chinese` 对英文/数字用整词提取（`[a-zA-Z]{2,}|[0-9]{2,}`），而 `_tokenize_query` 对整个查询串做 bigram。搜索"ISO9001"→查询 bigram `["IS","SO","O9","90","00","01"]`，索引中却是 `"ISO"`、`"9001"`。

**影响**：所有英文/数字关键词搜索失败（如 ISO9001、GB/T 19001 等资质编号）。
**修复**：`_tokenize_query` 改为先按 `[\u4e00-\u9fa5]+|[a-zA-Z]{2,}|[0-9]{2,}` 切片，中文做 bigram，英数整词查询。

---

### C-06：`_serialize_state` deepcopy 未修改 — 快照导出 OOM 🔴
**发现者**：Agent 3, 6

`graph.py:588` 仍是无条件 `copy.deepcopy(snapshot)`，spec 声称"大文件模式跳过全文深拷贝"但代码中完全未实现。

**影响**：快照导出时仍会 OOM。
**修复**：检测 `documents[].parse_mode == "streaming"` 时，跳过 `parsed_content`/`raw_text`/`chunks` 字段的 deepcopy。

---

### C-07：PageBlobReader 写入路径未集成 🔴
**发现者**：Agent 3

`_stream_parse_pdf` 第 417-418 行仍用 `page_file.write_text()`，未调用 `PageBlobReader.write_page`。Reader 仅在读取侧被引用，写入与读取路径不对称。

**影响**：pages.blob 永远不会生成，PageBlobReader 形同虚设。
**修复**：在 `_stream_parse_pdf` 中统一使用 `PageBlobReader.write_page` 替代 `page_file.write_text`。

---

### C-08：PaddleOCR 单例线程安全问题 🔴
**发现者**：Agent 5

4 个线程同时调用 `_ocr_engine_instance.ocr()`（单例），PaddleOCR 的 Python 封装层并非保证线程安全。

**影响**：并行 OCR 可能出错或 segfault。
**修复**：为每个 worker 创建独立 PaddleOCR 实例（池大小 = max_workers），或加 `threading.Lock` 保护。

---

### C-09：`_ocr_with_timeout` 超时机制不可靠 🔴
**发现者**：Agent 5

`threading.Thread + join(timeout)` + `daemon=True` 是已知有缺陷的模式。PaddleOCR 推理在 C 扩展中阻塞时，Python 无法中断线程。超时后线程继续运行，持有 `img_array` 引用。

**影响**：750 页若有 5% 超时 = 37 个泄漏线程，长期运行内存泄漏。
**修复**：改用 `multiprocessing.Process` + `terminate()`，或进程级超时。

---

### C-10：`_detect_chapter_boundary` 和 `_save_chapter` 未定义 🔴
**发现者**：Agent 2

§3.2.1 第 427/431/452 行调用，§3.9 声称"补充缺失函数"但实际未定义。

**影响**：流式解析无法运行。
**修复**：补全实现，建议用正则 `r'^第[一二三四五六七八九十百\d]+章\s*\S+'` + 章节切分失败降级。

---

### C-11：`_build_meta` 调用链断裂 🔴
**发现者**：Agent 7

`_build_meta` 签名要求 `probe_info/scan_page_count/ocr_page_count/ocr_failed_pages/parse_duration_sec`，但 `_parse_single_doc` 调用时未传这些参数。

**影响**：meta.json 缺失关键字段（page_count=0、scan_page_count=0）。
**修复**：在 `_stream_parse_pdf` 返回值中增加 probe_info/scan_page_count 等并透传给 `_build_meta`。

---

### C-12：进度文件非原子写 — 崩溃损坏 JSON 🟡
**发现者**：Agent 2

`progress_file.write_text` 非原子写，崩溃中途中写会损坏 JSON。`inverted_index.json` 仅在末尾一次写，崩溃丢失全部索引。

**修复**：write_text 到 `.tmp` 再 `os.replace` 原子替换；inverted_index.json 每 N 页增量落盘。

---

### C-13：`remove_page` 性能问题 — O(N) 遍历全部 token 🟡
**发现者**：Agent 2, 5

`remove_page` 遍历全部 token（50K-80K 个），750 扫描页 = 60M 次操作。

**修复**：维护反向索引 `_page_to_tokens: dict[int, set[str]]`，O(1) 定位该页 token。

---

## 三、各模块详细评估

### 模块 1：上传与预检（§3.1 + §3.7）— 7/10

**可行点**：
- 分块写盘方案正确，`uploaded_file.read(chunk_size)` 是标准用法
- 均匀采样逻辑正确，修复了 v3 的前 10 页偏差
- 50MB 阈值估算合理

**风险点**：
- 50MB 阈值只看文件大小，忽略内容密度（40MB 纯扫描 PDF 走 inline 静默失败）
- `_probe_pdf` 与 `_stream_parse_pdf` 重复打开同一 PDF 两次
- 临时文件 `delete=False` 但解析失败时无人清理
- 非 PDF 大文件无 streaming 路径

---

### 模块 2：流式解析与倒排索引（§3.2）— 6/10

**可行点**：
- C2 增量构建索引有效，内存中只保留当前页文本+索引结构
- bigram 分词器对纯中文场景召回率 ≥90% 成立
- NC5 修复（remove_page → add_page）逻辑顺序正确

**风险点**：
- bigram 英数处理致命 bug（C-05）
- `fitz.Document` 在整个流式解析+OCR 期间保持打开（数十分钟），可能内存泄漏
- 进度文件每页同步写 I/O，2000 页 = 2000 次 fsync
- `inverted_index.json` 仅末尾一次写，崩溃丢失全部索引
- 内存估算偏乐观 30-50%（Python dict/list/int 对象开销大）

---

### 模块 3：磁盘持久化（§3.3）— 6/10

**可行点**：
- pages.blob + page_offsets.json 合并存储方案原理正确
- seek+read 实现 O(1) 随机访问
- M7 修复（raw_text=""）方向正确

**风险点**：
- PageBlobReader 写入路径未集成（C-07）
- _serialize_state deepcopy 未修改（C-06）
- PageBlobReader.write_page 存在 TOCTOU 竞态（stat().st_size 与 write 之间无锁）
- OCR 回写采用追加模式，占位文本残留在 blob 中
- page_offsets.json 持久化时机不明，_stream_parse_pdf 中从未调用 flush_offsets

---

### 模块 4：按需检索与 ReqExtractor（§3.4 + §3.5）— 7.5/10

**可行点**：
- NC6 懒加载设计标准正确
- C3 LRU 修复（move_to_end）有效
- NC1 预算分配逻辑清晰，总计 = MAX_INPUT_CHARS = 8000
- C5 修复与现有代码 build_generation_graph 签名完全一致

**风险点**：
- LRU 50 页偏小（search_pages top_k=20 一次就可能填满）
- per_page_limit=800 可能截断评分表关键信息
- 二次检索只扫描前 100 页，工程标书关键页常在文档后半
- seen_pages 去重会丢弃同页不同段的信息
- 缺少 session_state 丢失后的恢复机制

---

### 模块 5：OCR 集成（§3.6）— 6.5/10

**可行点**：
- NC2 预渲染+多线程 OCR 架构思路正确
- NC4 分批 50 页控制内存峰值方向正确
- NC3 两阶段 OCR 集成点合理
- PaddleOCR 单例避免重复加载模型

**风险点**：
- PaddleOCR 单例线程安全问题（C-08）
- _ocr_with_timeout 超时机制不可靠（C-09）
- 内存估算偏乐观（未计 PaddleOCR 内部张量，实际可能 ~1.8GB/批）
- PaddleOCR API 变更（use_gpu 在 3.x 已更名为 device）
- 首次下载模型 ~200MB 需联网（政府/企业内网可能无外网）
- _ocr_page（§3.6.1）与 _ocr_batch_parallel_safe（§3.6.2）关系不明，前者可能是死代码

---

### 模块 6：并发控制与节点适配（§3.10 + §四 + §五）— 6/10

**可行点**：
- 14 节点影响矩阵基本准确（3 个节点需适配）
- 内存对比数据方向正确
- 信号量限流参数选取合理

**风险点**：
- _calc_ocr_workers 竞态缺陷（C-03）
- 全局信号量在多进程部署（gunicorn 多 worker）中无效
- QualityChecker 前 20 页提取公司名不足（工程标书公司名常在中后段）
- 内存对比漏算 PaddleOCR 模型常驻内存（CPU 模式 ~1.5GB）
- ContractExtractor 是否依赖 parsed_content 未验证

---

### 模块 7：辅助函数与实施路径（§3.8-3.9 + §六-九）— 7/10

**可行点**：
- 5 个辅助函数基本完整
- 实施路径优先级排序合理
- 新增 4 文件职责划分清晰

**风险点**：
- _build_meta 调用链断裂（C-11）
- _classify_table 分类粒度偏粗（仅 scoring/qualification）
- _extract_company_names 遗漏"合伙企业/事务所/研究院/设计院"等实体
- 边界情况覆盖不足（超大单页、AcroForm 表单、非 UTF-8 编码等）
- 实施路径 22-30 天偏乐观（未含端到端联调 3-5 天，实际 30-40 天）
- 验收标准目标值不一致（§1.3 要求 95%，§3.2.2 自评 90%，§14 目标 90%）

---

## 四、改进建议优先级排序

### P0 — 阻塞实施的硬伤（必须在 Phase 1 前修复）

| # | 问题 | 修复方案 | 工作量 |
|---|------|---------|--------|
| C-01 | document_id 生成 bug | 改用 md5(filename + file_size + 内容hash) | 0.5 天 |
| C-02 | sections 字段类型冲突 | 重命名为 chapter_index | 0.5 天 |
| C-04 | AgentState TypedDict 未更新 | 扩展 TypedDict + factory_state | 0.5 天 |
| C-05 | bigram 英数处理 bug | 统一 _tokenize_query 切片逻辑 | 0.5 天 |
| C-07 | PageBlobReader 写入未集成 | _stream_parse_pdf 中统一使用 | 0.5 天 |
| C-10 | _detect_chapter_boundary 未定义 | 补全实现 + 降级策略 | 1 天 |
| C-11 | _build_meta 调用链断裂 | 透传 probe_info 等参数 | 0.5 天 |

### P1 — 实施中必须解决

| # | 问题 | 修复方案 | 工作量 |
|---|------|---------|--------|
| C-03 | _calc_ocr_workers 竞态 | acquire 后持有，finally release | 0.5 天 |
| C-06 | _serialize_state 未改 | 检测 streaming 模式跳过 deepcopy | 0.5 天 |
| C-08 | PaddleOCR 线程安全 | 实例池或加锁 | 1 天 |
| C-09 | _ocr_with_timeout 不可靠 | 改用 multiprocessing.Process | 1 天 |
| C-12 | 进度文件非原子写 | tmp + os.replace | 0.5 天 |
| C-13 | remove_page O(N) 性能 | 反向索引 dict[int, set[str]] | 0.5 天 |

### P2 — 优化项

- LRU 容量提升到 100-200 页
- per_page_limit 按类别差异化（scoring/qual 1200，format 600）
- 二次检索改为按章节索引扫描
- QualityChecker 公司名提取改用倒排索引
- 磁盘预检提升到 3GB
- 验收标准统一召回率目标为 90%
- PaddleOCR 版本锁定 + 离线模型预置

---

## 五、实施路径修订建议

原方案 10 个 Phase、22-30 天。建议修订为：

| Phase | 内容 | 优先级 | 修订工作量 |
|-------|------|--------|----------|
| **Phase 0（新增）** | 修复 C-01~C-13 阻塞性问题 | P0 | **3-4 天** |
| Phase 1 | 流式解析核心 + 磁盘持久化 + Reader 接口 | P0 | 5-7 天（原 4-6） |
| Phase 2 | ReqExtractor 适配 + 倒排索引 + 预算分配 | P0 | 2-3 天 |
| Phase 3 | QualityChecker 适配 | P1 | 1-1.5 天 |
| Phase 4 | OCR 完整实现 | P1 | 7-9 天（原 5-7） |
| Phase 5 | 跨阶段持久化 | P1 | 1-2 天 |
| Phase 6 | 进度反馈 + 断点续传 + 边界情况 | P2 | 2-3 天 |
| Phase 7 | 快照序列化 + 清理 + TTL | P2 | 1.5 天 |
| Phase 8 | 章节切分 + 辅助函数 | P2 | 2 天 |
| Phase 9 | 召回率验证 + 测试 | P2 | 2-3 天 |
| Phase 10 | I/O 优化 + 并发控制 | P3 | 2.5 天 |
| **Phase 11（新增）** | 端到端联调 + 性能调优 | — | **3-5 天** |

**修订后总计**：~33-42 天（原 22-30 天）

---

## 六、总体结论

### 优点
- **架构方向正确**：流式解析 + 倒排索引 + 磁盘持久化 + 按需检索的核心思路是处理大文件的标准方案
- **v5 修复有效**：C1-C5、NC1-NC6 的修复方向均正确，逐步完善了方案
- **向后兼容**：双模式设计（inline/streaming）保护小文件行为不变
- **影响分析到位**：14 节点矩阵准确识别了仅 3 个节点需适配

### 不足
- **13 个 Critical 级硬伤**：其中 7 个为阻塞性问题（P0），必须在实施前修复
- **跨模块一致性问题**：多个 agent 独立发现的 document_id bug、sections 冲突、AgentState 未更新说明方案在模块间对接上有系统性缺陷
- **工程细节不足**：PaddleOCR 线程安全、超时机制、原子写入等工程实践缺失
- **工作量偏乐观**：未含阻塞性修复（3-4 天）和端到端联调（3-5 天）

### 建议
1. **先修复 Phase 0 的 7 个 P0 问题**，再进入正式实施
2. **统一验收标准**：召回率目标统一为 90%（与 bigram 理论值一致）
3. **PaddleOCR 环境单独验证**：在实施前先验证 PaddleOCR 安装、线程安全、API 版本兼容性
4. **增加端到端联调 Phase**：确保 14 节点在 streaming 模式下协同工作

---

*报告由 7 个并行评估 agent 生成，AgentOrchestrator 汇总*
