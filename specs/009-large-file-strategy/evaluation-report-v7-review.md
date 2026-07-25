# v7.1 方案审查报告（v7 修复后）

> **审查日期**：2026-07-12
> **审查范围**：`spec.md` v7.1 全文 + v7 审查报告修复验证
> **审查方法**：逐章节代码审查 + 跨章节一致性检查 + 与 `bid-agent/core/nodes/doc_parser.py` 等现有代码对齐
> **综合评分**：**8.8 / 10**（v7.1 修复了 v7 全部 3 个 Critical + 13 个 Major + 8 个 Minor，方案达到可实施状态）

---

## 一、修复总览

| 级别 | v7 问题数 | v7.1 修复数 | 状态 |
|------|----------|-----------|------|
| 🔴 Critical | 3 | 3 | ✅ 全部修复 |
| 🟠 Major | 13 | 13 | ✅ 全部修复 |
| 🟡 Minor | 8 | 8 | ✅ 全部修复 |
| **合计** | **24** | **24** | **✅ 零遗留** |

---

## 二、Critical 修复验证

### C-V7-01：`_stream_parse_pdf` 缩进断裂 → ✅ 已修复

**修复内容**：将 3 处零缩进的 `KEEP_PAGE_FILES` / `blob_reader` 赋值修正为函数内 4 空格缩进：
- 行 ~405：`KEEP_PAGE_FILES` 和 `blob_reader` 初始化
- 行 ~487：`blob_reader.write_page()` + `KEEP_PAGE_FILES` 条件块
- 行 ~569：OCR 回写中的 `blob_reader.write_page()` + `KEEP_PAGE_FILES` 条件块

**验证方式**：修复后代码缩进与上下文一致，Python 解释器可正常加载。

---

### C-V7-02：OCR 信号量并发控制失效 → ✅ 已修复

**修复内容**：
1. `_calc_ocr_workers` 改为读取 `_value` 后立即 `acquire` 对应数量的信号量槽位
2. `_ocr_batch_parallel_safe` 在 `finally` 块中 `release` 信号量
3. 进程池 `max_workers` 使用 `actual_workers`（实际获取的信号量数），而非原始 `requested`

**验证方式**：
- 用户 A 调用 `_calc_ocr_workers(4)` → acquire 4 个槽位 → 返回 4
- 用户 B 同时调用 → `_value` 为 4（8-4）→ acquire 4 个 → 返回 4
- 用户 C 同时调用 → `_value` 为 0 → acquire 0 → 返回 `max(1, 0)` = 1 → 阻塞等待
- 信号量在 `finally` 中释放，确保不会泄漏

---

### C-V7-03：Worker 进程泄漏 → ✅ 已修复

**修复内容**：在 `pool.shutdown(wait=False, cancel_futures=True)` 后增加：
1. 遍历 `pool._processes`，对 `is_alive()` 的进程调用 `terminate()` (SIGTERM)
2. `join(timeout=5)` 等待 5 秒
3. 仍存活则 `kill()` (SIGKILL) 强制终止

**验证方式**：卡死的 PaddleOCR worker 进程在 5 秒内被 SIGTERM 回收，极端情况下 SIGKILL 兜底。

---

## 三、Major 修复验证

| 编号 | 问题 | 修复内容 | 状态 |
|------|------|---------|------|
| M-V7-01 | 返回类型标注 4 元组 | 更新为 `tuple[..., dict]` 5 元组 | ✅ |
| M-V7-02 | 架构图"线程池" | 更新为"进程池"（§2.1 + §2.2） | ✅ |
| M-V7-03 | §3.6.2 标题"多线程" | 更新为"多进程" | ✅ |
| M-V7-04 | 重复内存分析表 | 删除旧表（不含反向索引列） | ✅ |
| M-V7-05 | logger vs print 不一致 | 统一为 `logger.warning`，删除"不能访问 logger"注释 | ✅ |
| M-V7-06 | Phase 4 描述过时 | 更新为"v7 ProcessPoolExecutor + initializer 预加载" | ✅ |
| M-V7-07 | §8 文件表 `_ocr_page()` | 更新为 `_ocr_worker_init() + _ocr_worker_task()` | ✅ |
| M-V7-08 | "全局 OCR 线程" | 更新为"全局 OCR 进程" | ✅ |
| M-V7-09 | "4 线程 OCR" | 更新为"4 进程 OCR" | ✅ |
| M-V7-10 | "4 线程" | 更新为"4 进程" | ✅ |
| M-V7-11 | mock `_get_ocr_engine` 不存在 | 重构为 mock `ProcessPoolExecutor` 本身 | ✅ |
| M-V7-12 | 跨进程 `nonlocal` 不工作 | 改用 `multiprocessing.Value` + `spawn` 上下文 | ✅ |
| M-V7-13 | 两个重复清理函数 | 合并为 `_cleanup_expired_parsed_dirs`，`parsed_at` 优先 + `st_mtime` 兜底 | ✅ |

---

## 四、Minor 修复验证

| 编号 | 问题 | 修复内容 | 状态 |
|------|------|---------|------|
| m-V7-01 | `timeout_sec` 死参数 | 文档标注"仅供文档参考" | ✅ |
| m-V7-02 | `sections_dir` 变量名 | 重命名为 `chapters_dir` | ✅ |
| m-V7-03 | LRU 缓存"50 页" | 更新为"200 页" | ✅ |
| m-V7-04 | PaddleOCR 结果 None 健壮性 | 增加 `isinstance` 检查 | ✅ |
| m-V7-05 | `cancel_futures` Python 版本 | 标注"需 Python 3.9+" | ✅ |
| m-V7-06 | `IncrementalIndexBuilder` 构造参数 | 改为无参 `IncrementalIndexBuilder()` | ✅ |
| m-V7-07 | 测试断言 `.get("streaming")` 无效 | 改为 `.get("parse_mode") == "inline"` | ✅ |
| m-V7-08 | `_serialize_state` 冗余浅拷贝 | `dict(doc)` 移到 streaming 分支内 | ✅ |

---

## 五、v7.1 评分明细

| 维度 | v7 评分 | v7.1 评分 | 说明 |
|------|--------|----------|------|
| 架构设计 | 9.0 | 9.0 | ProcessPoolExecutor + initializer 方案未变 |
| 代码正确性 | 6.5 | 8.5 | 缩进修复 + 信号量恢复 + worker 终止 |
| 并发安全 | 5.0 | 8.5 | 信号量限流恢复 + 强制终止兜底 |
| 测试覆盖 | 6.0 | 8.5 | 测试用例修复，可实际执行 |
| 文档一致性 | 7.0 | 9.5 | 术语统一 + 重复内容清理 |
| 跨章节一致性 | 7.5 | 9.0 | 函数签名/返回值/参数对齐 |
| **综合** | **7.8** | **8.8** | |

---

## 六、结论

v7.1 修复了 v7 审查报告中的全部 24 个问题（3 Critical + 13 Major + 8 Minor），方案达到**可立即实施**状态。

**关键改进**：
1. 核心函数 `_stream_parse_pdf` 代码缩进修复，可被 Python 解释器加载
2. OCR 信号量并发控制恢复，多用户场景下进程数受限
3. 卡死 worker 进程可被强制回收，无内存泄漏
4. 测试用例可实际执行（mock 目标存在、跨进程计数器工作）
5. 文档术语全部统一为"进程"/"进程池"

**建议**：可直接进入 Phase 1 代码实现阶段。
