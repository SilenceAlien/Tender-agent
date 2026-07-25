# 标书制作智能体 (Tender Agent)

AI 驱动的招投标文件自动生成系统 —— 上传招标文件，自动解析需求、匹配模板、生成标书章节、质量审查、人工审阅、导出 DOCX。

## 功能概览

- **智能解析**：自动提取招标文件中的评分项、资质要求、技术规格、格式规则
- **信息校验**：提取关键信息后人工确认，确保项目名称、投标方等核心字段准确无误
- **模板匹配**：基于 FAISS 向量检索，从知识库中匹配最优标书模板
- **章节生成**：按评分项逐章生成标书内容，注入项目上下文契约保证跨章一致性
- **多重审查**：质量检查 → 交叉引用一致性 → 合规审查 → 评分模拟，四道关卡保障质量
- **人工审阅**：支持审阅-驳回-修改循环（最多 3 轮），审阅通过后装配最终文档
- **一致性自进化**：自动从审查报告中学经验，持久化存储并注入后续生成流程
- **多模型支持**：DeepSeek、OpenAI、Anthropic、智谱、通义千问、月之暗面，可按节点级配置不同模型

## 技术栈

| 层级 | 技术 |
|------|------|
| 工作流引擎 | LangGraph (14 节点状态图 + 条件路由) |
| LLM 框架 | LangChain (多 Provider 适配层) |
| 向量检索 | FAISS HNSW (含 numpy 降级方案) |
| 检索融合 | Reciprocal Rank Fusion (RRF) |
| GUI | Streamlit (四面板交互式界面) |
| 文档解析 | PyMuPDF (PDF) + python-docx (DOCX) |
| 导出 | python-docx (政府采购标准格式) |
| Python | ≥ 3.11 |

## 项目结构

```
标书agent02/
├── bid-agent/                # 主程序
│   ├── core/                 # 核心引擎
│   │   ├── graph.py          # LangGraph 14 节点流水线
│   │   ├── state.py          # AgentState 统一状态对象
│   │   ├── nodes/            # 16 个流水线节点
│   │   │   ├── doc_parser.py          # 文档解析 (PDF/DOCX)
│   │   │   ├── req_extractor.py       # 需求提取 (评分项/资质/技术规格)
│   │   │   ├── contract_extractor.py  # 契约构建 (项目上下文)
│   │   │   ├── info_verification_gate.py  # 信息校验门
│   │   │   ├── eligibility_checker.py # 资质校验
│   │   │   ├── template_matcher.py    # 模板匹配
│   │   │   ├── section_generator.py   # 章节生成 (并行)
│   │   │   ├── quality_checker.py     # 质量检查
│   │   │   ├── feedback_processor.py  # 反馈处理
│   │   │   ├── cross_reference_checker.py  # 交叉引用一致性
│   │   │   ├── compliance_checker.py  # 合规审查
│   │   │   ├── score_simulator.py     # 评分模拟
│   │   │   ├── human_review_gate.py   # 人工审核门
│   │   │   └── doc_assembler.py       # 文档装配导出
│   │   ├── llm/              # LLM 适配层
│   │   │   ├── providers.py  # 6 个 Provider 工厂
│   │   │   └── router.py     # 节点级模型路由
│   │   ├── retrieval/        # 检索系统
│   │   │   ├── embeddings.py         # Embedding 接口
│   │   │   ├── faiss_index.py        # FAISS 向量索引
│   │   │   ├── pipeline.py           # 多索引 RRF 融合
│   │   │   ├── reference_retriever.py # 参考范文检索
│   │   │   ├── template_library.py   # 模板库
│   │   │   └── subtype_router.py     # 子类型路由
│   │   └── evolution/        # 自进化系统
│   │       ├── consistency_lesson_store.py    # 经验持久化
│   │       ├── consistency_lesson_extractor.py # 经验自动提取
│   │       ├── prompt_registry.py     # 提示词注册
│   │       └── prompt_selector.py     # 提示词选择
│   ├── gui/                  # Streamlit GUI
│   │   ├── app.py            # 主入口
│   │   ├── panels/           # 四面板
│   │   │   ├── config_panel.py   # 模型配置
│   │   │   ├── upload_panel.py   # 资料上传
│   │   │   ├── review_panel.py   # 生成与审阅
│   │   │   └── export_panel.py   # 导出交付
│   │   └── components/       # 通用组件
│   ├── config/               # 配置文件
│   │   └── config.yaml       # 默认配置
│   ├── scripts/              # 工具脚本
│   │   ├── build_knowledge_base.py  # 知识库构建
│   │   └── ingest_real_bid.py       # 标书导入
│   ├── data/                 # 运行时数据
│   │   ├── consistency_lessons.json # 一致性经验库
│   │   ├── exports/          # 导出文件
│   │   ├── uploads/          # 上传文件
│   │   └── faiss_index/      # FAISS 索引
│   ├── tests/                # 测试套件
│   │   ├── unit/             # 单元测试
│   │   └── integration/      # 集成测试
│   ├── pyproject.toml        # 项目配置
│   ├── requirements.txt      # 依赖清单
│   └── .env.example          # 环境变量模板
├── knowledge_base/           # 知识库 (7 类标书)
│   ├── 服务类/               # 物业/IT运维/后勤
│   ├── 货物类/               # 设备采购
│   ├── 工程类/               # 工程建设
│   ├── 运维类/               # 运维服务
│   ├── 集成类/               # 系统集成
│   ├── 劳务管理服务类/        # 劳务派遣
│   └── 劳务外包类/           # 食堂/生产线/HRO
│       └── 食堂餐饮|工业生产线|HRO/
│           ├── chunks/       # 分块文档
│           ├── patterns/     # 模式规则
│           └── prompts/      # 子类型提示词
└── AGENTS.md                 # 项目开发规范
```

