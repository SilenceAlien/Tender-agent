# 技术计划 — 标书制作智能体

**语言/版本**：Python 3.11+
**主要依赖**：LangGraph, LangChain, FAISS-cpu, Streamlit, PyMuPDF, python-docx, SQLite
**存储**：SQLite（状态持久化）+ FAISS（向量检索，本地磁盘文件）
**测试**：pytest + pytest-mock + pytest-asyncio
**目标平台**：macOS（开发）+ Linux x86_64（Phase 3 部署）
**项目类型**：单用户桌面/GUI 应用，Phase 1 为本地 Streamlit 服务
**性能目标**：单份标书初稿生成 < 10 分钟（8 章约 20,000 字）
**约束**：不依赖任何云服务 API（除用户自配的 LLM API Key）；FAISS 全本地运行；所有数据不出本地
**规模/范围**：Phase 1 单用户，核心代码约 3,000-5,000 行 Python

---

## 架构决策记录

### ADR-01：编排引擎选择 LangGraph

**决策**：使用 LangGraph StateGraph 作为核心编排引擎。

**理由**：
- StateGraph + conditional edges 原生支持条件路由（QualityChecker PASS/FAIL 分支）和循环（微调回路）
- subgraph 机制支持章节并行生成
- 与 LangChain ChatModel 深度集成，无需额外适配层
- 可视化调试（LangGraph Studio）便于开发期状态追踪

**替代方案（已否决）**：
- 自建状态机：开发量大，重复造轮子
- Prefect/Airflow：太重，为 DAG 和定时任务设计，非交互式 AI 管道
- CrewAI/AutoGen：多 Agent 框架，颗粒度太粗，无法精确控制状态流转

### ADR-02：向量存储选择 FAISS

**决策**：使用 FAISS (faiss-cpu) 作为向量检索引擎。

**理由**：
- Meta 开源，零外部服务依赖，兼容 Phase 3 离线部署
- HNSW/IVFPQ 索引类型满足不同检索场景（模板库精度优先、历史库存储优先）
- 单机百万级向量规模下检索延迟 < 50ms
- Python 原生绑定，无需 Docker 或服务进程

**替代方案（已否决）**：
- Chroma：多了一层 HTTP 服务依赖，不利于离线部署
- Milvus：太重，面向分布式场景，单机场景杀鸡用牛刀
- Elasticsearch：Java 依赖，部署复杂度高

### ADR-03：前端选择 Streamlit

**决策**：Phase 1 使用 Streamlit 作为 GUI 框架。

**理由**：
- 纯 Python，与后端同语言零摩擦
- 快速原型，四面板布局几十分钟即可搭建
- 无需编写 HTML/CSS/JS，AI 可直接生成 GUI 代码
- Phase 2/3 可按需升级为 React 而不影响核心引擎

**替代方案（已否决）**：
- Gradio：对复杂交互布局的支持不如 Streamlit
- React/Vue：Phase 1 引入前端技术栈增加工程复杂度，偏离「快速验证」目标
- Tkinter/PyQt：桌面端框架，与后续 Web SaaS 路径不兼容

### ADR-04：状态持久化选择 SQLite

**决策**：使用 SQLite 持久化生成进度、版本历史、用户配置。

**理由**：
- 零配置嵌入式数据库，无需独立进程
- 单用户场景下并发不是瓶颈
- 文件即数据库，迁移方便
- Python 内置 sqlite3 模块，零额外依赖

### ADR-05：Embedding 模型选择

**决策**：Phase 1 使用 OpenAI text-embedding-3-small (dim=1536)。

**理由**：
- 中文表现良好，性价比最优
- 1536 维在百万级向量规模下检索精度和存储开销均衡
- Phase 1 仅需调用一次建立索引，后续检索在本地 FAISS 完成

**长期方向**：Phase 2/3 可替换为本地 Embedding 模型（如 BGE-large-zh-v1.5），实现完全离线。

---

## 项目结构

