# 数据模型 — 标书制作智能体

## 1. AgentState（核心状态对象）

LangGraph 节点间流转的统一状态对象：

```python
from typing import TypedDict, Annotated, Sequence
from langgraph.graph.message import add_messages
from langchain_core.documents import Document
from datetime import datetime
from enum import Enum


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FeedbackType(str, Enum):
    CONTENT_FIX = "content_fix"       # 内容修正
    STYLE_ADJUST = "style_adjust"     # 风格调整
    STRUCTURE_OPTIMIZE = "structure"  # 结构优化
    SCORE_ALIGN = "score_align"       # 评分对齐


class FeedbackScope(str, Enum):
    GLOBAL = "global"   # 影响所有章节（如「全文语气偏暖」）
    LOCAL = "local"     # 影响单章节/段落


class FeedbackRecord(TypedDict):
    round: int
    feedback_text: str
    feedback_type: FeedbackType
    scope: FeedbackScope       # 全局约束 vs 局部修改
    target_section: str
    target_paragraph_index: int
    diff_before: str           # 修改前段落内容
    diff_after: str            # 修改后段落内容
    timestamp: str  # ISO 8601


class QualityReport(TypedDict):
    verdict: str  # "PASS" | "FAIL"
    completeness: dict    # {score_item: bool, ...}
    compliance: list[str] # 违禁词/格式问题清单
    consistency: list[str] # 前后矛盾清单
    total_items: int
    passed_items: int


class AgentState(TypedDict):
    # 输入
    documents: list[dict]  # [{"filename": str, "content": str, "type": "pdf"|"docx"}]
    
    # 需求提取结果
    requirements: dict  # {"scoring": [...], "qualifications": [...], "tech_specs": [...], "format_rules": {...}}
    
    # 模板匹配
    matched_templates: list[str]  # 模板标识列表，按匹配度降序
    selected_template_id: str     # 用户确认的模板
    
    # 生成内容
    sections: dict[str, str]  # {"章节名": "生成内容"}
    current_section: str       # 当前正在生成的章节
    
    # 质量检查
    quality_report: QualityReport
    
    # 反馈循环（分层上下文管理）
    feedback_history: list[FeedbackRecord]
    global_constraints: list[str]  # 全局约束摘要（Layer 1，永不丢弃）
    compressed_history: str        # 超出窗口的旧轮次压缩摘要（Layer 2）
    context_window_size: int       # 最近 N 轮完整保留（默认3）
    current_round: int             # 当前微调轮次（0=初稿）
    max_rounds: int                # 最大微调轮次（默认3）
    
    # 节点状态追踪
    node_status: dict[str, NodeStatus]  # {"DocumentParser": "completed", ...}
    
    # 消息（LangGraph 内置）
    messages: Annotated[list, add_messages]
```

---

## 2. SQLite 持久化模型

### 2.1 projects 表（项目会话）

```sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,              -- UUID4
    name TEXT NOT NULL,               -- 项目名称（从招标文件提取）
    bid_type TEXT NOT NULL,           -- 标书类型：service/goods/software/engineering/integration/ops
    status TEXT DEFAULT 'created',    -- created/parsing/generating/reviewing/exporting/completed
    agent_state_json TEXT,            -- AgentState 序列化 JSON（用于断点恢复）
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### 2.2 documents 表（上传文件记录）

```sql
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,          -- pdf/docx
    file_path TEXT NOT NULL,          -- 本地存储路径
    parsed_content TEXT,              -- 解析后的纯文本
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### 2.3 sections 表（章节版本链）

```sql
CREATE TABLE sections (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    section_name TEXT NOT NULL,       -- 章节名（如 "第三章 服务方案"）
    section_order INTEGER NOT NULL,   -- 章节序号
    round INTEGER DEFAULT 0,          -- 版本轮次（0=初稿）
    content TEXT NOT NULL,            -- 内容
    word_count INTEGER DEFAULT 0,
    is_current BOOLEAN DEFAULT 1,     -- 是否为当前版本
    created_at TEXT DEFAULT (datetime('now'))
);
```

### 2.4 feedbacks 表（反馈历史）

