# 功能规范 — 标书资料信息提取人工校验门

## 需求背景

### 当前流程的问题

当前的标书生成流程是**一键式单次执行**：用户在上传面板点击「开始生成标书」后，整个 13 节点 LangGraph 管道同步跑完——从文档解析、需求提取、契约构建，一直到章节生成、质量检查、文档装配。

这带来一个关键风险：

1. **关键信息提取深埋在管道内部**——`ReqExtractor` 从招标文件中提取 `project_name`（项目名称）、`bid_number`（招标编号）、`tenderer_name`（招标人）、`package_number`（包件号）；`ContractExtractor` 从补充说明中提取 `bidder_name`（投标人）、`project_location`（项目地点）、`industry`（行业）、`duration`（工期）等。这些都是 LLM 驱动的提取，**存在幻觉和误提取风险**。

2. **用户无法在生成前校验提取结果**——如果 LLM 把项目名称提取错了一个字，或者把招标编号漏了，那么后续 8 个章节全部用错误信息生成，浪费大量 token 和时间。等到管道末端的 `HumanReviewGate`（人工审核门）发现问题时，内容已经生成完毕，修正成本极高。

3. **表单字段未接入管道**——上传面板已经收集了 `form_bidder_name`、`form_tenderer_name`、`form_project_location`、`form_duration` 等表单输入，但这些值**目前并未传入** `factory_state()`，也没有在 `ContractExtractor` 的三方合并中使用（代码注释明确标注 `表单字段(暂无)`）。

### 用户要解决的核心问题

> "在文件上传后，调用解析器对文本进行读取，然后针对读取的信息进行关键信息提取（如招标人、标号等），放在前端界面由用户核对，确认无误后再点击开始生成标书。"

这本质上是在管道的**提取阶段和生成阶段之间**插入一道人工校验门，让人的判断力在**最昂贵的 LLM 生成之前**发挥作用——用最小成本拦截提取错误。

---

## 可行性评估

### 结论：**完全可行，且与现有架构高度契合**

| 评估维度 | 现状 | 契合度 |
|---------|------|--------|
| 暂停-恢复模式 | `HumanReviewGate` 节点已实现 `pending/approved/rejected` 模式 + `resume_after_review()` 恢复函数 | ✅ 可直接复用模式 |
| 信息提取逻辑 | `ReqExtractor` + `ContractExtractor` 已完整实现 LLM 提取 + 正则兜底 + 三方合并 | ✅ 无需新写提取逻辑 |
| 契约数据结构 | `ProjectContextContract` 已定义全部关键字段 + `is_valid()` 校验 + `to_dict()/from_dict()` 序列化 | ✅ 直接作为校验表单的数据模型 |
| 表单字段预留 | 上传面板已收集 4 个表单字段，`ContractExtractor._merge_contract_sources()` 已预留"表单字段 > 招标文件 > LLM"优先级 | ✅ 补全接线即可 |
| 管道图结构 | LangGraph `StateGraph` 支持条件路由 + 子图拆分 | ✅ 可拆为两阶段子图 |
| GUI 交互模式 | Streamlit `session_state` + 多 Tab 页面 | ✅ 可新增校验步骤 |

### 与现有 `HumanReviewGate` 的关系

项目已有一个 `HumanReviewGate`（位于管道末端，`ScoreSimulator` 之后、`DocumentAssembler` 之前）。新提案的门与之**互补而非替代**：

| | 现有 HumanReviewGate（末端） | 新增 InfoVerificationGate（前端） |
|---|---|---|
| **位置** | 章节生成 + 评分模拟之后 | 信息提取之后、章节生成之前 |
| **校验对象** | 生成的标书内容质量 | 提取的关键信息准确性 |
| **拦截成本** | 高（已花费 8 章 LLM 生成 + 质检 token） | 低（仅花费提取 token） |
| **决策影响** | 决定是否需要重写内容 | 决定是否可以开始生成 |
| **模式** | approved → 装配；rejected → 反馈重写 | confirmed → 生成；edited → 注入修正后生成 |

两者形成**"前门 + 后门"双重校验**：前门拦截提取错误，后门拦截生成质量。

---

## 方案设计

### 核心思路：管道两阶段拆分 + 中间插入校验门

