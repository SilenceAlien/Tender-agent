# 大文件处理方案 v7.1 二次审查报告

**审查日期**: 2026-07-12  
**审查版本**: v7.1 → v7.2（已修复）  
**审查范围**: `specs/009-large-file-strategy/spec.md`（3315 行）  
**审查方法**: 逐行代码审查 + 跨章节一致性检查 + 修复副作用追踪  

> **v7.2 修复状态**: 全部 10 个问题（2C + 5M + 3m）已修复并验证通过。方案达到可实施状态。

---

## 审查结论

| 维度 | 评分 | 说明 |
|------|------|------|
| v7 修复验证 | ✅ 全部通过 | C-V7-01/02/03 + 13 Major + 8 Minor 均已正确修复 |
| 新引入问题 | 2 Critical + 5 Major + 3 Minor | 修复过程中引入的新问题 |
| 综合评分 | **7.5/10** | v7 修复到位，但引入了信号量双重获取和日志配置缺失两个 Critical |

**建议**: 修复 2 个 Critical 后可进入 Phase 1 实现。

---

## 一、v7 修复验证（全部通过）

| 问题 ID | 描述 | 状态 | 验证位置 |
|---------|------|------|---------|
| C-V7-01 | 缩进断裂 | ✅ 已修复 | L421, L504, L585 — 3 处缩进正确 |
| C-V7-02 | 信号量仅读取不获取 | ✅ 已修复 | L2278-2284 — acquire 已添加 |
| C-V7-03 | shutdown 无法终止卡死进程 | ✅ 已修复 | L1714-1721 — terminate + kill |
| M-V7-01 | 返回类型标注 4→5 元组 | ✅ 已修复 | L398, L401 |
| M-V7-02~03 | 线程池→进程池 | ✅ 已修复 | 架构图 L158, 标题 L1545 |
| M-V7-04 | 重复内存分析表 | ✅ 已修复 | 仅保留一份 |
| M-V7-05 | logger vs print | ⚠️ 声明已修但代码未落实 | 见 C-V7.1-02 |
| M-V7-06~10 | 术语线程→进程 | ⚠️ 部分残留 | 见 M-V7.1-03 |
| M-V7-11 | mock 不存在的函数 | ✅ 已修复 | L3047-3050 |
| M-V7-12 | 跨进程 nonlocal | ✅ 已修复 | L3206 — multiprocessing.Value |
| M-V7-13 | 清理函数重复 | ✅ 已修复 | L1909-1921 |
| m-V7-01~08 | 各类小修 | ✅ 全部修复 | 逐项验证通过 |

---

## 二、新发现问题

### Critical（2 个）

#### C-V7.1-01: 信号量双重获取导致限流泄漏

**位置**: L572（调用方）+ L1627（函数内部）

**问题**:

调用方 `_stream_parse_pdf`（L567-573）:
```python
ocr_results = _ocr_batch_parallel_safe(
    doc=doc,
    scan_page_indices=scan_page_indices,
    parsed_dir=parsed_dir,
    max_workers=_calc_ocr_workers(requested=4),  # 第一次 acquire: 获取 N 个槽位
)
```

函数内部 `_ocr_batch_parallel_safe`（L1626-1627）:
```python
actual_workers = _calc_ocr_workers(requested=max_workers)  # 第二次 acquire: 又获取 N 个
```

finally 块（L1723-1725）:
```python
for _ in range(actual_workers):
    _ocr_semaphore.release()  # 只释放 N 个
```

**影响**:
- 每次 OCR 获取 2N 槽位，释放 N 个，净泄漏 N 个/次
- `_MAX_CONCURRENT_OCR = 8`，`requested=4` → 2 次后信号量耗尽
- 后续所有 OCR 操作永久阻塞

**根因**: C-V7-02 修复在 `_calc_ocr_workers` 内添加了 acquire，但未删除调用方已有的调用。

**修复**: 删除调用方的 `_calc_ocr_workers`，直接传 `max_workers=4`:

```python
# L572 修改为:
max_workers=4,  # _ocr_batch_parallel_safe 内部调用 _calc_ocr_workers
```

---

#### C-V7.1-02: `_ocr_worker_init` 未配置 basicConfig，子进程日志丢失

**位置**: L1438-1468

**问题**:

M-V7-05 声明"统一为 logger + basicConfig"，`_ocr_worker_task` docstring（L1484）也写"_ocr_worker_init 中已配置 basicConfig"，但实际代码中**没有** `basicConfig` 调用。

```python
def _ocr_worker_init():
    global _worker_ocr_engine
    try:
        from paddleocr import PaddleOCR
        _worker_ocr_engine = PaddleOCR(...)
        logger.info(...)  # logger 未配置，输出丢失
```

**影响**:
- `spawn` 启动方式下子进程不继承父进程 logger 配置
- 所有 worker 日志静默丢失，调试困难

