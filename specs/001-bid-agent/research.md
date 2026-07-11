# 技术研究 — 标书制作智能体

## 探索过程

### 研究 1：LangGraph vs 其他编排框架（2026-06-28）

**问题**：用什么框架编排 7 节点的条件路由和循环微调？

**候选**：
| 框架 | 条件路由支持 | 循环支持 | Python 原生 | 学习曲线 |
|------|------------|---------|------------|---------|
| LangGraph | ✅ StateGraph + conditional_edges | ✅ Command/goto | ✅ | 中 |
| Prefect | ❌ DAG only | ❌ | ✅ | 高 |
| CrewAI | ❌ 隐式编排 | ❌ | ✅ | 低 |
| 自建状态机 | ✅ 完全可控 | ✅ | ✅ | 高（开发量） |

**结论**：LangGraph。StateGraph 的条件边天然匹配 QualityChecker FAIL → FeedbackProcessor → SectionGenerator 回路。且 subgraph 支持章节并行生成，这是自建状态机难以快速实现的。

**行动**：Phase 1 使用 LangGraph StateGraph，暂不需要 LangGraph Cloud/Studio。

---

### 研究 2：FAISS 索引类型选择（2026-06-28）

**问题**：三种检索场景（模板库、历史库、知识库）应该用哪些 FAISS 索引类型？

**决策矩阵**：
| 场景 | 数据量 | 精度要求 | 存储限制 | 更新频率 | 选型 |
|------|--------|---------|---------|---------|------|
| 模板库 | ~10^3 | 100% 召回 | 不敏感 | 构建后不变 | IndexHNSWFlat (M=32) |
| 历史库 | ~10^5-10^6 | 高召回 | 需要压缩 | 定期增量 | IndexIVFPQ (nlist=256, m=48) |
| 知识库 | ~10^3-10^4 | 不可降精度 | 不敏感 | 偶尔更新 | IndexHNSWFlat (M=16) |

**关键细节**：
- 历史库使用 Parent Document Retrieval：段落级 chunk 做向量匹配，拉回完整章节做上下文
- HNSW 的 M 参数权衡：M=32 精度高但内存大（适合小规模模板库），M=16 适合知识库（数据少，不需要太高 M）
- IVFPQ 的 m 参数：m=48 表示 1536 维被分成 32 个子向量，每个压缩到 48 字节

**行动**：Phase 1 先建模板库单索引（IndexHNSWFlat），验证检索管线后再建历史库。

---

### 研究 3：Embedding 模型调研（2026-06-28）

**问题**：Phase 1 用什么 Embedding 模型？

**候选**：
| 模型 | 维度 | 中文 MTEB | 成本 | 离线可用 |
|------|------|----------|------|---------|
| OpenAI text-embedding-3-small | 1536 | 良好 | $0.02/1M tokens | ❌ 需 API |
| BGE-large-zh-v1.5 | 1024 | 优秀 | 免费 | ✅ 本地 |
| BGE-M3 | 1024 | 优秀（多语言） | 免费 | ✅ 本地 |
| text2vec-large-chinese | 1024 | 良好 | 免费 | ✅ 本地 |

**结论**：Phase 1 用 OpenAI text-embedding-3-small。理由：Phase 1 仅调用一次建索引，成本可忽略（< $0.10）；无需额外模型部署。Phase 2/3 迁移到 BGE-M3 实现完全离线。

**行动**：core/retrieval/embeddings.py 用工厂模式封装，后续切换模型仅需改配置。

---

### 研究 4：DOCX 导出方案（2026-06-28）

**问题**：如何生成符合政府采购格式规范的 DOCX？

**候选**：
| 方案 | 格式控制 | 学习曲线 | 表格/目录支持 |
|------|---------|---------|-------------|
| python-docx | 精细（段落/字体/页边距） | 低 | ✅ 完善 |
| docx-js (Node) | 精细 | 低 | ✅ 完善 |
| pandoc (md→docx) | 粗糙 | 极低 | 有限 |
| LibreOffice UNO | 最精细 | 高 | ✅ 最完善 |

