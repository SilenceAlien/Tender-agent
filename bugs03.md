# 全流程功能测试报告（第二轮回归）— bugs03.md

> 测试人：端测测（Web 应用测试专家）
> 测试日期：2026-07-11（第二轮）
> 测试范围：N01-N14 修复验证 + 全流程功能回归 + 新问题探测
> 测试方法：动态功能测试（mock LLM + 真实 PDF）+ 静态代码审查 + pytest 套件 + Streamlit UI 冒烟
> 测试环境：Python 3.13.12 / venv / macOS / bid-agent/.venv
> 仅记录问题，不做修复

---

## 一、测试概况

| 维度 | 结果 |
|------|------|
| pytest 套件 | **507 通过 / 0 失败**（17.9s） |
| Streamlit UI 冒烟 | **HTTP 200**，四面板正常加载，节点级模型配置 UI 已出现 |
| 动态功能测试（mock LLM + 真实 PDF） | 覆盖 13 节点 + E2E + 14 项修复点验证 |
| N01-N14 修复验证 | **12/14 已确认修复**，1 项修复不彻底，1 项仅文档化 |
| 新发现问题 | **2 个**（R01 高优先级 / R02 中优先级） |

---

## 二、N01-N14 修复验证结果

| 编号 | 状态 | 验证方式 | 说明 |
|------|:--:|------|------|
| N01 | ✅ 已修复 | 动态: run_pipeline 返回 export_path 非空 + 文件存在(37441 bytes) | `AgentState` 已加 `export_path: str`，`factory_state` 默认 `""`，headless 管道返回路径 `/data/exports/20260711_175246_标书_v0.docx` |
| N02 | ⚠️ 仅文档化 | 静态: graph.py docstring + review_panel 源码 | `build_generation_graph` 的 HumanReviewGate 仍无 auto_approve（by design），但 review_panel 未实现 `resume_after_review` 调用 → approved 路径是死代码。导出仍靠 export_panel 按钮。**遗留问题 → R02** |
| N03 | ✅ 已修复 | 静态: DS_MODELS = ['deepseek-chat', 'deepseek-reasoner'] | 模型名改为 DeepSeek 官方真实模型名 |
| N04 | ✅ 已修复 | 静态: config_panel 新增"节点级模型配置"expander | 4 个节点（SectionGenerator/QualityChecker/ScoreSimulator/ReqExtractor）可选模型，存为 `{"provider":..., "model":...}` 格式 |
| N05 | ✅ 已修复 | 动态: 契约禁止机构"复旦大学"被检测为 CRITICAL | 新增 `_check_contract_deviation()`，读取 `project_contract.forbidden_institutions` |
| N06 | ✅ 已修复 | 动态: 响应时间(30 vs 60) + 到达现场(2 vs 4) 矛盾均被检测 | 新增 `_check_tech_parameter_consistency()`，6 种技术参数 pattern |
| N07 | ✅ 已修复 | 动态: format_rules 缺 page_margin/font/line_spacing 报警 + 缺 seal_requirement 报警 | `_check_format_compliance` 现检查 format_rules 结构字段 |
| N08 | ✅ 已修复 | 静态: 三处命名完全一致（7 种类型） | upload_panel 移除"软件类"，TEMPLATE_TYPES 全部带"类"且含"劳务管理服务类"，与 knowledge_base 目录一致 |
| N09 | ✅ 已修复 | 动态: "劳务管理服务方案" 分词为 7 tokens，预测得分 24/40 | 新增 `_tokenize_cn()` 中文 2-gram 分词 |
| N10 | ✅ 已修复 | 动态: 无 sections 时返回 `pending`（不再 reject） | human_review_gate 无 sections 时返回 pending + 注释说明 |
| N11 | ❌ 修复不彻底 | 静态 + 动态: provider 仍恒为 deepseek | `config_panel.py:128` 的推导逻辑 `"deepseek" if ds_model else ...` 中 ds_model 恒非空（selectbox 值），provider 永远是 deepseek。**新问题 → R01** |
| N12 | ✅ 已修复 | 静态: upload_panel `index=0`（默认"服务类"） | 默认标书类型改为"服务类" |
| N13 | ✅ 已修复 | 静态: graph.py docstring 改为 "14-node pipeline" | 模块注释已更新 |
| N14 | ✅ 已修复 | 静态: PRD 多处更新为 "14 节点" | PRD 3.3、9.3 等章节均已同步 |

