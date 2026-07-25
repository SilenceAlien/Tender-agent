# 修复记录 L4 — QualityChecker LLM 调用重试缺失

- 修复日期：2026-07-20
- 修复文件：`bid-agent/core/nodes/quality_checker.py`
- 规范依据：`system-workflow.md:340`（三重 JSON 容错 → 重试 1 次 → FAIL）
- 审计证据：`specs/code-audit/cluster_C4_v2.md` L4 段落

## 问题
`_llm_quality_check` 仅单次 `llm_fn(prompt)`，非 JSON 直接 FAIL，与规范「重试 1 次」不符。

## 改法（仅改本文件）
1. 函数顶部新增 `import time`（行 `import json/re` 后）。
2. 更新函数 docstring，说明 L4 重试语义。
3. 将原来「单次调用 + 直接 FAIL」逻辑替换为重试循环：
   - `max_attempts = 2`（首次 + 重试 1 次）。
   - 每次 attempt：调用 `llm_fn(prompt)` → **保留原有三重 JSON 容错**（直接 → ```json``` → 花括号）→ `json.loads`。
   - 解析失败 / 异常 / JSON 结构不符预期（非 dict 或缺 `verdict`）→ 记 warning，`time.sleep(0.5)` 后重试；**三重容错在每次 attempt 内都先执行**，失败才进重试。
   - 重试仍失败 → 返回 `verdict=FAIL` 兜底 dict（沿用 Phase B1 非 JSON 不再静默 PASS 的语义）。
4. 未触碰：8000 字完整度门槛（N3/H2）与空章守卫（code-review）逻辑，保持不动。

## 关键行
- `_llm_quality_check` 函数体内 LLM 调用与解析段整体改写（原 `response = llm_fn(prompt)` 至原 FAIL 返回块）。

## 验证
- `py_compile` 无语法错误。
- 桩 mock 测试（测完已删）：
  - 首次非 JSON → 重试 1 次 → 第二次正常：调用次数=2，verdict=PASS ✅
  - 两次均失败：调用次数=2，verdict=FAIL ✅

## 结论
L4 已修复。符合规范「三重容错 → 重试 1 次 → FAIL」。