```
┌─────────────────────────────────────────────────────────┐
│  Phase 1：提取阶段（Extraction Sub-Graph）                │
│                                                         │
│  DocumentParser → ReqExtractor → ContractExtractor       │
│                                         │               │
│                                         ▼               │
│                              [InfoVerificationGate]      │
│                                  暂停，返回 GUI           │
└─────────────────────────────────────────┬───────────────┘
                                          │
                              用户在前端校验/修改
                              点击「确认并开始生成」
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 2：生成阶段（Generation Sub-Graph）                │
│                                                         │
│  EligibilityChecker → TemplateMatcher → SectionGenerator │
│    → QualityChecker → CrossRef → Compliance → ScoreSim   │
│    → HumanReviewGate → DocumentAssembler                 │
└─────────────────────────────────────────────────────────┘
```

### 1. 新增节点：`InfoVerificationGate`

**文件**：`bid-agent/core/nodes/info_verification_gate.py`

**职责**：
- 接收 `ReqExtractor` + `ContractExtractor` 的输出（`requirements` + `project_contract`）
- 汇总为一份「关键信息摘要」供前端展示
- 设置 `info_verification_status = "pending"`，暂停管道
- 接收用户校验结果（确认 / 修改后的值），注入 state

**接口契约**：

```python
def info_verification_gate(state: AgentState) -> dict:
    """
    输入：state.requirements, state.project_contract
    输出：{
        info_verification_status: "pending" | "confirmed",
        info_summary: {  # 供前端展示的关键信息摘要
            project_name: str,
            bid_number: str,
            tenderer_name: str,
            package_number: str,
            bidder_name: str,
            project_location: str,
            industry: str,
            duration: str,
            warranty: str,
            service_target: str,
            bid_type: str,
            # 提取来源标注（让用户知道每个字段的来源）
            field_sources: {
                project_name: "招标文件" | "补充说明" | "LLM" | "用户填写",
                ...
            },
            # 表格提取摘要
            scoring_count: int,
            qualification_count: int,
        },
        node_status: {"InfoVerificationGate": COMPLETED},
    }
    """

def apply_user_corrections(
    state: AgentState,
    corrected_fields: dict,
) -> dict:
    """
    用户校验后调用，将修正值注入 state。

    修正后的值作为最高优先级「表单字段」覆盖到 project_contract。
    ContractExtractor 的合并优先级：用户校验 > 招标文件提取 > LLM提取

    Args:
        corrected_fields: 用户确认/修改后的字段 dict，如：
            {"project_name": "XXX项目", "bidder_name": "广州华南人力有限公司", ...}
    Returns:
        {project_contract: 修正后的 dict, info_verification_status: "confirmed"}
    """
```

**工作模式**（复用 `HumanReviewGate` 的模式）：

- **交互模式（GUI）**：节点设置 `info_verification_status = "pending"` 并返回，GUI 展示校验表单，用户确认后调用 `apply_user_corrections()` 注入结果，然后恢复 Phase 2 管道。
- **无头模式（CI/测试）**：`auto_confirm=True` 时自动通过，不暂停。

### 2. State 扩展

在 `AgentState` (TypedDict) 中新增字段：

```python
class AgentState(TypedDict):
    # ... 现有字段 ...

    # ── Info Verification Gate (新增) ───────────────────────────────
    # "pending" | "confirmed" — 信息校验门状态
    info_verification_status: str
    # 供前端展示的关键信息摘要 + 字段来源标注
    info_summary: dict
    # 用户校验后的修正值（最高优先级，覆盖 LLM 提取结果）
    user_confirmed_fields: dict
```

在 `ALL_NODES` 列表中插入 `"InfoVerificationGate"`（位于 `ContractExtractor` 之后、`EligibilityChecker` 之前）。

在 `factory_state()` 中初始化：
```python
"info_verification_status": "",
"info_summary": {},
"user_confirmed_fields": {},
```

### 3. 管道图改造

**文件**：`bid-agent/core/graph.py`

将单一 `build_graph()` 拆为两个构建函数 + 一个全量构建函数：

