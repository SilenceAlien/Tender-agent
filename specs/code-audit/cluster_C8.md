# 簇 C8 — 审计发现

> 审计对象：bid-agent 编排层 / GUI 层 / LLM 适配层 / 配置·持久化层
> 对照规范：`system-workflow.md`（v1.0）
> 审计方式：逐行读源码 + 逐条比对规范 §一/§二/§4.1/§5/§七/§八

## 1. 文件清单（路径 + 行数 + 职责）

| 文件 | 行数 | 职责 |
|------|-----:|------|
| `core/graph.py` | 672 | LangGraph 14 节点状态机，含 `build_graph`/`build_extraction_graph`/`build_generation_graph`、`run_pipeline_with_snapshots`、9 条条件路由 |
| `gui/pipeline_runner.py` | 118 | 从 `~/.bid-agent/config.json` 构建各节点 `llm_fn` 字典，含 API Key 预校验 |
| `gui/app.py` | 89 | Streamlit 入口，4 个 Tab：模型配置/资料上传/生成与审阅/导出交付 |
| `gui/panels/upload_panel.py` | 144 | 文件上传、标书类型、投标人/项目信息、补充说明、启动提取 |
| `gui/panels/config_panel.py` | 264 | API Key 录入、DeepSeek/OpenAI 模型选择、节点级模型覆盖、持久化到 config.json |
| `gui/panels/export_panel.py` | 110 | DOCX/JSON 导出、质量门提示、版本历史 |
| `gui/panels/review_panel.py` | 475 | Phase1/Phase2 串联、信息校验表单、章节预览、人工审核裁决、反馈重生成、快照展示 |
| `gui/components/progress.py` | 56 | 管道进度条（13 节点，缺 InfoVerificationGate） |
| `gui/components/reset.py` | 76 | 重置管道 session_state 与临时文件 |
| `gui/components/feedback_form.py` | 157 | 反馈提交与「应用反馈并重新生成」 |
| `core/llm/router.py` | 116 | `ModelRouter`：节点→模型映射与缓存 |
| `core/llm/providers.py` | 210 | 多供应商工厂（openai/deepseek/zhipu/qwen/moonshot）+ 连接测试 |
| `config/settings.py` | 236 | `Settings`：读 `config/config.yaml` + 环境变量覆盖，路径解析 |
| `core/config_persistence.py` | 100 | LLM 配置读写 `~/.bid-agent/config.json`、`get_model_for_node` |
| `core/database.py` | 336 | SQLite：projects/documents/sections/feedbacks/configs CRUD |

## 2. 实现对照表

| 规范条款 | 代码位置(file:line) | 状态 | 说明 |
|---------|---------------------|------|------|
| §二 14 节点顺序与边（Parser→Req→Contract→Verify→Elig→Template→Section→QC→…→Asm） | `graph.py:254-350` | ✓ | `build_graph` 节点与边与 §二 mermaid 完全一致 |
| §二 Phase1/Phase2 两阶段拆分 | `graph.py:361-412`（提取）、`graph.py:415-520`（生成） | ✓ | 入口/节点集合与规范一致 |
| §七 9 条路由函数（parser/extraction/verification/eligibility/quality_check/cross_ref/compliance/feedback/review） | `graph.py:153-189`/`192-208`/`75-95`/`98-103`/`106-111`/`144-150`/`124-141` | ✓ | 全部实现，条件与 §七 一致 |
| §七 路由 FAIL+已超轮次强制通过 | `graph.py:90-93`/`101`/`109`/`135-139` | ✓ | 强制通过逻辑一致 |
| §4.1 DeepSeek 默认（生成/提取/质检） | `config_persistence.py:18-19` | ✓ | 默认 provider=deepseek/model=deepseek-chat |
| §4.1 `functools.partial` 预绑定 `llm_fn` | `graph.py:35,244-248,258,261,269` | ✓ | 一致 |
| §4.1 节点级路由可配不同模型 | `config_panel.py:185-222`、`pipeline_runner.py:88-103`、`graph.py:244-248` | ✓ | 配置→`get_model_for_node`→partial 链路完整 |
| §4.1 配置存 `~/.bid-agent/config.json`（项目目录外） | `config_persistence.py:14-15` | ✓ | 路径正确 |
| §5.1 交互模式两阶段暂停（VerifyGate + HumanReviewGate） | `graph.py:393`/`466`、`review_panel.py:204-220,334-426` | ✓ | 两门均暂停并 GUI 恢复 |
| §5.2 无头模式自动通过 | `graph.py:258,269` | ✓ | `auto_confirm=True`/`auto_approve=True` |
| §5.3 快照导出 `run_pipeline_with_snapshots` | `graph.py:591-671` | ✓ | 已实现，基于 `stream(mode="updates")` |
| §八 AgentState 字段流转 | 各节点（本次未逐个读 `core/nodes/*`） | 需人工确认 | 状态字段在 nodes 层生产，超出本批审计范围 |
| §4.1 OpenAI 供应商（gpt-4o/mini） | `providers.py:88`、`config_panel.py:20` | ✓ | 已实现 |
| §4.1 Anthropic（claude-3.5-sonnet，质检专用） | `providers.py:87-93`、`config_panel.py:20,185-190`、`config.yaml:11-14` | **缺失/错误** | 见 §3-高-1、§4-1 |
| §一 GUI 五组件（上传/信息校验/审阅/配置/导出） | `app.py:70-84` | **错误** | 仅 4 个 Tab，信息校验以组件嵌入 review_panel（`review_panel.py:205` 引用 `gui/components/info_verification.py`，文件存在） |
| §4.1「API Key 内存暂存，不持久化到磁盘」 | `config_persistence.py:64-88`、`config_panel.py:119-126,173` | **矛盾** | 规范自相矛盾（同句又称存 config.json）；代码按「落盘」实现，自动写入 api_keys |
| §4.1 默认质检模型为 DeepSeek | `router.py:17-22` | **错误** | `DEFAULT_NODE_MODELS` 将 QualityChecker 默认设为 openai/gpt-4o-mini，与规范及 `config_persistence` 默认不一致（该默认实际未被 pipeline_runner 采用，但属误导） |
| §一/§二 14 节点应包含 InfoVerificationGate | `progress.py:5-19` | **错误** | 进度组件 ALL_NODES 仅 13 个，缺 InfoVerificationGate |
| §七 「ScoreSimulator→HumanReviewGate」为普通边 | `graph.py:336,507` | ✓ | 用 `add_edge`，正确；但 `route_after_score_sim`（`graph.py:114-121`）定义后从未被调用（死代码） |

