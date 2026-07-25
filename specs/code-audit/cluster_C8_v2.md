# 复审计报告 — 簇 C8（v2）

> **审计对象**：`bid-agent/` 全量源码（graph / gui / core.llm / core.config / database）
> **对照规范**：`system-workflow.md`（§一/§二/§4.1/§5/§七/§八）
> **方法**：逐行重读 15 个指定文件 + 规范；逐项核对 7 个旧问题；复查 graph 14 节点/路由、§4.1 多供应商、§5 三模式、GUI 五面板；确认 H10 SectionGenerator 绑定。
> **结论**：7 个旧问题中 **3 已修复（M13/M16/M14）**，**3 未修复（M15/L7/L8）**，**1 部分修复（H12）**。graph 节点图与 §二/§七 仍完全一致；未引入高严重回归，但发现 3 个新问题（含 H12 修复引入的依赖缺失）。

---

## 1. 旧问题修复状态（逐项）

| 编号 | 严重度 | 问题 | 状态 | 证据（file:line） |
|------|--------|------|------|-------------------|
| **H12** | 高 | Anthropic 工厂不存在 + config.yaml `task_routing` 死配置 + `create_llm("anthropic")` 抛 ValueError | **部分修复** | 工厂已补：`providers.py:85-98`（`_create_anthropic`）、`providers.py:109`（注册进 `PROVIDER_FACTORIES`）、`create_llm` 仅对未知供应商抛 ValueError（`providers.py:142`）。**但**死配置仍在：`config/config.yaml:11-14` 的 `task_routing` 从未被运行时读取（见下）；GUI 配置面板无 Anthropic Key 输入（`config_panel.py:72-113` 仅有 DeepSeek/OpenAI）。 |
| **M13** | 中 | 进度组件 `ALL_NODES` 缺 InfoVerificationGate | **已修复** | `progress.py:10`（`"InfoVerificationGate"` 已加入列表）、`progress.py:28`（标签已加），共 14 项。 |
| **M14** | 中 | GUI 仅 4 Tab，规范 §一列 5 组件 | **已确认符合规范（非问题）** | 规范 §一 原文标注「信息校验为嵌入组件非独立面板」。GUI：`app.py:70-84` 为 4 Tab，信息校验作为嵌入组件在审阅面板渲染（`review_panel.py:204-220` 调 `gui.components.info_verification`）。4 Tab + 1 嵌入组件 = §一 5 组件，一致。 |
| **M15** | 中 | rejected 重跑整段 Phase2（从 EligibilityChecker），非规范 §七 最小回路 ⑨→⑦ | **未修复** | `review_panel.py:405-418` 驳回后 `build_generation_graph(llm_fns)` 重新全流程；该图入口为 `EligibilityChecker`（`graph.py:519` 的 `set_entry_point("EligibilityChecker")`）。按 §七 应为 `HumanReviewGate.rejected → FeedbackProcessor → SectionGenerator`（最小回路 ⑬→⑨→⑦），实际重跑了 EligibilityChecker、TemplateMatcher 等前置节点（浪费计算、偏离文档）。R02 修复仅补了 `resume_after_review` 调用与重跑触发，未改变重跑入口。 |
| **M16** | 中 | router.py 默认质检模型 openai/gpt-4o-mini 与规范 DeepSeek 默认冲突 | **已修复** | `router.py:17-23` 全部节点默认 `deepseek/deepseek-chat`（含 `QualityChecker`），并标注「M16 fix」；`router.py:26-27` 全局默认 `deepseek`。 |
| **L7** | 低 | `route_after_score_sim` 死代码（从未调用） | **未修复（仍未删）** | `graph.py:114-121` 定义 `route_after_score_sim`，三个构图函数均用 `add_edge("ScoreSimulator","HumanReviewGate")`（`graph.py:336`、`graph.py:507`），无任何调用。 |
| **L8** | 低 | database.py 仅测试引用，未接入运行时 | **未修复** | `core/database.py` 仅在 `tests/unit/test_database.py:9` 被 import；运行时节点（`core/nodes/*`）与 `core/evolution/metrics_tracker.py`、`prompt_registry.py` 各自独立实现 `_init_db()`（其 `metrics_tracker.py:67`、`prompt_registry.py:65`），**不引用** `core.database`。grep `from core.database` 全库仅命中测试文件。 |