**结论**：python-docx。与项目同语言，API 足够控制政府采购格式的字体/页边距/标题层级/目录。对于复杂表格和自动目录，使用 python-docx 的 Sections/TableOfContents API。

**行动**：DocumentAssembler 节点使用 python-docx，导出前注入格式模板。

---

### 研究 5：Streamlit 多面板布局方案（2026-06-28）

**问题**：如何在一个 Streamlit 页面中实现四面板操作台？

**方案**：使用 `st.tabs()` 或 `st.sidebar` + 条件渲染。

**结论**：`st.sidebar` + 主区域分段渲染。侧边栏放面板导航（配置/上传/审阅/导出四个按钮），主区域根据选中面板渲染对应组件。不选择 `st.tabs()` 因为标签在顶部会占用大量纵向空间，与标书审阅的纵向滚动不兼容。

**行动**：gui/app.py 使用 `st.session_state["active_panel"]` 控制面板切换。

---

### 研究 6：LLM 供应商统一接口（2026-06-28）

**问题**：六家 LLM 供应商如何统一调用接口？

**方案**：基于 LangChain 的 `BaseChatModel` 抽象层。每家供应商一个工厂函数，返回 `BaseChatModel` 实例。`ModelRouter` 根据任务类型 + 用户配置选择对应的 ChatModel。

**关键约束**：API Key 从 `config.yaml` 或环境变量读取，不在代码中硬编码。API Key 仅在内存中 (st.session_state) 暂存，不持久化。

**行动**：core/llm/providers.py 实现六家工厂，core/llm/router.py 实现任务级路由。

---

## 技术风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| LangGraph 版本 API 变更 | 中 | 高 | 锁定版本号，Phase 1 不升级 |
| FAISS 索引构建失败（大文件） | 低 | 中 | 分批次构建 + 进度回调 |
| LLM 输出不稳定（同一 prompt 不同质量） | 高 | 中 | temperature 默认 0.3，保留 retry 机制 |
| 招标 PDF 解析失败（扫描件/加密） | 中 | 中 | 提示用户手动输入关键信息，不做 OCR |
| Streamlit 性能瓶颈（大文件上传） | 低 | 低 | Phase 1 不处理 > 200MB 的 PDF |
| 生成时间超 10 分钟 | 中 | 中 | subgraph 章节并行 + 流式输出进度 |
| 微调上下文膨胀导致风格漂移 | 高 | 高 | 分层上下文注入（见研究 7） |

---

### 研究 7：微调循环的上下文管理策略（2026-06-28）

**问题**：微调超过 3 轮后，如何避免上下文膨胀和全局约束丢失？

**候选方案**：

| 方案 | Token 开销 | 一致性 | 实现复杂度 |
|------|-----------|--------|-----------|
| 全量历史 | O(n²) — 每轮翻倍 | ✅ 最佳 | 低 |
| 滑动窗口（前3轮） | O(1) 固定 | ❌ 全局约束丢失 | 低 |
| **分层上下文注入** | O(1) 固定 | ✅ 全局约束保留 | 中 |

**结论**：采用**分层上下文注入**。原因：滑动窗口虽然 Token 开销恒定，但会丢失全局约束（如第1轮设置的「全文风格偏暖」在窗口外后被第4轮忽略），导致局部修改与全文风格不一致。分层上下文通过将全局约束提取到不可变层（Layer 1）、旧轮次压缩为摘要（Layer 2）、最近 N 轮完整保留（Layer 3），同时解决了 token 开销和一致性问题。

**三层架构**：

