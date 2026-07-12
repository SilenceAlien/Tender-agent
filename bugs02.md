# 全流程功能测试 — 新发现问题（N01 ~ N14）

> 来源：`bugs.md` → 全流程功能测试报告（2026-07-11）→ 第三节「新发现问题」
> 测试人：端测测（Web 应用测试专家）
> 测试日期：2026-07-11
> 提取说明：本文件独立收录本次动态功能测试（mock LLM + 真实 PDF）发现的 14 个问题，**仅记录不修复**。

---

## 三、新发现问题（N01 ~ N14）

### N01: headless 管道 export_path 丢失 — 文件生成了但调用方拿不到路径 🔴

- **文件**：`core/state.py`（AgentState 定义）+ `core/nodes/doc_assembler.py`
- **严重程度**：高
- **类型**：数据丢失 / 状态管理缺陷

**问题描述**：
`doc_assembler` 节点返回 `{"export_path": result_path, ...}`，但 `export_path` **未在 `AgentState` TypedDict 中定义**。LangGraph 的 StateGraph 以 TypedDict 为 schema，未定义字段会在状态合并时被**静默丢弃**。

**复现**（已验证）：
```
result = run_pipeline(state, llm_fns=...)  # headless 模式
result.get("export_path")  → ''  (空!)
# 但 data/exports/ 目录下实际生成了 DOCX 文件
# DocumentAssembler node_status = "completed"
```

**影响**：任何通过 `run_pipeline()` / `build_graph()` 运行的 headless 管道（CI/批处理/API 调用），即使 DocumentAssembler 成功生成 DOCX，调用方也无法从返回的 state 中获取文件路径。GUI 模式因 export_panel 直接调用 `doc_assembler()` 不受影响。

**修复建议**：在 `AgentState` TypedDict 中添加 `export_path: str` 字段，并在 `factory_state()` 默认值中初始化为 `""`。

---

### N02: 交互模式 DocumentAssembler 永不执行 — 导出完全靠 UI 按钮 🔴

- **文件**：`core/graph.py`（`build_generation_graph()`）+ `gui/panels/review_panel.py`
- **严重程度**：高
- **类型**：架构不一致 / 死代码

**问题描述**：
`build_generation_graph()`（交互模式）注册 HumanReviewGate 时**未设 `auto_approve=True`**（graph.py:438）。review_panel Phase 2 预设 `review_status="pending"`（review_panel.py:237），HumanReviewGate 返回 pending，`route_after_review` 返回 `"__end__"`，管道终止——**DocumentAssembler 从不执行**。

管道设计（PRD 3.3）：ScoreSimulator → HumanReviewGate → [approved] → DocumentAssembler。但交互模式下 approved 路径无触发方式，DocumentAssembler 是死代码。导出完全依赖 export_panel 的"导出 DOCX"按钮（直接调用 doc_assembler）。

**影响**：交互模式下管道的"文档装配"节点形同虚设，与 PRD 描述的管道流程不一致。review_panel 没有处理 HumanReviewGate approved 后 resume 管道的逻辑。

**修复建议**：在 review_panel 中实现 HumanReviewGate 的 resume 逻辑（用户点"通过"后调用 `resume_after_review` + 重新 invoke 生成图到 DocumentAssembler），或在交互模式也接受 auto_approve 后由 export_panel 导出（当前实际行为，需文档化）。

---

### N03: config_panel DeepSeek 模型名虚构 — API 调用会失败 🔴

- **文件**：`gui/panels/config_panel.py`（第 9-12 行）
- **严重程度**：高
- **类型**：配置错误

**问题描述**：
```python
DS_MODELS = [
    {"id": "deepseek-v4-pro", ...},
    {"id": "deepseek-v4-flash", ...},
]
```
DeepSeek 官方 API 的实际模型名是 `deepseek-chat` 和 `deepseek-reasoner`。`deepseek-v4-pro` / `deepseek-v4-flash` **不存在**。用户配置后，任何 LLM 调用都会因模型名无效而失败。

**影响**：DeepSeek 用户无法正常使用系统（除非手动改模型名，但 UI 不允许自定义输入）。

**修复建议**：改为 `deepseek-chat`（V3）和 `deepseek-reasoner`（R1），或提供自定义模型名输入框。

---