```python
def build_extraction_graph(llm_fns=None) -> StateGraph:
    """Phase 1：提取阶段子图。

    DocumentParser → ReqExtractor → ContractExtractor → InfoVerificationGate → END

    InfoVerificationGate 设置 pending 后管道自然终止（END），
    GUI 拿到中间状态展示校验表单。
    """
    graph = StateGraph(AgentState)
    graph.add_node("DocumentParser", document_parser)
    graph.add_node("ReqExtractor", _bind("ReqExtractor", req_extractor))
    graph.add_node("ContractExtractor", _bind("ContractExtractor", contract_extractor))
    graph.add_node("InfoVerificationGate", info_verification_gate)

    graph.add_edge("DocumentParser", "ReqExtractor")  # 保留 route_after_parser 条件路由
    graph.add_edge("ReqExtractor", "ContractExtractor")  # 保留 route_after_extraction
    graph.add_edge("ContractExtractor", "InfoVerificationGate")
    graph.add_edge("InfoVerificationGate", END)  # pending → 自然终止，等用户确认

    graph.set_entry_point("DocumentParser")
    return graph.compile()


def build_generation_graph(llm_fns=None) -> StateGraph:
    """Phase 2：生成阶段子图。

    EligibilityChecker → TemplateMatcher → ... → DocumentAssembler → END

    入口接收 Phase 1 + 用户校验后的完整 state。
    """
    graph = StateGraph(AgentState)
    # 注册 EligibilityChecker ~ DocumentAssembler（与现有逻辑一致）
    ...
    graph.set_entry_point("EligibilityChecker")
    return graph.compile()


def build_graph(llm_fns=None) -> StateGraph:
    """全量管道（向后兼容，无头模式使用）。

    与现有逻辑一致，但插入 InfoVerificationGate（auto_confirm=True）。
    用于 CI 测试和 headless 批处理场景。
    """
    ...
```

**条件路由变化**：

```python
def route_after_verification(state: AgentState) -> Literal["EligibilityChecker", "__end__"]:
    """信息校验门：confirmed → 进入生成阶段，pending → 暂停。"""
    status = state.get("info_verification_status", "")
    if status == "confirmed":
        return "EligibilityChecker"
    return "__end__"  # pending → 暂停，等 GUI 恢复
```

### 4. GUI 交互改造

#### 4.1 上传面板变化（`upload_panel.py`）

**按钮文案和流程变化**：

```
原来：[🚀 开始生成标书]  →  一键跑完
新增：[📋 解析并提取信息]  →  仅跑 Phase 1，然后展示校验表单
```

点击「解析并提取信息」后：
1. 保存上传文件到临时目录（与现有逻辑一致）
2. 设置 `session_state["extraction_started"] = True`
3. 提示用户切换到校验区域

**表单字段接入**：将现有的 `form_bidder_name`、`form_tenderer_name`、`form_project_location`、`form_duration` 在 Phase 1 启动时传入 `factory_state()`，作为初始提示（ContractExtractor 会将它们与 LLM 提取结果合并）。

#### 4.2 新增校验面板组件

**文件**：`bid-agent/gui/components/info_verification.py`（新建）

```python
def render_info_verification(extraction_result: dict) -> dict | None:
    """渲染信息校验表单，返回用户确认/修正后的字段 dict 或 None（未确认时）。

    展示内容：
    1. 关键信息表格（项目名称、招标编号、招标人、投标人、项目地点、行业、工期...）
       - 每个字段标注提取来源（招标文件 / 补充说明 / LLM / 用户填写）
       - 每个字段可编辑（text_input 预填提取值）
    2. 提取摘要统计（评分项 N 条、资质要求 N 条、表格 N 个）
    3. 警告提示（必填字段缺失时标红）
    4. [✅ 确认无误，开始生成] 按钮
    """
```

**UI 示意**：

```
┌─────────────────────────────────────────────────────┐
│  📋 关键信息校验                                      │
│  请核对以下从招标文件中自动提取的信息，确认无误后开始生成   │
├─────────────────────────────────────────────────────┤
│                                                     │
│  项目名称  [________________________]  来源：招标文件  │
│  招标编号  [________________________]  来源：招标文件  │
│  招标人    [________________________]  来源：招标文件  │
│  包件号    [________________________]  来源：招标文件  │
│  投标人    [________________________]  来源：补充说明  │
│  项目地点  [________________________]  来源：补充说明  │
│  行业属性  [________________________]  来源：推断      │
│  工期      [________________________]  来源：补充说明  │
│                                                     │
│  ── 提取摘要 ──                                      │
│  ✅ 评分项 15 条  ✅ 资质要求 8 条  ✅ 表格 3 个       │
│  ⚠️ 项目名称为空，建议手动填写                         │
│                                                     │
│            [✅ 确认无误，开始生成标书]                  │
└─────────────────────────────────────────────────────┘
```