---

## 2. graph.py 14 节点 / 路由一致性复核（vs §二 / §七）

**节点顺序（build_graph `graph.py:254-270`）** 与 §二完全一致：
`DocumentParser → ReqExtractor → ContractExtractor → InfoVerificationGate → EligibilityChecker → TemplateMatcher → SectionGenerator → QualityChecker → FeedbackProcessor → CrossReferenceChecker → ComplianceChecker → ScoreSimulator → HumanReviewGate → DocumentAssembler`（14 节点）。

**边与条件路由** 与 §七 逐条一致：
- `route_after_parser`（`:153`）FAIL→END / SUCCESS→ReqExtractor ✓
- `route_after_extraction`（`:166`）FAIL→END / SUCCESS→ContractExtractor ✓
- `route_after_verification`（`:179`）confirmed→EligibilityChecker / pending→END ✓
- `route_after_eligibility`（`:192`）FAIL→END / PASS·WARNING→TemplateMatcher ✓
- `route_after_quality_check`（`:75`）PASS→CrossRef / FAIL+未超轮→Feedback / FAIL+已超轮→CrossRef（强制）✓
- `route_after_cross_ref`（`:98`）PASS→Compliance / FAIL+未超轮→Feedback ✓
- `route_after_compliance`（`:106`）PASS→ScoreSim / FAIL+未超轮→Feedback ✓
- `route_after_feedback`（`:144`）→SectionGenerator ✓
- `route_after_review`（`:124`）approved→Assembler / rejected+未超→Feedback / rejected+已超→Assembler / pending→END ✓
- ScoreSimulator→HumanReviewGate 为直连边（`graph.py:336`），对应 §二 `N12→N13`，正确；`route_after_score_sim`（L7）为多余死函数。

**结论**：修复未破坏 graph。14 节点、全部条件路由与 §二/§七 一致。`_LLM_NODES`（`graph.py:69`）含 6 个 LLM 节点，经 `functools.partial` 绑定 `llm_fn`，与 §4.1 注入机制一致。

---

## 3. §4.1 多供应商 / §5 三模式 / 五面板复核

- **§4.1 多供应商**：`providers.py:103-110` 注册 6 家（openai/deepseek/zhipu/qwen/moonshot/anthropic），注入机制 `functools.partial` 一致（graph.py:244-248）。但 **Anthropic 端到端不通**：GUI 仅暴露 DeepSeek/OpenAI Key（`config_panel.py:72-113`），`router.py:17-23` 默认表无 anthropic，`config.yaml:11-14` 的 anthropic 路由为死配置（见 H12）。DeepSeek 为默认，与规范一致。
- **§5 三模式**：
  - 5.1 交互模式：`build_extraction_graph`（`graph.py:361`）+ `build_generation_graph`（`graph.py:415`）✓
  - 5.2 无头模式：`build_graph`（`graph.py:214`，InfoVerificationGate/HumanReviewGate 均 `auto_confirm/auto_approve=True`）✓
  - 5.3 快照模式：`run_pipeline_with_snapshots`（`graph.py:591`，`stream(mode="updates")`）✓
  三模式均实现，与 §五 一致。
- **五面板**：4 Tab + 信息校验嵌入组件（见 M14），与 §一 一致。

---

## 4. H10 确认：graph 绑定哪个 SectionGenerator 函数

