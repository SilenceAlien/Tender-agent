# 代码审计读写策略 — 标书制作智能体 14 节点 vs system-workflow.md

> 日期：2026-07-20 | 目标：核对全部源码与 `system-workflow.md`（v1.0）的一致性，找出「遗漏」与「错误」

---

## 一、背景与约束

- **参照物**：`/Users/alanchris/Desktop/标书agent02/system-workflow.md`（716 行，描述 14 个节点 + 横切关注点 + 路由表 + AgentState 字段表）。
- **待审计代码**：`bid-agent/` 下全部非 `.venv` 的 `.py` 文件，共 **~21,579 行**（其中源码约 17,000 行，测试约 4,500 行）。
- **上下文约束**：单轮把全部源码读入主上下文会超 token 上限。因此采用「**主代理只做编排与综合，子代理并行完整读取**」的策略，每个子代理在自己独立进程中读全所分配文件，返回结构化报告。

## 二、范围划分

| 类别 | 是否纳入本次主审计 | 说明 |
|------|:--:|------|
| 核心节点源码 `core/nodes/*.py`（14 个） | ✅ | 直接对应 14 节点，重点 |
| 支撑模块 `core/retrieval`、`core/evolution`、`core/llm`、`core/contract`、`core/state`、`core/graph` | ✅ | 横切关注点（§4.1–4.4、§七路由、§八 State） |
| GUI 层 `gui/**` | ✅ | 架构图 GUI 层 + 两阶段暂停（§5.1） |
| 配置/持久化 `config/**`、`core/config_persistence.py`、`core/database.py` | ✅ | API Key 管理（§4.1）、状态持久化 |
| 脚本 `scripts/**` | ⚠️ 轻量 | 知识库构建，不进入节点逻辑，仅标注 |
| 测试 `tests/**` | ⚠️ 本轮不深读 | 反映预期行为，留作后续「行为一致性」专项；本轮只在节点缺实现时参考 |

> 说明：测试文件不在本轮「完整逐行读取」清单内，是为了把上下文预算集中到「节点实现 vs 规范」这一核心矛盾；若需要，可追加一轮测试专项审计。

## 三、执行方法（读写策略核心）

1. **分簇并行**：把 50 个源文件按节点/横切维度切成 8 个簇，每簇派 1 个 `general-purpose` 子代理，**独立进程内完整读取**所分配的每个 `.py` 文件（用 Read 工具，不跳过、不 Grep 替代）。
2. **规范就地读取**：每个子代理自行 `Read /Users/alanchris/Desktop/标书agent02/system-workflow.md` 中**分配给它的节点章节**，保证比对用的是规范原文，避免主代理转述偏差。
3. **落盘 + 回流**：每个子代理把详细发现写入 `specs/code-audit/cluster_X.md`，并向主代理返回一段精简摘要（含路径）。主代理随后读取 8 份簇报告，综合成 `findings.md`。
4. **证据化**：所有「缺失/错误」必须带 `文件:行号` 引用，便于人工复核；无法确定时标注「需人工确认」而非臆断。

## 四、簇划分与文件清单

| 簇 | 对应规范 | 文件 |
|----|---------|------|
| **C1** 解析与需求 | ① ② ③、§七(route_parser/extraction)、§八 State | `core/nodes/doc_parser.py`、`core/nodes/req_extractor.py`、`core/nodes/contract_extractor.py`、`core/contract.py`、`core/state.py` |
| **C2** 校验门/资质/模板 | ④ ⑤ ⑥、§七(route_verification/eligibility) | `core/nodes/info_verification_gate.py`、`gui/components/info_verification.py`、`core/nodes/eligibility_checker.py`、`core/nodes/template_matcher.py` |
| **C3** 章节生成 | ⑦ | `core/nodes/section_generator.py`、`core/nodes/optimized_prompts.py` |
| **C4** 校验链 | ⑧ ⑨ ⑩ ⑪ ⑫ ⑬、§七(routes) | `core/nodes/quality_checker.py`、`core/nodes/feedback_processor.py`、`core/nodes/cross_reference_checker.py`、`core/nodes/compliance_checker.py`、`core/nodes/score_simulator.py`、`core/nodes/human_review_gate.py` |
| **C5** 文档装配 | ⑭ | `core/nodes/doc_assembler.py` |
| **C6** 检索/RAG | ⑥ FAISS、§4.2 检索管线、§4.4 知识库、子类型权重表 | `core/retrieval/embeddings.py`、`faiss_index.py`、`pipeline.py`、`reference_retriever.py`、`template_library.py`、`subtype_router.py` |
| **C7** 一致性自进化 | ⑨ 自进化触发、§4.3 | `core/evolution/prompt_selector.py`、`prompt_registry.py`、`consistency_lesson_store.py`、`consistency_lesson_extractor.py`、`metrics_tracker.py` |
| **C8** 编排/GUI/LLM/配置 | §一 架构、§二 工作流图+两阶段拆分、§4.1 LLM、§5 运行模式、§七路由汇总、§八 State | `core/graph.py`、`gui/pipeline_runner.py`、`gui/app.py`、`gui/panels/{upload,config,export,review}_panel.py`、`gui/components/{progress,reset,info_verification,feedback_form}.py`、`core/llm/{router,providers}.py`、`config/settings.py`、`core/config_persistence.py`、`core/database.py` |

## 五、子代理输出格式（每份 cluster_X.md）

```
# 簇 Cx — 审计发现
## 1. 文件清单（路径 + 行数 + 一句话职责）
## 2. 节点实现对照表
| 规范条款 | 代码位置(file:line) | 状态(✓/缺失/错误) | 说明 |
## 3. 错误/缺陷明细（按严重度：高/中/低）
## 4. 遗漏功能（规范有、代码无）
## 5. 跨节点/横切问题
## 6. 需人工确认项
```

## 六、综合与交付

- 主代理读取 8 份簇报告 → 生成 `specs/code-audit/findings.md`（总览 + 分节点结论 + 优先级排序的修复清单）。
- 最终向用户呈现：策略文档链接 + findings 摘要 + 关键遗漏/错误列表。