#### 4.3 审阅面板变化（`review_panel.py`）

审阅面板的执行逻辑改为两阶段：

```python
# Phase 1：提取阶段
if st.session_state.get("extraction_started") and "extraction_result" not in st.session_state:
    with st.spinner("正在解析文档并提取关键信息..."):
        extraction_graph = build_extraction_graph(llm_fns)
        extraction_state = factory_state(documents=docs, ...)
        extraction_result = extraction_graph.invoke(extraction_state)
        st.session_state["extraction_result"] = extraction_result

# 展示校验表单
extraction_result = st.session_state.get("extraction_result")
if extraction_result and extraction_result.get("info_verification_status") == "pending":
    corrected = render_info_verification(extraction_result)
    if corrected is not None:
        # 用户确认了 → 注入修正值，启动 Phase 2
        apply_user_corrections(extraction_result, corrected)
        st.session_state["generation_started"] = True
        st.rerun()

# Phase 2：生成阶段
if st.session_state.get("generation_started") and "pipeline_result" not in st.session_state:
    with st.spinner("🚀 正在运行 AI 标书生成管道..."):
        generation_graph = build_generation_graph(llm_fns)
        result = generation_graph.invoke(extraction_result)  # 接续 Phase 1 的 state
        st.session_state["pipeline_result"] = result
```

### 5. ContractExtractor 改造

**文件**：`bid-agent/core/nodes/contract_extractor.py`

修改 `_merge_contract_sources()` 函数，补全「表单字段」优先级层：

```python
def _merge_contract_sources(
    llm_extracted: dict,
    requirements: dict,
    bid_type: str,
    user_confirmed_fields: dict | None = None,  # 新增参数
) -> ProjectContextContract:
    """三方合并 → 四方合并.

    优先级: 用户校验确认 > 招标文件提取 > LLM从补充说明提取
    """
    # 从 requirements 提取（招标文件）
    req_project_name = requirements.get("project_name", "")
    req_tenderer = requirements.get("tenderer_name", "")
    req_bid_number = requirements.get("bid_number", "")
    req_package_number = requirements.get("package_number", "")

    # 用户校验确认的字段（最高优先级）
    confirmed = user_confirmed_fields or {}

    contract = ProjectContextContract(
        project_name=(
            confirmed.get("project_name")              # 1. 用户校验确认
            or req_project_name                         # 2. 招标文件提取
            or llm_extracted.get("project_name", "")    # 3. LLM 提取
        ),
        bidder_name=(
            confirmed.get("bidder_name")
            or llm_extracted.get("bidder_name", "")
        ),
        tenderer_name=(
            confirmed.get("tenderer_name")
            or req_tenderer
            or llm_extracted.get("tenderer_name", "")
        ),
        project_code=(
            confirmed.get("bid_number")
            or req_bid_number
            or llm_extracted.get("project_code", "")
        ),
        # ... 其余字段同理 ...
    )
    ...
```

### 6. 字段来源标注

为了让用户知道每个字段的值是怎么来的（方便判断可信度），`InfoVerificationGate` 在构建 `info_summary` 时标注来源：

```python
def _annotate_field_sources(
    requirements: dict,
    project_contract: dict,
    user_fields: dict,  # 上传面板表单字段
) -> dict:
    """为每个字段标注提取来源。

    来源优先级：用户填写 > 招标文件 > 补充说明(LLM) > 推断 > 缺失
    """
    sources = {}
    contract = project_contract

    # project_name
    if user_fields.get("project_name"):
        sources["project_name"] = "用户填写"
    elif requirements.get("project_name"):
        sources["project_name"] = "招标文件"
    elif contract.get("project_name"):
        sources["project_name"] = "补充说明"
    else:
        sources["project_name"] = "缺失"

    # ... 其余字段同理 ...
    return sources
```

---

## 校验的关键信息字段清单

