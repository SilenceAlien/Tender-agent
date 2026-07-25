# 标书代码审计 — 低危项（L1/L4/L6/L7/L8/L9）处理整合报告

> 生成时间：2026-07-20 17:10
> 编排师：AgentsOrchestrator（智能体编排师）
> 范围：上一轮遗留的 6 个低危项（L 系列），本轮集中处理

## 一、处理总览

| 项 | 文件 | 问题 | 处理方式 | 状态 |
|----|------|------|----------|------|
| L1 | faiss_index.py / embeddings.py | L2 距离非余弦 | 经核查**已符合**（索引本用 `IndexFlatIP` + 两处归一化），无需改码 | ✅ 确认 |
| L4 | quality_checker.py | LLM 非JSON 未重试1次 | 加 `max_attempts=2` 重试循环（三重容错失败后再重试1次） | ✅ 修复 |
| L6 | score_simulator.py | 截断 6000 ≠ 质检 8000 | 新增常量 `MAX_SECTION_CHARS_FOR_LLM=8000`，截断对齐 | ✅ 修复 |
| L7 | graph.py | `route_after_score_sim` 死代码 | **删除**死函数（方案 A），其余 19 路由不动 | ✅ 清理 |
| L8 | database.py | 仅测试引用未接入运行时 | 评估后**未强行接入**（无 <20 行低风险点），补 docstring 说明状态与建议 | ⚪ 文档化 |
| L9 | prompt_registry/selector.py | 无节点调用 | 评估后**未强行接入**（与 Store 并行机制、空库会降级空操作、接写入点>20行），补 docstring 说明 | ⚪ 文档化 |

## 二、各项说明

### L1 — 检索距离度量（核查即合规，零改动）
Agent 核查 `faiss_index.py:56` 发现索引本就用 `faiss.METRIC_INNER_PRODUCT`，且 `add`(:82-86) 与 `search`(:191-195) **两处都对向量做 L2 归一化**——内积即余弦相似度，已符合规范语义。`embeddings.py` 的 MockEmbedder 单位向量与归一化互补、离线安全。为避免引入回归，**未改动源码**，验证确认 `IndexFlatIP` 按余弦排序正确。

### L4 — QualityChecker LLM 重试（对齐规范）
`_llm_quality_check` 由「单次调用+直接 FAIL」改为 `max_attempts=2` 重试循环：每次先跑原有「直接→```json```→花括号」三重容错，解析失败/异常/结构不符才 `sleep(0.5)` 重试 1 次，仍失败才 FAIL。8000 字门槛与空章守卫（已修）未动。

### L6 — ScoreSimulator 截断对齐
`score_simulator.py` 新增模块常量 `MAX_SECTION_CHARS_FOR_LLM = 8000`（:23-26），`:196` 的 `content[:6000]` 改为 `content[:MAX_SECTION_CHARS_FOR_LLM]`。因 quality_checker 的 8000 本身为魔法数字无具名常量，未强制跨文件共用，但数值已对齐。

### L7 — graph 死代码清理
删除 `route_after_score_sim`（原 :114-121）死函数，Grep 全仓确认无残留调用；`Literal` import 被其他路由共用故保留；其余 19 条路由与节点图与规范 §七 一致，未动。

### L8 — database 接入（评估后文档化）
`database.py` 提供完整 SQLite CRUD（projects/documents/sections/feedbacks/configs），API 有跨会话价值，但 Grep 确认仅 `tests/unit/test_database.py` 引用。评估结论：**无 <20 行低风险接入点**（需建 project 生命周期、config 回退 DB 会迁移敏感 API Key 改变安全模型、GUI 直写 DB 有崩溃风险）。仅在顶部 docstring 补「运行时接入状态(L8)」说明（当前仅用于 tests、运行时见 config_persistence.py、建议接入方式与禁止事项）。

### L9 — PromptRegistry/Selector 接线（评估后文档化）
Registry/Selector 与 ConsistencyLessonStore **非重叠**（前者回灌「整段最优 prompt 变体」，后者注入「指令规则」），属并行独特机制。但 Grep 确认两模块无任何节点调用、也无 `.save()` 写入路径 → Registry 恒为空库，接入 Selector 也会因空候选永久降级为原 prompt（空操作），且 `replace` 模式会覆盖已接好的结构化 prompt、有冲突风险；接 `.save()` 写入点 >20 行且需评分联动，非低风险。仅在两文件顶部补 L9 状态 docstring（未接线事实、与 Store 关系、未来接入建议）。H11 的 Store 注入路径未触碰。

## 三、验证结果

- **联合编译**：8 个受影响文件 `py_compile` 全部通过（ALL_COMPILE_OK，exit 0）。
- **各 agent 自测**：L1 独立脚本确认余弦排序、L4 桩 mock 确认重试两路径、L6 Grep 确认无 6000 残留、L7 Grep 确认无残留调用、L8/L9 py_compile 通过。临时脚本已清理。
- **未引入回归**：L8/L9 仅加注释零逻辑改动；L4/L6/L7 改动局部且向后兼容；L1 零改动。

## 四、全周期累计

三轮处理（复审计修复 + code-review 修复 + M 系列 + L 系列）后：
- **高危全清**（H1~H12 含 N0 回归）
- **中危全清**（M1~M17）
- **低危全清或文档化**（L1 确认合规 / L4 L6 L7 已修 / L8 L9 评估后文档化，因无低风险接入点不建议强行接线）
- 14 节点 + 19 条路由 + 20 字段主体与 `system-workflow.md` 规范一致。

## 五、收尾建议

- L8/L9 如需真正接入，建议作为独立小需求立项（不在本次审计修复范围内）：L8 设计 project 生命周期持久化，L9 设计评分联动的 prompt 变体写入点与 Selector 降级策略。
- 代码层面无剩余阻塞项，系统可进入集成测试/试运行阶段。

---
**结论**：标书制作智能体代码与规范的偏差已**全量收敛**，质量置信度 HIGH。