## 3. 错误/缺陷明细（高/中/低）

### 高
- **H1 — Anthropic/Claude 供应商完全不可用（§4.1 矛盾）**
  - `providers.py:87-93` 的 `PROVIDER_FACTORIES` 只含 openai/deepseek/zhipu/qwen/moonshot，**无 anthropic 工厂**；`config_panel.py:20` 的 `OA_MODELS` 与节点覆盖选项（`config_panel.py:185-190`）均无 Anthropic 入口。规范 §4.1 明确 Anthropic(claude-3.5-sonnet) 为「质检专用」可选供应商。
  - 更糟：`config.yaml:11-14` 声明 `task_routing.quality_checker.provider: anthropic`，但流水线**根本不读 `config.yaml` 的 `task_routing`**（`pipeline_runner.py` 仅用 `config_persistence.load_config()` 的 `node_overrides`，默认空）。该配置为死配置；若强行启用将因 `create_llm("anthropic",…)` 抛 `ValueError`（providers.py:123-125）。
  - 模型名不一致：规范写 `claude-3.5-sonnet`，`config.yaml` 写 `claude-sonnet-4-20250514`。
  - 结论：§4.1 Anthropic 支持在代码侧实效（声明≠可调=可创建）。

### 中
- **M1 — 进度组件漏列 InfoVerificationGate（`progress.py:5-19`）**
  - `ALL_NODES` 为 13 项，缺少节点④ `InfoVerificationGate`，与规范 §二/§八 的 14 节点不符，进度条少显示一道关卡。
- **M2 — GUI 结构为 4 Tab，规范 §一 列 5 组件（`app.py:70-84`）**
  - 信息校验以组件（`gui/components/info_verification.py`）嵌入 review_panel 流程，非独立 Tab。功能存在但结构与规范架构图不完全对应。
- **M3 — 交互模式 rejected 重生成重跑整段 Phase2（`review_panel.py:405-418`、`feedback_form.py:36-51`）**
  - 规范 §七 `route_after_review: rejected → FeedbackProcessor → SectionGenerator`（最小回路）。代码改为：拒绝后**重新 `build_generation_graph` 从 `EligibilityChecker` 入口整体重跑**（重新执行 EligibilityChecker/TemplateMatcher）。依赖 `section_generator` 定向修订避免全量再生，结果正确但冗余、且不与 headless `build_graph` 内循环（FeedbackProcessor→SectionGenerator…→HumanReviewGate）一致。
- **M4 — `router.py:17-22` 默认质检模型与规范/持久化层冲突**
  - `DEFAULT_NODE_MODELS["QualityChecker"]={"provider":"openai","model":"gpt-4o-mini"}`，而规范 §4.1 默认 DeepSeek、`config_persistence` 默认 deepseek-chat。该默认值未被 `pipeline_runner` 采用（其显式传参覆盖），属潜在误导/死默认。