| 字段 | 中文 | 来源 | 必填 | 说明 |
|------|------|------|------|------|
| `project_name` | 项目名称 | 招标文件 / 用户 | ✅ | 全篇一致性核心字段 |
| `bid_number` | 招标编号 | 招标文件 | ✅ | 封面必填 |
| `tenderer_name` | 招标人 | 招标文件 | ✅ | 全篇一致性 |
| `package_number` | 包件号 | 招标文件 | ❌ | 有则填 |
| `bidder_name` | 投标人 | 补充说明 / 用户表单 | ✅ | 全篇一致性核心字段 |
| `project_location` | 项目地点 | 补充说明 / 用户表单 | ❌ | 建议填写 |
| `industry` | 行业属性 | 推断 / 补充说明 | ❌ | 影响 prompt 选择 |
| `duration` | 工期/服务期 | 补充说明 / 用户表单 | ❌ | 建议填写 |
| `warranty` | 质保期 | 补充说明 | ❌ | 有则填 |
| `service_target` | 服务对象 | 招标文件 / 补充说明 | ❌ | 有则填 |
| `bid_type` | 标书类型 | 用户选择 | ✅ | 已在上传面板选择 |

---

## 用户故事

### 用户故事 6 - 提取信息人工校验（优先级：P1）

作为投标负责人，我希望在上传招标文件后，系统能自动解析并提取关键信息（项目名称、招标编号、招标人、投标人等），让我在前端核对确认无误后再开始生成标书，这样就不会因为提取错误导致整篇标书内容偏差。

**为什么这个优先级**：关键信息提取错误会导致 8 个章节全部用错误信息生成，浪费大量 token 和时间。在生成前校验是成本最低的错误拦截点。

**独立测试**：上传一份招标文件 PDF → 点击「解析并提取信息」→ 系统展示提取的关键信息表单 → 核对/修改后点击「确认并开始生成」→ 系统使用确认后的信息生成标书。

**验收场景**：
1. **给定** 一份服务类招标文件 PDF，**当** 用户上传并点击「解析并提取信息」，**那么** 系统在 30 秒内展示包含项目名称、招标编号、招标人、投标人等关键字段的可编辑表单，每个字段标注提取来源
2. **给定** 系统提取的项目名称为「XX大学物业服务采购项目」但实际应为「XX大学物业管理服务采购项目」，**当** 用户在表单中修改项目名称并点击确认，**那么** 生成的 8 个章节全部使用修改后的正确项目名称
3. **给定** 招标文件中未提及投标人名称，**当** 系统展示校验表单，**那么** 投标人字段标红提示「未提取到，请手动填写」，用户填写后该值作为最高优先级注入全篇
4. **给定** 用户在无头模式（CI 测试）下运行管道，**当** `InfoVerificationGate` 配置 `auto_confirm=True`，**那么** 管道自动通过校验门，不暂停，行为与现有全量管道一致

---

## 技术计划

### 技术选型

- **语言/版本**：Python 3.11+（与项目一致）
- **框架**：LangGraph StateGraph（子图拆分）、Streamlit（GUI）
- **无新依赖**：全部复用现有技术栈

### 改动范围

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `core/nodes/info_verification_gate.py` | **新建** | 校验门节点 + `apply_user_corrections()` |
| `core/state.py` | 修改 | 新增 3 个 state 字段 + `ALL_NODES` 插入 |
| `core/graph.py` | 修改 | 拆分 `build_extraction_graph()` + `build_generation_graph()`，保留 `build_graph()` 向后兼容 |
| `core/nodes/contract_extractor.py` | 修改 | `_merge_contract_sources()` 补全 `user_confirmed_fields` 参数 |
| `gui/components/info_verification.py` | **新建** | 校验表单组件 |
| `gui/panels/upload_panel.py` | 修改 | 按钮文案 + 启动 Phase 1 |
| `gui/panels/review_panel.py` | 修改 | 两阶段执行逻辑 |
| `gui/components/reset.py` | 修改 | 清除新增的 session_state key |
| `specs/001-bid-agent/contracts/node_interfaces.md` | 修改 | 新增节点接口契约 |
| `specs/001-bid-agent/contracts/gui_api.md` | 修改 | 新增校验面板接口 |
| `tests/unit/test_info_verification_gate.py` | **新建** | 单元测试 |
| `tests/integration/test_two_phase_pipeline.py` | **新建** | 集成测试 |

### 向后兼容性