```
Layer 1: 全局约束摘要（~200 tokens，永不丢弃）
  → 从 requirements.format_rules + scope=global 的反馈中提取
  → 格式：「全文风格约束：专业但不冰冷」「数据单位约束：万元」
  → 注入方式：硬注入到每次 SectionGenerator 的 system prompt

Layer 2: 压缩历史（~300 tokens，超出窗口的旧轮次摘要）
  → 超过 context_window_size 的轮次用 LLM 压缩为摘要
  → 格式：「第1轮[风格]→全文语气偏暖」「第2轮[内容]→团队配置补充张三年限」
  → 压缩 prompt 约束：「保留所有『禁止』和『必须』类约束的原文，不得改写」

Layer 3: 活跃窗口（~1500 tokens，最近 N 轮完整保留）
  → 完整反馈文本 + diff_before + diff_after + 上下文段落
  → 默认 window_size=3
```

**关键实现点**：
- FeedbackProcessor 在解析反馈时标注 `scope: "global" | "local"`
- scope=global 的反馈直接追加到 `global_constraints`，参与 Layer 1 构建
- scope=local 的反馈走正常 feedback_history → Layer 2/3 窗口处理
- 压缩 Layer 2 时调用一次轻量 LLM（temperature=0.1，max_tokens=400），只在窗口滑动时触发

**安全考虑（Prompt Guard 视角）**：
- Layer 1 为系统级注入，不可被用户反馈覆盖或篡改
- 如果用户反馈包含「忽略之前所有约束」，需先由 FeedbackProcessor 判断是新的全局约束（替换旧的）还是注入尝试（拦截）
- Layer 2 压缩存在摘要偏差风险——LLM 压缩时可能曲解原意。缓解：压缩 prompt 明确要求「逐条保留禁止/必须类约束原文」

**行动**：AgentState 新增 `global_constraints`、`compressed_history`、`context_window_size` 字段。SectionGenerator 的 prompt 构建逻辑改为三层组装。

---

### 研究 8：跨 Agent / 跨环境的上下文互通共享（2026-06-28）

**问题**：多个开发 Agent 并行工作时（如一个 Agent 写 SectionGenerator，另一个写 QualityChecker），如何让它们共享标书生成的完整上下文？用户在会话 1 生成了标书初稿，会话 2 如何无缝继续微调？

**核心挑战**：

```
Agent A（SectionGenerator）        Agent B（QualityChecker）
        │                                    │
        ├─ sections 生成完成                  ├─ 需要读取 sections
        ├─ feedback_history[0..2]            ├─ 需要读取 feedback_history
        ├─ global_constraints                ├─ 需要读取 global_constraints
        │                                    │
        └──────── 如何共享 State？ ──────────┘
```

**方案对比**：

| 方案 | 跨 Agent | 跨会话 | 跨环境 | 实现复杂度 |
|------|---------|--------|--------|-----------|
| 内存传递（Python dict） | ❌ 同进程 | ❌ 进程退出即丢失 | ❌ | 极低 |
| LangGraph Checkpointer | ✅ 自动 | ✅ 线程级 | ❌ 单机 | 低 |
| SQLite（本项目已选型） | ✅ 读写 | ✅ 持久 | ✅ 文件即数据库 | 低 |
| Redis/外部缓存 | ✅ 读写 | ✅ 持久 | ✅ 网络可达 | 中 |
| 文件序列化（JSON） | ⚠️ 手动 | ✅ 持久 | ✅ 拷贝文件 | 极低 |

**结论**：采用 **SQLite + AgentState JSON 序列化** 作为上下文总线的方案。理由：
- SQLite 已在本项目选型，零额外依赖
- `projects.agent_state_json` 字段天然承载完整 AgentState 的序列化/反序列化
- 文件即数据库的特性让跨环境共享只需拷贝一个 `.db` 文件
- LangGraph 的 `SqliteSaver` Checkpointer 与 SQLite 深度集成，自动处理节点间的状态快照

**三层共享架构**：