**结论**：14 项中 **12 项已确认修复**，N02 仅文档化处理（遗留 R02），N11 修复不彻底（引发 R01）。

---

## 三、新发现问题

### R01: config_provider 恒为 deepseek — 只配 OpenAI 的用户节点全部 fallback mock 🔴

- **文件**：`gui/panels/config_panel.py`（第 128 行）
- **严重程度**：高
- **类型**：配置缺陷 / 功能不可用（N11 修复不彻底）
- **关联**：N11

**问题描述**：

N11 的修复代码：
```python
st.session_state["config_provider"] = "deepseek" if ds_model else st.session_state.get("config_provider", "deepseek")
```

`ds_model` 是 DeepSeek 模型 selectbox 的值，**恒非空**（至少是默认值 `deepseek-chat`）。因此三元表达式永远走 `"deepseek"` 分支，`config_provider` 恒为 `"deepseek"`，无论用户实际配置了哪个 provider。

**连锁影响**（已验证 `get_model_for_node` + `pipeline_runner` 链路）：

1. `config_persistence.py:99` 的 fallback 逻辑：
   ```python
   return {"provider": config["provider"], "model": config["model"]}
   # → {"provider": "deepseek", "model": "deepseek-chat"}
   ```
2. 对未设 node_override 的节点，`get_model_for_node` 返回 `{provider: "deepseek", ...}`
3. `pipeline_runner.py:93`：
   ```python
   if provider in api_keys and api_keys[provider]:
   # "deepseek" not in {"openai": "sk-..."} → False
   ```
4. 结果：`llm_fns[node] = None`（mock）

**影响场景**：用户只配置 OpenAI API Key（未配 DeepSeek），且未为每个节点单独设 node_override 时，**全部 6 个 LLM 节点都用 mock**，系统完全无法使用 LLM 能力。用户必须为每个节点单独配置 override 指向 openai 才能用上 OpenAI——这违背了"全局配置 + 可选节点级覆盖"的设计意图。

**复现**（已验证）：
```
config = {
    "provider": "deepseek",       # 恒定
    "model": "deepseek-chat",     # ds_model 默认值
    "api_keys": {"openai": "sk-test"},  # 只配了 OpenAI
    "node_overrides": {},         # 未设节点级覆盖
}
get_model_for_node(config, "QualityChecker")
  → {"provider": "deepseek", "model": "deepseek-chat"}  # fallback
# pipeline_runner: "deepseek" not in api_keys → llm_fns["QualityChecker"] = None (mock)
```

**修复建议**：`config_provider` 应根据用户实际填入的 API Key 推导（如只填了 openai key → provider=openai），或让用户显式选择"主用 provider"。不应依赖 ds_model 是否为空。

---

### R02: 交互模式 approved 路径无触发方式 — review_panel 缺 resume_after_review 调用 🟡

- **文件**：`gui/panels/review_panel.py` + `core/graph.py`（`build_generation_graph`）
- **严重程度**：中
- **类型**：架构不一致 / 死代码（N02 仅文档化，未实现 resume）
- **关联**：N02

**问题描述**：

N02 的修复方式是**文档化设计决策**：graph.py docstring 说明"交互模式 HumanReviewGate 无 auto_approve，by design 暂停在 pending，导出由 export_panel 处理"。同时 `build_generation_graph` 的条件路由声明了 `approved → DocumentAssembler`。

但 `review_panel.py` **没有调用 `resume_after_review()`** 来注入用户的 approved/rejected 裁决并重新 invoke 生成图。因此：

- 用户在 review_panel 点"通过"后，没有代码把 `review_status` 设为 `approved` 并 resume 管道
- `approved → DocumentAssembler` 这条路由**从未被触发**，是死代码
- 导出仍完全依赖 export_panel 的"导出 DOCX"按钮（直接调用 `doc_assembler()`，绕过管道）