## 快速开始

### 1. 环境准备

**系统要求**：Python ≥ 3.11，macOS / Linux / Windows

```bash
# 克隆仓库
git clone https://github.com/SilenceAlien/Tender-agent.git
cd Tender-agent/bid-agent

# 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

# 安装依赖
pip install -e ".[dev]"
```

### 2. 配置 API Key

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入你的 API Key
# 至少配置一个 LLM Provider 的 Key
```

`.env` 文件内容：

```ini
# DeepSeek API (推荐，性价比高)
DEEPSEEK_API_KEY=your_deepseek_api_key_here

# OpenAI API (可选，用于 embedding 或备选模型)
OPENAI_API_KEY=your_openai_api_key_here

# Anthropic API (可选，用于 Claude 模型)
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

**API Key 获取地址**：

| Provider | 获取地址 |
|----------|---------|
| DeepSeek | https://platform.deepseek.com/api_keys |
| OpenAI | https://platform.openai.com/api-keys |
| Anthropic | https://console.anthropic.com/ |

### 3. 启动 GUI

```bash
cd bid-agent
streamlit run gui/app.py
```

浏览器自动打开 `http://localhost:8501`。

### 4. 使用流程

GUI 提供四个标签页，按顺序操作：

**① 模型配置** → 输入 API Key，选择主用模型，点击「测试」验证连通性

**② 资料上传** → 选择标书类型，上传招标文件 (PDF/DOCX)，填写补充说明，点击「开始解析」

**③ 生成与审阅** → 系统自动执行：解析 → 提取 → 信息校验 → 生成 → 质量检查 → 审阅

- 信息校验步骤会暂停，请确认提取的关键信息无误后点击「确认」继续
- 生成完成后可审阅各章节内容，选择「通过」或「驳回并修改」

**④ 导出交付** → 点击「导出 DOCX」下载最终标书文件

## 流水线架构

系统基于 LangGraph 构建 14 节点状态图，支持条件路由和人工检查点：

```
DocumentParser → ReqExtractor → ContractExtractor → InfoVerificationGate
                                                           ↓ (确认)
EligibilityChecker → TemplateMatcher → SectionGenerator → QualityChecker
                                                           ↓
                    ┌──────────────────────────────────────┘
                    ↓ PASS                              ↓ FAIL
          CrossReferenceChecker                    FeedbackProcessor
                    ↓ PASS                                ↓
            ComplianceChecker ──── FAIL ────────────────→↑
                    ↓ PASS
            ScoreSimulator → HumanReviewGate
                               ↓ 通过                    ↓ 驳回
                        DocumentAssembler          FeedbackProcessor → SectionGenerator
                               ↓
                             END
```

**两种运行模式**：

- **交互模式**（GUI）：在信息校验和人工审阅处暂停，等待用户操作后继续
- **无头模式**（API/脚本）：自动确认所有检查点，端到端运行

### 无头模式使用