```
┌─────────────────────────────────────────────────────────┐
│ Layer A: LangGraph SqliteSaver（运行时快照）             │
│ - 每个节点执行前后自动保存 AgentState 快照               │
│ - 支持从任意快照点恢复执行                               │
│ - 同一进程内多 Agent 通过 graph.get_state(thread_id) 读取│
├─────────────────────────────────────────────────────────┤
│ Layer B: SQLite agent_state_json（持久化序列化）         │
│ - projects 表存储完整 AgentState JSON                    │
│ - 跨会话：新会话读取 project_id → 反序列化 → 恢复状态   │
│ - 跨 Agent：任何 Agent 通过 DAO 读取/写入                │
├─────────────────────────────────────────────────────────┤
│ Layer C: 文件系统状态文件（环境间传输）                   │
│ - data/{project_id}/state.json（可选，轻量导出）         │
│ - 跨环境：拷贝 .db 文件或 state.json 到目标环境          │
│ - 跨团队：Git LFS 或共享网盘存储状态文件                  │
└─────────────────────────────────────────────────────────┘
```

**关键实现细节**：

1. **AgentState 序列化契约**（已在 contracts/agent_state.md 定义）
   - 所有字段必须 JSON 可序列化（dict/list/str/int/float/bool/None）
   - 枚举值序列化时转字符串（`"PASS"` 而非 `<NodeStatus.PASS>`）
   - `sections` 可能很大（~20,000 字 × 8 章），考虑 gzip 压缩后存 BLOB

2. **LangGraph SqliteSaver 使用方式**：

```python
from langgraph.checkpoint.sqlite import SqliteSaver

# 初始化（共享同一个 .db 文件）
checkpointer = SqliteSaver.from_conn_string("data/bid_agent.db")

# 编译 graph 时绑定 checkpointer
graph = builder.compile(checkpointer=checkpointer)

# 所有 Agent 通过同一 thread_id 共享上下文
# Agent A 生成标书：
config_a = {"configurable": {"thread_id": "project_abc123"}}
graph.invoke(initial_state, config_a)

# Agent B 读取同样的状态：
state_b = graph.get_state(config_a)
# state_b.values["sections"] → Agent A 生成的章节内容
```

3. **跨会话恢复流程**：

```python
def restore_session(project_id: str) -> AgentState:
    """任何新会话/新 Agent 的入口——从 SQLite 恢复完整上下文"""
    conn = sqlite3.connect("data/bid_agent.db")
    row = conn.execute(
        "SELECT agent_state_json FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row:
        return json.loads(row[0])  # 恢复完整的 AgentState
    raise ProjectNotFoundError(project_id)
```

4. **跨环境共享**：开发者在本地 Mac 上生成标书 → 导出 `bid_agent.db` → 上传到团队共享目录 → 另一名开发者下载 → 打开即恢复完整上下文（包括所有微调轮次和全局约束）。

| 传输方式 | 适用场景 |
|---------|---------|
| 同一台机器 | 直接读写同一 `.db` 文件 |
| 局域网 | `data/` 目录挂载到共享文件夹 |
| 跨团队 | Git LFS / 网盘 / 内部文件服务 |
| CI/CD | `.db` 作为 pipeline artifact 传递 |
| SaaS 部署 | 服务端统一 SQLite 文件（单实例）或迁移到 PostgreSQL |

**与分层上下文注入的关系**：

`global_constraints`、`compressed_history`、`feedback_history` 均作为 AgentState 的字段随 SQLite 序列化一起持久化。Agent 恢复会话后直接获得完整的三层上下文，无需重新计算压缩摘要。

**安全考虑（Prompt Guard 视角）**：
- 状态文件包含完整的标书内容，是高度敏感的商务机密——传输时需加密
- 跨环境恢复时，Layer 1（全局约束）应重新校验完整性，防止传输过程中被篡改
- 不建议将 `.db` 文件放入公开可访问的 URL，始终通过鉴权渠道传输

**行动**：在 Phase 1 实现时，DocumentParser 和 DocumentAssembler 节点间使用 SqliteSaver 自动保存快照。GUI 的「继续上次会话」功能从 projects 表读取最近项目并恢复 AgentState。
