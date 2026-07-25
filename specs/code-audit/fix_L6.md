# 修复记录 — L6（低危）：ScoreSimulator 截断长度与 QualityChecker 不一致

- 修复日期：2026-07-20
- 审计依据：`specs/code-audit/cluster_C4_v2.md` §1 L6、`system-workflow.md`（节点 ⑧ 完整度门槛 8000 字）
- 修改文件：`bid-agent/core/nodes/score_simulator.py`（仅此一处；`quality_checker.py` 为只读基准，未改）

## 改动明细

| 行 | 旧值 | 新值 |
|----|------|------|
| 23-26（新增） | 无 | 模块级常量 `MAX_SECTION_CHARS_FOR_LLM = 8000`，注释对齐 quality_checker.py:475 `content[:8000]` 与 :581 默认 `min_section_chars=8000` |
| 196 → 201 | `f"【{name}】\n{content[:6000]}"` | `f"【{name}】\n{content[:MAX_SECTION_CHARS_FOR_LLM]}"`（6000 → 8000） |

## 说明

- `quality_checker.py` 当前 8000 亦为魔法数字（无具名常量），且任务要求该文件只读，故无法 import 复用同一常量。
- 在 `score_simulator.py` 内定义 `MAX_SECTION_CHARS_FOR_LLM = 8000` 常量取代原 `6000` 字面量，数值与 quality_checker 门槛对齐，消除本节点魔法数字；评分逻辑其余部分未改动。
- 评分逻辑（启发式 / LLM 解析 / 输出结构）保持不变。

## 验证

- 编译：`python3 -m py_compile core/nodes/score_simulator.py` → `COMPILE_OK`（无语法错误）
- Grep 确认：截断处已为 `content[:MAX_SECTION_CHARS_FOR_LLM]`，常量值为 `8000`，无残留 `6000`。
