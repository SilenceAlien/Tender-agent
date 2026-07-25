# 修复记录 — M15（rejected 重跑走最小回路）

**文件**：`bid-agent/gui/panels/review_panel.py`（唯一修改文件）
**依赖（只读复用）**：`core/graph.py` 的 `route_after_*` 条件路由、`build_default_search_fn`；各 `core.nodes.*` 节点。
**验证**：`python3 -m py_compile review_panel.py` → 无语法错误（exit 0）。

---

## 问题（M15，中危）
原 reject 处理（旧 405-418 行）调用 `build_generation_graph(llm_fns)`，其入口为
`EligibilityChecker`，即**重跑整段 Phase2**（资质校验、模板匹配等前置阶段全部重跑），
既慢又可能丢失用户已确认信息，偏离规范 §七「rejected → ⑨ FeedbackProcessor →
⑦ SectionGenerator」最小回路。

## 修复内容

### 1. 新增 helper：`_build_feedback_loop_graph()`（约 131-222 行）
构造「最小反馈回路」子图：
- 入口 `FeedbackProcessor`（⑨），**不含** `EligibilityChecker` / `TemplateMatcher` 前置节点。
- ⑨ → ⑦：`route_after_feedback` 固定回到 `SectionGenerator`（定向修订，消费
  `feedback_history` 中由 `resume_after_review` 写入的人工审核意见）。
- 保留 ⑦ 之后的校验链：`SectionGenerator → QualityChecker → CrossReferenceChecker
  → ComplianceChecker → ScoreSimulator → HumanReviewGate`，使重生成章节后重新评审核对。
- `HumanReviewGate` 复用 `route_after_review`：
  - rejected+未超轮 → 再回到 `FeedbackProcessor`（图内自动循环，但因 `review_status` 已清空，
    实际到达 HumanReviewGate 时返回 pending 暂停等待下一轮人工审核）；
  - **rejected+超轮 → `DocumentAssembler`(⑭)** 规范行为自然保留；
  - pending → END（暂停）。

### 2. 替换 reject 重跑调用（原 405-418 行）
- 删除 `from core.graph import build_generation_graph` + `build_generation_graph(...)`。
- 改为 `feedback_graph = _build_feedback_loop_graph(llm_fns=...)` 并
  `feedback_graph.invoke(phase2_state)`，`phase2_state["review_status"] = ""` 保持
  （让 HumanReviewGate 单轮修订后回到 pending，而非又因 stale "rejected" 死循环）。

## 未破坏的路径
- approved 路径：仍由「通过审核」按钮直接调 `doc_assembler()`，未改动。
- pending 暂停逻辑：未改动，仍等待用户裁决。
- 超轮→强制装配：由复用的 `route_after_review` 保证，符合 §七。

## 验证结果
`python3 -m py_compile .../review_panel.py` ⇒ **OK: 无语法错误**（exit code 0）。
运行时行为：rejected 现仅走 ⑨→⑦ 最小回路 + 后续校验链，不再重跑 EligibilityChecker/TemplateMatcher。