### N04: config_panel 缺节点级模型配置 UI 🟡

- **文件**：`gui/panels/config_panel.py`
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.6 验收标准 2）

**问题描述**：
PRD 4.2.6 验收标准 2 要求"为不同节点配置不同模型（如生成用 GPT-4o、质检用 Claude）"。代码中有 `config_node_overrides` session state 的读取/保存（config_panel.py:30,42），但**配置面板 UI 中没有任何控件让用户为不同节点选择模型**。

**影响**：节点级模型配置功能不可用，用户只能全局选一个模型。

**修复建议**：在配置面板添加节点级模型选择 UI（如为 SectionGenerator/QualityChecker/ScoreSimulator 分别选择模型）。

---

### N05: CrossReferenceChecker 缺契约偏离校验 🟡

- **文件**：`core/nodes/cross_reference_checker.py`
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.2 验收标准 2）

**问题描述**：
PRD 4.2.2 验收标准 2："给定某章出现契约禁止的机构名（如"复旦大学"出现在非复旦项目中），标记为 CRITICAL: contract_forbidden_institution"。

但 `cross_reference_checker` **完全不读取 `project_contract`**，只检查人员/金额/日期/文档组成。契约禁止项检测（forbidden_institutions / forbidden_industries）未实现。

**复现**（已验证）：
```
sections = {"服务方案": "我们与复旦大学合作多年..."}
project_contract = {"forbidden_institutions": ["复旦大学"]}
# cross_reference_checker 返回 inconsistencies=[] — 未检测到契约偏离
```

**影响**：拼凑残留（其他项目的机构名错误出现）无法被自动检测，可能被评审专家发现导致扣分。

**修复建议**：新增 `_check_contract_deviation()` 函数，读取 `project_contract.forbidden_institutions` 并扫描各章节。

---

### N06: CrossReferenceChecker 缺技术参数一致性检查 🟡

- **文件**：`core/nodes/cross_reference_checker.py`
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.2）

**问题描述**：
PRD 4.2.2 要求"检查技术参数跨章节一致（技术方案章 vs 服务方案章）"。代码只有 `_check_personnel_consistency` / `_check_amount_consistency` / `_check_date_consistency` / `_check_document_composition_consistency`，**没有技术参数一致性检查**。

**复现**（已验证）：
```
sections = {"技术方案章": "响应时间30秒", "服务方案章": "响应时间60秒"}
# cross_reference_checker 返回 inconsistencies=[] — 未检测到参数矛盾
```

**影响**：技术参数跨章矛盾（如响应时间、服务时长）不会被检测。

---

### N07: ComplianceChecker 格式合规不检查 format_rules 🟡

- **文件**：`core/nodes/compliance_checker.py`（`_check_format_compliance` 第 58-79 行）
- **严重程度**：中
- **类型**：功能缺失（PRD 4.2.3 验收标准 2）

**问题描述**：
`_check_format_compliance(sections, format_rules)` 接收 `format_rules` 参数但**完全不使用它**，只检查每章内容长度是否 ≥100 字。PRD 4.2.3 验收标准 2 要求"检查页边距、字体、行距、目录格式"。

代码注释承认"无法完全验证页边距/字体（需渲染 DOCX）"，但连 format_rules 中可检查的结构规则（如是否声明了页边距）都没检查。

**影响**：格式合规检查名不副实，仅检查内容长度。

---

### N08: 标书类型命名三处不一致 🟡

- **文件**：`gui/panels/upload_panel.py` + `core/nodes/template_matcher.py` + `knowledge_base/`
- **严重程度**：中
- **类型**：命名不一致 / 数据缺失

**问题描述**：
标书类型在三个地方命名不一致：

| 位置 | 命名 | 缺失 |
|------|------|------|
| upload_panel（用户选择） | `服务类`、`货物类`、`软件类`、`工程类`、`集成类`、`运维类`、`劳务管理服务类`、`劳务外包类`（8种，带"类"） | — |
| template_matcher.TEMPLATE_TYPES | `服务`、`货物`、`软件`、`工程`、`集成`、`运维`、`劳务外包`（7种，**不带"类"**） | 缺"劳务管理服务类" |
| knowledge_base 目录 | `服务类`、`货物类`、`工程类`、`集成类`、`运维类`、`劳务外包类`、`劳务管理服务类`（7种，带"类"） | **缺"软件类"目录** |