`graph.py:56`：
```python
from core.nodes.section_generator import generate_all_sections_parallel as section_generator
```
即 **SectionGenerator 节点绑定 `generate_all_sections_parallel`**（并行 8 章生成，含反馈定向修订与错误隔离，`section_generator.py:728-829`）。签名 `(state, llm_fn=None)`，与 `functools.partial` 绑定兼容（`graph.py:262`）。
该选择符合规范 §三 ⑦「8 章通过 ThreadPoolExecutor 并行生成」。模块内另有一个顺序版 `section_generator(state, llm_fn, sections_to_generate)`（`section_generator.py:653`，docstring 标注 “LangGraph Node”）**未被 graph 绑定**——属可维护性陷阱（低），非功能缺陷。

---

## 5. 新发现问题

| 编号 | 严重度 | 问题 | 证据 |
|------|--------|------|------|
| **N1** | 中 | **config.yaml `llm` 段（含 `task_routing`）为死配置，与运行时配置系统分裂**。`runtime`（`pipeline_runner.build_llm_for_pipeline`）只读 `~/.bid-agent/config.json`（`config_persistence.py:36`），从不读 `config.yaml` 的 `llm`/`task_routing`。`settings.py`（config.yaml）仅被 `run_pipeline_with_snapshots` 用于 `paths.export_dir`。两套配置并存，模型路由以 config.json 为准，`config.yaml:8-14` 的 `default_provider`/`task_routing` 误导且永不生效。 | `config/config.yaml:8-14`；`config_persistence.py:36,91`；`pipeline_runner.py:45,89` |
| **N2** | 低 | **H12 修复引入依赖缺失风险**：`providers.py:91` 懒加载 `langchain_anthropic`，但 `requirements*.txt`/`pyproject.toml` 均未声明该包（grep 无 `langchain-anthropic`/`anthropic`）。用户若经 `task_routing`/node_override 选 Anthropic，`_create_anthropic` 在调用时抛 `ImportError`；虽被 `pipeline_runner.py:98` 捕获降级为 mock，但静默失配、无提示，易误判为“已配好 Claude”。 | `providers.py:91`；`pipeline_runner.py:94-100` |
| **N3** | 低 | **`route_after_score_sim` 死函数未清理**（即旧 L7），且与新增的 `human_review_gate` 幂等设计（`auto_approve` 参数）无关联，长期留存增加维护歧义。 | `graph.py:114-121` |

> 注：N1 本质为 H12 残余（死配置）的扩展定性；N2 为 H12 工厂修复带来的新依赖缺口；N3 即未删的 L7。

---

## 6. 结论摘要（≤300 字）

**旧问题**：M13（progress 缺节点）✅已修复（`progress.py:10,28`）；M16（router 默认 QC 模型）✅已修复（`router.py:17-23`）；M14（GUI 4 Tab）✅确认符合规范（信息校验为嵌入组件，`review_panel.py:204-220`）。H12 部分修复：Anthropic 工厂已补（`providers.py:85-109`），但 `config.yaml:11-14` 死配置仍在、且 Anthropic 端到端不通。M15（rejected 重跑整段 Phase2）❌未修复（`review_panel.py:405-418` 重跑 `build_generation_graph`，入口 `EligibilityChecker` `graph.py:519`，偏离 §七 ⑨→⑦ 最小回路）。L7（`route_after_score_sim` 死代码 `graph.py:114-121`）❌未删；L8（database.py 仅测试引用）❌未接入运行时。

**graph 一致性**：14 节点顺序与全部条件路由与 §二/§七 完全一致，修复未破坏。

**H10 确认**：SectionGenerator 绑定 `generate_all_sections_parallel`（并行 8 章，`graph.py:56`），符合 §三 ⑦。

**新发现**：N1 config.yaml `llm` 段死配置/双配置分裂；N2 Anthropic 依赖未声明（H12 修复引入）；N3 L7 死代码未清理。

报告文件：`specs/code-audit/cluster_C8_v2.md`