### 低
- **L1 — `route_after_score_sim` 死代码（`graph.py:114-121`）**：定义但 `build_graph`/`build_generation_graph` 均用 `add_edge("ScoreSimulator","HumanReviewGate")`（`:336,507`），从未调用。
- **L2 — `route_after_feedback` 返回类型含 `"__end__"`（`graph.py:144`）**：但 `add_conditional_edges` 映射（`graph.py:327-333,501-505`）未注册 `__end__`，当前恒返回 `"SectionGenerator"` 不会触发，属类型/映射不一致噪音。
- **L3 — `database.py` 在生产流水线中未被引用**（仅 `tests/unit/test_database.py`）。SQLite 持久化层（projects/documents/sections/feedbacks/configs）与 `config_persistence.py`（JSON 配置）职责重叠（都含「config」存储），但二者均未接入 `app.py`/`graph.py` 的实时运行路径。见 §4-2、§5。

## 4. 遗漏功能（规范有、代码无）

1. **Anthropic/Claude 供应商未真正落地**：`providers.py` 缺工厂、`config_panel.py` 缺 UI、`config.yaml` 的 `task_routing` 为死配置、模型名与规范不符（§4.1，详见 H1）。
2. **API Key「不落盘」诉求未满足**：规范 §4.1 同时写「不持久化」与「存 config.json」（自相矛盾），代码按落盘实现并**自动保存明文 key**（仅目录 700/文件 600，`config_persistence.py:28-33,82-86`）。若严格按「内存暂存不落盘」解读，则缺「仅内存、进程退出即弃」的实现路径（如 session-only 模式开关）。
3. **`config.yaml` 的 `llm.task_routing` 未被任何代码消费**：节点级模型路由实际只来自 `config_persistence.node_overrides`；`settings.get("llm.task_routing.*")` 在流水线中从未被查询 → 规范「节点级路由可配」在 YAML 侧成死配置。
4. **进度可视化缺 InfoVerificationGate 节点**（M1）。
5. **§一 信息校验独立面板/组件入口形态与规范不完全一致**（M2，功能上存在）。

## 5. 跨节点/横切问题

- **graph.py 节点图 vs §二 工作流图：顺序与连接「一致」**。
  - `build_graph`（:254-350）节点序列 = ①Parser→②Req→③Contract→④Verify(auto_confirm)→⑤Elig→⑥Template→⑦Section→⑧QC→(路由)⑩CrossRef/⑨Feedback→⑩CrossRef→⑪Compliance→⑫ScoreSim→⑬HumanReview→⑭Asm，**与 §二 mermaid 的 14 节点顺序、主边、反馈环（⑨→⑦）、及 FAIL+已超轮次强制通过完全一致**。
  - 未发现节点顺序错误或错连边。
- **两处规范未要求但存在的偏离**：
  1. `route_after_score_sim` 定义未使用（L1）——不影响行为（§二 N12→N13 本就是普通边）。
  2. 交互模式 rejected 回路比规范 §七 更「重」（M3，重跑整段 Phase2 而非最小 FeedbackProcessor→SectionGenerator 回路）。
- **配置系统双轨**：`config.yaml`(settings.py，项目内) 与 `~/.bid-agent/config.json`(config_persistence.py，项目外) 并存；LLM 模型/Key 走后者、路径/检索/生成超参走前者。**规范 §4.1 只提到 config.json**，`config.yaml` 中 `llm.task_routing` 与 `llm.default_*` 实际不被流水线使用，与 `config_persistence` 默认值可能冲突（如默认质检模型，M4）。
- **持久化职责割裂**：`database.py`(SQLite) 与 `config_persistence.py`(JSON) 各自维护一份「配置/状态」，`database.py` 的 `configs` 表与 `config_persistence` 的 `api_keys/provider/model` 重叠但互不联通；且 `database.py` 未接入运行时（L3）。

## 6. 需人工确认项

1. **§4.1「API Key 内存暂存不落盘」是否确为诉求**：规范同句自相矛盾（又称存 config.json）。请明确——当前落盘实现是否符合预期，还是需增加「session-only 不写盘」开关。
2. **`gui/components/info_verification.py` 的 11 字段校验表单**（规范 §四 ④ 列 11 字段 + 来源标注 + 子类型三级识别）是否齐备：本批审计未读该文件，仅确认其被 `review_panel.py:205` 引用且文件存在。
3. **`database.py` 是否应在生产路径启用**：当前仅测试引用，是否计划接入项目级持久化（断点续跑/多项目）。若否，其与 `config_persistence` 的职责边界需文档澄清。
4. **`config.yaml` 的 `llm.task_routing` 是否应被 `pipeline_runner` 消费**（以 YAML 作为节点级默认路由源）；当前其为死配置。
5. **Anthropic 支持优先级**：若需落实 §4.1，需新增 `providers.py` 工厂（含 claude 实际模型名）、`config_panel.py` UI、`router.py`/`config_persistence` 默认值，并清理 `config.yaml` 中无效声明。
6. **各节点对 `AgentState` 字段的生产/消费**（规范 §八）是否正确：需读 `core/nodes/*` 逐节点核对，超出本批文件范围。