```python
from core.graph import run_pipeline
from core.llm.providers import create_llm

# 创建 LLM
llm = create_llm("deepseek", api_key="sk-xxx", model="deepseek-chat")

# 构造初始状态
from core.state import factory_state
initial_state = factory_state(
    documents=[{"filename": "tender.pdf", "content": "...", "type": "pdf"}],
    bid_type="服务类",
)

# 运行流水线
result = run_pipeline(
    initial_state=initial_state,
    llm_fns={"ReqExtractor": llm, "SectionGenerator": llm, ...},
)

# 获取结果
print(result["sections"])        # 各章节内容
print(result["export_path"])     # DOCX 文件路径
print(result["quality_report"])  # 质量报告
```

## 支持的 LLM Provider

| Provider | 模型示例 | 用途 |
|----------|---------|------|
| DeepSeek | `deepseek-chat`, `deepseek-reasoner` | 默认 Provider，通用对话 + 推理 |
| OpenAI | `gpt-4o`, `gpt-4o-mini` | 生成 / Embedding |
| Anthropic | `claude-sonnet-4-20250514` | 质量检查 / 审查 |
| 智谱 (Zhipu) | `glm-4` | 备选生成 |
| 通义千问 (Qwen) | `qwen-max` | 备选生成 |
| 月之暗面 (Moonshot) | `moonshot-v1-8k` | 备选生成 |

**节点级模型配置**：可在 GUI 的「模型配置 → 节点级模型配置」中为不同节点指定不同模型（如生成用 DeepSeek，质检用 GPT-4o）。

## 知识库

知识库位于项目根目录 `knowledge_base/`，按标书类型组织，包含：

| 目录 | 内容 |
|------|------|
| `模板/` | 完整标书模板（8 章标准结构） |
| `范文/` | 各章节参考范文 |
| `中标方案/` | 历史中标方案摘要 |
| `中标公告/` | 中标结果公告 |
| `招标文件/` | 原始招标文件 |

**劳务外包类**额外支持子类型分区检索：
- `食堂餐饮/` — 食堂外包
- `工业生产线/` — 生产线劳务
- `HRO/` — 人力资源外包

### 构建知识库索引

```bash
cd bid-agent
python scripts/build_knowledge_base.py
```

此脚本会读取知识库中的标书模板和范文，生成 FAISS 向量索引供模板匹配和 RAG 检索使用。

## 配置说明

### config.yaml

位于 `bid-agent/config/config.yaml`，包含所有默认配置：

```yaml
llm:
  default_provider: deepseek
  default_model: deepseek-chat

retrieval:
  embedding_model: text-embedding-3-small
  embedding_dim: 1536
  top_k_retrieval: 8
  top_k_rerank: 5

generation:
  max_rounds: 3              # 最大修改轮次
  default_temperature: 0.3
  max_section_tokens: 4096

document:
  chunk_size: 1000
  chunk_overlap: 200
```

### 环境变量覆盖

使用 `BID_` 前缀覆盖任意配置项：

```bash
# 格式: BID_<section>__<key>=value
export BID_LLM__DEFAULT_PROVIDER=openai
export BID_GENERATION__MAX_ROUNDS=5
```

### 用户配置持久化

GUI 中输入的 API Key 和模型配置自动保存到 `~/.bid-agent/config.json`，下次启动自动加载。

## 运行测试

```bash
cd bid-agent

# 运行全部测试
pytest

# 运行单元测试
pytest tests/unit/ -m unit

# 运行集成测试
pytest tests/integration/ -m integration

# 运行单个测试文件
pytest tests/unit/test_router.py -v
```

## 日志

日志文件位于 `~/.bid-agent/logs/bid-agent.log`，记录所有节点的执行状态和错误信息。

## 常见问题

### Q: 启动后提示 "No API key for deepseek"

A: 在 GUI 的「模型配置」标签页输入 API Key 并保存，或在 `.env` 文件中设置 `DEEPSEEK_API_KEY`。

### Q: FAISS 未安装，检索功能是否可用？

A: 是。系统会自动降级为 numpy 暴力搜索，功能完整但检索速度较慢。生产环境建议安装 `faiss-cpu`。

### Q: 生成的标书质量不理想怎么办？

A: 1) 在「节点级模型配置」中为 SectionGenerator 选择更强的模型；2) 在人工审阅环节驳回并填写修改意见，系统会自动修改；3) 增大 `max_rounds` 配置项允许更多修改轮次。

### Q: 如何添加新的标书类型？

A: 在 `knowledge_base/` 下新建对应类型的目录，放入模板和范文，然后运行 `python scripts/build_knowledge_base.py` 重建索引。

### Q: 推送到 GitHub 时遇到 SSL 错误？

A: 尝试增大 Git HTTP 缓冲：`git config http.postBuffer 524288000`，然后重新推送。

## License

MIT