```
bid-agent/
├── core/                         # 核心引擎（永恒不变）
│   ├── __init__.py
│   ├── graph.py                  # LangGraph 状态机定义
│   ├── state.py                  # AgentState 数据类
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── doc_parser.py         # DocumentParser 节点
│   │   ├── req_extractor.py      # ReqExtractor 节点
│   │   ├── template_matcher.py   # TemplateMatcher 节点
│   │   ├── section_generator.py  # SectionGenerator 节点
│   │   ├── quality_checker.py    # QualityChecker 节点
│   │   ├── feedback_processor.py # FeedbackProcessor 节点
│   │   └── doc_assembler.py      # DocumentAssembler 节点
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── faiss_index.py        # FAISS 多索引管理
│   │   ├── pipeline.py           # 六步检索管线
│   │   └── embeddings.py         # Embedding 统一接口
│   └── llm/
│       ├── __init__.py
│       ├── router.py             # ModelRouter 任务级路由
│       └── providers.py          # 六家供应商 ChatModel 工厂
├── gui/                          # Streamlit 界面
│   ├── __init__.py
│   ├── app.py                    # 主入口 + 路由
│   ├── panels/
│   │   ├── config_panel.py       # 模型配置面板
│   │   ├── upload_panel.py       # 资料上传面板
│   │   ├── review_panel.py       # 生成与审阅面板
│   │   └── export_panel.py       # 导出交付面板
│   └── components/
│       ├── progress.py           # 进度条与节点状态
│       └── feedback_form.py      # 反馈输入组件
├── config/
│   ├── __init__.py
│   ├── settings.py               # 全局配置（从 .env + config.yaml 加载）
│   ├── config.yaml               # 默认配置
│   └── model_registry.yaml       # LLM 供应商注册表
├── adapters/                     # 部署适配器（Phase 1 最小实现）
│   ├── __init__.py
│   └── local_adapter.py          # 本地模式桩实现
├── data/                         # 运行时数据（不进入版本控制）
│   ├── uploads/                  # 用户上传文件临时目录
│   ├── faiss_index/              # FAISS 索引文件
│   └── exports/                  # 导出的 DOCX/PDF
├── templates/                    # 标书模板库
│   └── [6 种类型]/
├── tests/
│   ├── contract/                 # API 契约测试
│   ├── integration/              # 集成测试（节点间流转）
│   └── unit/                     # 单元测试（单节点/单函数）
├── specs/
│   └── 001-bid-agent/            # 本功能规范文档
├── requirements.txt
├── pyproject.toml
├── Makefile
└── README.md
```

---

## 接口契约

详见 `contracts/` 目录：
- `contracts/agent_state.md` — AgentState 字段定义与类型约束
- `contracts/node_interfaces.md` — 每个节点的输入输出契约
- `contracts/gui_api.md` — Streamlit 面板与核心引擎的接口

---

## 测试策略

| 测试层级 | 覆盖范围 | 框架 | 目标覆盖率 |
|---------|---------|------|-----------|
| 单元测试 | 单节点逻辑、FAISS 检索、LLM 路由 | pytest | > 70% |
| 集成测试 | 节点间流转、条件路由、微调循环 | pytest + mock LLM | 关键路径 100% |
| 契约测试 | AgentState 序列化、节点 I/O 格式 | pytest | 100% |
| E2E 测试 | 上传→生成→审阅→微调→导出 | pytest + fixtures | ≥ 3 种标书类型 |

**Mock 策略**：LLM 调用全部 mock（使用预定义的 fixture 响应），确保测试确定性和速度。FAISS 索引使用小规模 fixture 数据（每种类型 3 份模板）。

---

## Phase 1 里程碑

| 阶段 | 时间 | 交付物 | 验证方式 |
|------|------|--------|---------|
| S1：框架搭建 | 第 1-2 周 | 项目骨架 + 2 节点可运行 | pytest 全部通过 |
| S2：核心生成 | 第 3-4 周 | FAISS 单索引 + 4 节点联调 | 生成完整 8 章标书 |
| S3：GUI + 微调 | 第 5-6 周 | Streamlit 四面板 + 微调循环 | 端到端走通全流程 |
| S4：优化沉淀 | 第 7-8 周 | 6 种类型模板 + 质量报告 | 3 份真实标书验证 |