**修复**: 在 `_ocr_worker_init` 开头添加:

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [PID=%(process)d] %(levelname)s %(message)s',
)
```

---

### Major（5 个）

#### M-V7.1-01: `_calc_ocr_workers` TOCTOU 竞态

**位置**: L2278-2284

**问题**: 读取 `_value` 和 `acquire` 之间存在窗口，其他线程可能在此期间获取所有槽位，导致 `acquire` 阻塞无超时。

**修复**: 直接 acquire `requested` 个，不预读 `_value`:

```python
def _calc_ocr_workers(requested: int = 4) -> int:
    for _ in range(requested):
        _ocr_semaphore.acquire()
    return requested
```

---

#### M-V7.1-02: 日志使用 `max_workers` 而非 `actual_workers`

**位置**: L1678

```python
f"Batch {batch_idx + 1}: 并行 OCR {len(pre_rendered)} 页, {max_workers} workers"
```

`max_workers` 是原始参数（可能为 4），`actual_workers` 是经信号量限制后的实际值。应使用 `actual_workers`。

---

#### M-V7.1-03: 残留"线程"术语未更新为"进程"（5 处）

| 行号 | 当前文本 | 应改为 |
|------|---------|--------|
| L571 | `动态计算 OCR 线程数` | `OCR 进程数` |
| L2215 | `3 × 4 线程 = 12 个 OCR 线程` | `3 × 4 进程 = 12 个 OCR 进程` |
| L2225 | `最多 8 个 OCR 线程` | `最多 8 个 OCR 进程` |
| L2541 | `预渲染 + 多线程 OCR` | `预渲染 + 多进程 OCR` |
| L2899 | `test_ocr_thread_safety.py # 线程安全测试` | `test_ocr_process_safety.py # 进程安全测试` |

---

#### M-V7.1-04: `test_ocr_batch_parallel_no_segfault` 未 mock `as_completed`

**位置**: L3012-3061

**问题**: mock `ProcessPoolExecutor` 后，`pool.submit()` 返回 `MagicMock` 对象作为 future。但 `as_completed(futures)`（L1690）未被 mock，它期望真正的 `Future` 对象，传入 `MagicMock` 会导致不可预期的行为或报错。

**修复**: 同时 mock `as_completed`:

```python
with patch('core.large_file.ProcessPoolExecutor', return_value=mock_pool), \
     patch('core.large_file._calc_ocr_workers', return_value=2), \
     patch('core.large_file._ocr_worker_init'), \
     patch('core.large_file.PaddleOCR'), \
     patch('core.large_file.as_completed') as mock_as_completed:
    
    mock_as_completed.return_value = list(futures.keys())
    # ...
```

---

#### M-V7.1-05: 验收标准召回率不一致

| 位置 | 当前值 | 应改为 |
|------|--------|--------|
| L103 (§1.3 成功标准) | ≥ 95% | ≥ 90% |
| L2490 (§9.1 功能验收) | ≥ 90% | ✅ 正确 |
| L2853 (§14.1 召回率测试) | ≥ 95% | ≥ 90% |

v6 P2 优化已统一为 90%（bigram 理论上限），但 §1.3 和 §14.1 遗漏未更新。

---

### Minor（3 个）

#### m-V7.1-01: `TimeoutError` 在 Python 3.8-3.10 可能不匹配

**位置**: L1696

`future.result(timeout=...)` 抛出 `concurrent.futures.TimeoutError`，而代码 `except TimeoutError` 捕获内置 `TimeoutError`。Python 3.11+ 两者合并，但 3.8-3.10 不合并。

**修复**:
```python
from concurrent.futures import TimeoutError as FutureTimeoutError
except FutureTimeoutError:
```

---

#### m-V7.1-02: `_ocr_worker_init` 内存估算未覆盖最大场景

**位置**: L1448

注释写"4 个 worker = ~6GB"，但 `_MAX_CONCURRENT_OCR = 8`，极端情况下 8 个 worker = ~12GB。

---

#### m-V7.1-03: `test_two_phase_ocr_integration` patch 路径错误

**位置**: L3084

```python
with patch('__main__._ocr_batch_parallel_safe') as mock_ocr:
```

`__main__` 在测试环境中通常不包含此函数，应改为实际模块路径如 `core.large_file._ocr_batch_parallel_safe`。

---

## 三、修复优先级

| 优先级 | 问题数 | 必须在 Phase 1 前修复 | 可在实现中修复 |
|--------|--------|---------------------|---------------|
| Critical | 2 | ✅ C-V7.1-01, C-V7.1-02 | — |
| Major | 5 | — | ✅ 全部 |
| Minor | 3 | — | ✅ 全部 |

---

## 四、总结

v7.1 成功修复了 v7 审查报告中的全部 24 个问题，但在 C-V7-02（信号量 acquire）修复中引入了双重获取的 Critical 缺陷，且 M-V7-05（basicConfig）修复声明未落实到代码。建议修复 2 个 Critical 后升级为 v7.2，即可进入 Phase 1 代码实现。