**影响**：
1. "软件类"在 UI 可选但知识库无对应目录，选了会检索失败
2. TEMPLATE_TYPES 命名不带"类"与知识库目录名不匹配，可能导致模板索引构建/检索错位
3. "劳务管理服务类"不在 TEMPLATE_TYPES 中，但知识库有目录

**修复建议**：统一命名规范（建议全部带"类"），补齐软件类知识库或从 UI 移除。

---

### N09: ScoreSimulator 启发式分词对中文无效 🟡

- **文件**：`core/nodes/score_simulator.py`（`_heuristic_score` 第 108 行）
- **严重程度**：中
- **类型**：逻辑缺陷

**问题描述**：
```python
tokens = [t for t in name_lower.replace("，", " ").split() if len(t) >= 2]
```
`split()` 按空白分词，但中文评分项名称（如"劳务管理服务方案"）**没有空格**，split 后整个名称是一个 token。当该名称未在 corpus 中完整出现时，`hits = sum(1 for t in tokens if t in corpus)` 只检查整个长名称，几乎不会命中。

**复现**（已验证）：
```
scoring item = "劳务管理服务方案", sections = "完全不相关的内容"
# predicted = 0, gap = 40 — 因为中文无法分词，token fallback 失效
```

**影响**：无 LLM 时，启发式评分对中文评分项几乎无效，预测得分偏低。

**修复建议**：用 jieba 分词或 n-gram 切分中文评分项名称。

---

### N10: HumanReviewGate 无 sections 时自动 reject 🟢

- **文件**：`core/nodes/human_review_gate.py`（第 64-73 行）
- **严重程度**：低
- **类型**：设计不一致

**问题描述**：
无 sections 时自动返回 `rejected`。但 PRD 设计 HumanReviewGate 的三态是 pending/approved/rejected，由人工决定。自动 reject 不符合"等待人工"的设计意图，且 rejected 会路由到 FeedbackProcessor 触发修订（但无内容可修订）。

**影响**：边缘场景，实际运行中无 sections 的情况罕见。

---

### N11: config_panel provider 硬编码 deepseek 🟢

- **文件**：`gui/panels/config_panel.py`（第 125 行）
- **严重程度**：低
- **类型**：硬编码

**问题描述**：
```python
st.session_state["config_provider"] = "deepseek"  # 硬编码
```
即使用户配置 OpenAI，provider 始终是 deepseek。虽然 pipeline_runner 按节点配置模型不受影响，但 config_provider 字段语义错误。

---

### N12: upload_panel 默认标书类型不合理 🟢

- **文件**：`gui/panels/upload_panel.py`（第 17 行）
- **严重程度**：低
- **类型**：UX 缺陷

**问题描述**：
`index=7` 默认选中"劳务外包类"。最常见的标书类型应是"服务类"（index=0），默认选第 8 项不符合常规预期。

---

### N13: graph.py 模块注释"7-node"过时 🟢

- **文件**：`core/graph.py`（第 1 行）
- **严重程度**：低
- **类型**：文档过时

**问题描述**：
模块 docstring 写"the 7-node pipeline"，实际已扩展到 14 节点（含 InfoVerificationGate）。

---

### N14: PRD 说 13 节点但代码 14 节点 🟢

- **文件**：`PRD_标书制作智能体.md`（3.3 节）vs `core/state.py`（ALL_NODES）
- **严重程度**：低
- **类型**：文档不一致

**问题描述**：
PRD 3.3 说"13 节点状态机"，但 `ALL_NODES` 实际有 14 个（多了 `InfoVerificationGate`，US#6 新增）。test_state.py 的 `test_exactly_fourteen_nodes` 断言是 14。PRD 未同步更新。

---

## 附：修复优先级建议

| 优先级 | 编号 | 原因 |
|:--:|------|------|
| P0（立即） | N01, N03 | 导出路径丢失 / 模型名无效导致功能不可用 |
| P1（高） | N02 | 架构不一致，交互模式管道不完整 |
| P2（中） | N04, N05, N06, N07, N08, N09 | 功能缺失 / PRD 验收标准未满足 |
| P3（低） | N10, N11, N12, N13, N14 | 边缘场景 / UX / 文档 |