```sql
CREATE TABLE feedbacks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    section_id TEXT REFERENCES sections(id),
    round INTEGER NOT NULL,
    feedback_text TEXT NOT NULL,
    feedback_type TEXT NOT NULL,      -- content_fix/style_adjust/structure/score_align
    target_section TEXT NOT NULL,
    target_paragraph_index INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### 2.5 configs 表（用户配置）

```sql
CREATE TABLE configs (
    key TEXT PRIMARY KEY,             -- 配置键（如 llm_default_provider）
    value TEXT NOT NULL,              -- 配置值
    updated_at TEXT DEFAULT (datetime('now'))
);
```

---

## 3. FAISS 索引数据模型

### 3.1 模板库索引（IndexHNSWFlat）

```python
# 每条记录
{
    "id": "template_service_001",
    "text": "第三章 服务方案\n...",  # 章节级 chunk (500-2000 tokens)
    "metadata": {
        "template_id": "service_v2",
        "bid_type": "service",
        "section_name": "服务方案",
        "section_order": 3,
        "template_version": "2.1",
        "created_date": "2025-06-15"
    }
}
```

### 3.2 历史标书库（IndexIVFPQ + Parent Doc）

```python
# 子 chunk（用于向量检索）
{
    "id": "history_sub_001_03",
    "text": "段落级内容 (200-500 tokens)",
    "metadata": {
        "parent_id": "history_doc_001",
        "bid_type": "service",
        "section_name": "服务方案",
        "win_status": "won",           # won/lost/pending
        "project_scale": "500万+",
        "bid_date": "2024-03-15",
        "chunk_index": 3
    }
}

# 父文档（用于上下文还原）
{
    "id": "history_doc_001",
    "full_text": "完整章节内容...",
    "metadata": {
        "project_name": "XX市劳务管理服务采购",
        "bid_type": "service",
        "win_status": "won",
        "score": 92.5
    }
}
```

### 3.3 知识库索引（IndexHNSWFlat）

```python
# 每条记录
{
    "id": "kb_company_profile",
    "text": "公司简介... (100-300 tokens)",
    "metadata": {
        "kb_type": "company_info",     # company_info/certification/regulation/standard
        "category": "资质证书",
        "valid_until": "2027-12-31"
    }
}
```

---

## 4. 配置数据模型

### config.yaml 结构

```yaml
deployment:
  mode: local  # local / saas / onpremise

llm:
  default_provider: deepseek
  default_model: deepseek-chat
  task_routing:  # 任务级覆盖（可选）
    quality_checker:
      provider: anthropic
      model: claude-sonnet-4-20250514

retrieval:
  embedding_model: text-embedding-3-small
  embedding_dim: 1536
  reranker_model: BAAI/bge-reranker-v2-m3
  top_k_retrieval: 8
  top_k_rerank: 5

generation:
  max_rounds: 3
  default_temperature: 0.3
  max_section_tokens: 4096

document:
  chunk_size: 1000
  chunk_overlap: 200
  supported_formats: [pdf, docx]

paths:
  upload_dir: data/uploads
  index_dir: data/faiss_index
  export_dir: data/exports
  template_dir: templates
```

---

## 5. 微调上下文管理模型

### 分层上下文注入架构

```
┌─────────────────────────────────────────┐
│ 用户提交反馈 query                       │
├─────────────────────────────────────────┤
│ FeedbackProcessor 解析                   │
│  ├─ scope=global → 写入 global_constraints│
│  └─ scope=local  → 写入 feedback_history │
├─────────────────────────────────────────┤
│ SectionGenerator 组装 Prompt             │
│  ├─ Layer 1: global_constraints (硬注入)  │
│  ├─ Layer 2: compressed_history (摘要)    │
│  ├─ Layer 3: feedback_history[-N:] (完整) │
│  └─ + RAG检索 + 模板结构                 │
├─────────────────────────────────────────┤
│ 当 feedback_history 超出窗口时：         │
│  → 调用压缩 LLM 更新 compressed_history  │
│  → 格式：「第X轮[类型][作用域]→摘要」     │
└─────────────────────────────────────────┘
```

### AgentState 新增字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `global_constraints` | `list[str]` | `[]` | 全局约束摘要，永不丢弃，硬注入 Layer 1 |
| `compressed_history` | `str` | `""` | 超出窗口的旧轮次压缩摘要，Layer 2 |
| `context_window_size` | `int` | `3` | Layer 3 保留的最近完整轮次数 |

### FeedbackRecord 新增字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `scope` | `FeedbackScope` | `global`=影响所有章节 / `local`=单章节 |
| `diff_before` | `str` | 修改前段落原文 |
| `diff_after` | `str` | 修改后段落内容（SectionGenerator 生成后回填） |

### 压缩 Prompt 模板

```
以下是标书微调的历史记录摘要。请将以下修改记录压缩为简洁摘要：

约束：
1. 逐条保留所有「禁止」「必须」「应当」类约束的原文，不得改写
2. 每条摘要 ≤30 字，格式：「第X轮[作用域][类型]→摘要」
3. 局部修改（scope=local）标注目标章节
4. 全局修改（scope=global）标注影响范围

示例输入：
- 第1轮[全局][风格]：「全文语气偏暖，避免生硬的法律条文口吻」
- 第2轮[局部][内容]：「第3章第2段补充张三年限10年和PMP证书编号」

示例输出：
第1轮[全局][风格]→语气偏暖，禁用生硬法条口吻
第2轮[局部][内容]→团队配置-张三年限10年+PMP
```
