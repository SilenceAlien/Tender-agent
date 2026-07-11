# AgentState 字段契约

## 类型定义

```python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages
from enum import Enum


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FeedbackType(str, Enum):
    CONTENT_FIX = "content_fix"
    STYLE_ADJUST = "style_adjust"
    STRUCTURE_OPTIMIZE = "structure"
    SCORE_ALIGN = "score_align"


class AgentState(TypedDict):
    documents: list[dict]
    requirements: dict
    info_verification_status: str       # US#6: "pending"|"confirmed"
    info_summary: dict                  # US#6: 供 GUI 展示的关键信息摘要
    user_confirmed_fields: dict         # US#6: 用户校验后的修正值
    matched_templates: list[str]
    selected_template_id: str
    sections: dict[str, str]
    current_section: str
    quality_report: dict
    feedback_history: list[dict]
    current_round: int
    max_rounds: int
    node_status: dict[str, NodeStatus]
    messages: Annotated[list, add_messages]
```

## 字段约束

| 字段 | 类型 | 必填 | 默认值 | 约束 |
|------|------|------|--------|------|
| `documents` | `list[dict]` | 是 | `[]` | 每项含 `filename`(str) `content`(str) `type`(pdf/docx) |
| `requirements` | `dict` | 是 | `{}` | 必须含 `scoring` `qualifications` `tech_specs` `format_rules` 四键 |
| `info_verification_status` | `str` | 是 | `""` | US#6: `"pending"` \| `"confirmed"`，信息校验门状态 |
| `info_summary` | `dict` | 是 | `{}` | US#6: 关键信息摘要 + `field_sources` 来源标注 |
| `user_confirmed_fields` | `dict` | 是 | `{}` | US#6: 用户校验后的修正值（最高优先级） |
| `matched_templates` | `list[str]` | 是 | `[]` | 按匹配度降序，最多 5 个 |
| `selected_template_id` | `str` | 否 | `""` | 空字符串表示未选择 |
| `sections` | `dict[str,str]` | 是 | `{}` | key=章节名，value=生成内容 |
| `current_section` | `str` | 是 | `""` | 当前生成中的章节名 |
| `quality_report` | `dict` | 是 | `{}` | 必须含 `verdict`(`PASS`/`FAIL`) `completeness` `compliance` `consistency` |
| `feedback_history` | `list[dict]` | 是 | `[]` | 每项含 `round` `feedback_text` `feedback_type` `target_section` `target_paragraph_index` `timestamp` |
| `current_round` | `int` | 是 | `0` | 0=初稿，每轮反馈后递增，上限 `max_rounds` |
| `max_rounds` | `int` | 是 | `3` | 配置项，可在 config 中覆盖 |
| `node_status` | `dict[str,NodeStatus]` | 是 | `{}` | key=节点名，value=NodeStatus 枚举 |
| `messages` | `list` | 是 | `[]` | LangGraph 内置字段，由 add_messages reducer 管理 |

## 序列化规则

- `NodeStatus` 枚举在序列化时转为字符串值
- `FeedbackType` 枚举同上
- `AgentState` 通过 `json.dumps(state)` 序列化为 JSON 存入 SQLite `projects.agent_state_json`
- 反序列化时 `json.loads(json_str)` 恢复为 dict（枚举值需手动转回枚举）