**验证**：
```
grep "resume_after_review" gui/panels/review_panel.py → 无匹配
review_panel.py:237 仍预设 review_status = "pending"
```

**影响**：
1. graph.py 声明的 approved 路由是死代码，误导开发者认为管道能自动导出
2. 交互模式下 DocumentAssembler 节点在管道中永不执行，与 PRD 3.3 描述的管道流程不一致
3. 若未来需要在导出前自动执行某些管道节点（如最终合规检查），当前架构无法支持

**修复建议**（二选一）：
- **方案 A**：在 review_panel 实现 resume 逻辑——用户点"通过"后调用 `resume_after_review(state, "approved")` + 重新 invoke `build_generation_graph` 到 DocumentAssembler
- **方案 B**：如果确认交互模式就是靠 export_panel 导出，则移除 graph.py 中 `approved → DocumentAssembler` 的路由声明，避免死代码误导

---

## 四、测试盲区与补充说明

### 本轮已验证的修复点

- **N01 动态验证**：`run_pipeline()` 返回的 `export_path` 非空，`data/exports/` 下文件存在（37441 bytes DOCX）
- **N05 动态验证**：契约禁止机构"复旦大学"被检测为 `contract_forbidden_institution`（severity: critical）
- **N06 动态验证**：响应时间（30 vs 60 秒）和到达现场（2 vs 4 小时）矛盾均被检测
- **N07 动态验证**：format_rules 缺 `page_margin`/`font`/`line_spacing` 时报"格式规则缺失"；缺 `seal_requirement` 时报"签章要求未声明"
- **N09 动态验证**："劳务管理服务方案"被分为 7 个 2-gram tokens，启发式评分预测 24/40（之前为 0）
- **N10 动态验证**：无 sections 时 HumanReviewGate 返回 `pending`（不再 reject）

### 仍存在的测试盲区

| 盲区 | 说明 |
|------|------|
| 真实 LLM 调用 | 本轮仍用 mock LLM，R01 的连锁影响（OpenAI 用户 fallback mock）是基于代码逻辑推导，未用真实 OpenAI Key 验证 |
| 交互模式 E2E | 无自动化测试覆盖 `build_generation_graph` 的完整交互流程（R02 的 approved 路径死代码因此未被发现） |
| FAISS 持久化 | BUG-04（save/load 向量丢失）、BUG-11（HNSW reconstruct）仍未验证 |
| evolution 模块 | BUG-06（mark_used off-by-one）、BUG-07（avg_revision_rounds）仍未验证 |
| 性能基准 | PRD 要求"初稿生成 <10 分钟""文档解析 <30 秒"，本轮未测（mock LLM 无意义） |

### 误报澄清

- 测试脚本初版报"PDF 解析失败: 无内容"（R-NEW-11），经核实是**测试脚本字段名误用**：`doc_parser` 返回 `raw_text`/`parsed_content` 字段，而非 `content`。PDF 解析实际正常（raw_text 含完整文本）。**非产品 bug，已排除。**

---

## 五、修复优先级建议

| 优先级 | 编号 | 原因 |
|:--:|------|------|
| P0（立即） | R01 | 只配 OpenAI 的用户全部节点 fallback mock，系统不可用 |
| P2（中） | R02 | 交互模式 approved 路径死代码，架构不一致 |

---

## 六、与上轮测试对比

| 维度 | 第一轮（bugs.md N01-N14） | 第二轮（bugs03.md R01-R02） |
|------|------|------|
| 发现问题数 | 14 个 | 2 个 |
| 高优先级 | 3 个（N01/N02/N03） | 1 个（R01） |
| pytest | 507 通过 | 507 通过 |
| 修复确认 | — | 12/14 已修复 |
| 测试方法 | mock LLM + 真实 PDF | 同 + 14 项修复点专项验证 |

**结论**：经过一轮修复，14 个问题中 12 个已确认修复，新发现 2 个问题（其中 R01 是 N11 修复不彻底引发的连锁缺陷）。整体质量显著提升，剩余问题集中在**多模型配置的 provider 推导逻辑**和**交互模式管道完整性**两个点。