- `build_graph()` 保留为全量管道入口，`InfoVerificationGate` 默认 `auto_confirm=True`
- 现有的 `run_pipeline()` 和 `run_pipeline_with_snapshots()` 无需修改，行为不变
- 无头模式（CI/批处理）不受影响
- 现有测试全部通过（`InfoVerificationGate` 在 `build_graph` 中自动通过）

---

## 边界情况处理

| 场景 | 处理方式 |
|------|---------|
| 文件解析全部失败 | `DocumentParser` 返回 FAILED → `route_after_parser` 直接 END，不进入提取 |
| LLM 提取返回空 JSON | `ReqExtractor` 已有正则兜底 + 重试机制，提取结果可能部分为空 → 校验表单中标红提示用户手动填写 |
| 用户未修改任何字段直接确认 | 直接使用 LLM 提取结果，行为与当前管道一致 |
| 用户修改了部分字段 | 仅修改的字段以最高优先级覆盖，未修改的保持 LLM 提取值 |
| 用户在校验页面点「返回」重新上传 | 清除 `extraction_result` session_state，回到上传面板 |
| 必填字段（project_name, bidder_name）缺失 | 校验表单中标红警告，确认按钮禁用直到用户填写 |
| 多文件上传时信息冲突 | 以第一个成功解析的文件为准，校验表单中标注「多文件，已取首个」 |
| 无头模式 | `auto_confirm=True`，管道不暂停，行为与现有全量管道一致 |

---

## 实施任务分解（参考）

```
- [ ] T001 [P] [US#6] 创建 InfoVerificationGate 节点
  - 文件：bid-agent/core/nodes/info_verification_gate.py
  - 验证：test_info_verification_gate.py — pending/confirmed 状态切换 + auto_confirm
  - 耗时：4
  - 依赖：无

- [ ] T002 扩展 AgentState
  - 文件：bid-agent/core/state.py
  - 验证：test_state.py — 新字段默认值 + factory_state 初始化
  - 耗时：2
  - 依赖：T001

- [ ] T003 拆分管道图
  - 文件：bid-agent/core/graph.py
  - 验证：test_graph.py — build_extraction_graph / build_generation_graph / build_graph 三种模式
  - 耗时：4
  - 依赖：T001, T002

- [ ] T004 [P] 改造 ContractExtractor 四方合并
  - 文件：bid-agent/core/nodes/contract_extractor.py
  - 验证：test_contract_extractor.py — user_confirmed_fields 覆盖优先级
  - 耗时：3
  - 依赖：T002

- [ ] T005 [P] 创建校验表单组件
  - 文件：bid-agent/gui/components/info_verification.py
  - 验证：手动验证 Streamlit UI 渲染
  - 耗时：4
  - 依赖：T001

- [ ] T006 改造上传面板 + 审阅面板两阶段执行
  - 文件：bid-agent/gui/panels/upload_panel.py, review_panel.py
  - 验证：手动验证完整流程
  - 耗时：4
  - 依赖：T003, T005

- [ ] T007 更新 reset.py 清除新增 session_state
  - 文件：bid-agent/gui/components/reset.py
  - 验证：重置后状态干净
  - 耗时：1
  - 依赖：T006

- [ ] T008 集成测试：两阶段管道 E2E
  - 文件：tests/integration/test_two_phase_pipeline.py
  - 验证：Phase1 → 校验 → Phase2 完整流程
  - 耗时：4
  - 依赖：T003, T004

- [ ] T009 更新接口契约文档
  - 文件：specs/001-bid-agent/contracts/node_interfaces.md, gui_api.md
  - 验证：文档与代码一致
  - 耗时：2
  - 依赖：T001, T005
```

---

## 总结

| 维度 | 评估 |
|------|------|
| **可行性** | ✅ 完全可行，与现有架构高度契合 |
| **改动量** | 中等（1 个新节点 + 1 个新组件 + 3 个文件修改 + 管道拆分） |
| **风险** | 低（向后兼容，无头模式不受影响，现有测试不破坏） |
| **收益** | 高（在 LLM 生成前拦截提取错误，节省 token + 时间 + 修正成本） |
| **新依赖** | 无 |
| **与现有 HumanReviewGate 的关系** | 互补——前门拦截提取错误，后门拦截生成质量 |
