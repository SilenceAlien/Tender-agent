"""SQLite database initialization and CRUD operations.

Tables:
    projects   — Project sessions with serialized AgentState
    documents  — Uploaded file records
    sections   — Chapter version chain
    feedbacks  — Feedback history
    configs    — User configuration key-value store

⚠──────────────────────────────────────────────────────────────────────
【运行时接入状态 — L8（低危）】
本模块目前 **未接入运行时**，仅被 `tests/unit/test_database.py` 引用。
运行时实际持久化方案：
  · 用户配置 / API Key → `core/config_persistence.py`（文件 JSON，位于 ~/.bid-agent/config.json）
  · metrics / prompt 演化 → 各自独立 `_init_db()`（core/evolution/metrics_tracker.py、prompt_registry.py）
跨会话的 pipeline 运行记录、企业资质库缓存等能力本模块已具备 API，但运行时
尚未建立「project」生命周期来消费它。

【若需接入，建议方式】
1. 在运行时入口（如 gui/pipeline_runner 或 graph 完成回调）以「可选、失败不抛」的
   方式调用 create_project / save_agent_state，将一次运行快照写入 projects 表；
   必须 try/except 包裹，DB 异常绝不可中断主流程。
2. 若希望统一管理，可将 metrics_tracker / prompt_registry 的 `_init_db()` 改造为
   复用本模块的 init_db + 连接；但属较大重构（>20 行），需单独评估，不在本次范围。

【禁止事项】
· 不得把 config_persistence 的 API Key 回退/迁移到本数据库（会改变安全模型，Key 落库）。
· 不得要求运行时强制依赖外部 DB 服务；本模块仅用本地 SQLite 文件，属内嵌存储。
· schema 以 CREATE TABLE IF NOT EXISTS 幂等创建，无需迁移；新增列需评估兼容性。
──────────────────────────────────────────────────────────────────────⚠
"""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

# We'll set this from config or fallback
_DEFAULT_DB_PATH: str | None = None


def _get_db_path() -> str:
    """Resolve the database file path."""
    if _DEFAULT_DB_PATH:
        return _DEFAULT_DB_PATH
    # Fallback: project root / data / bid_agent.db
    root = Path(__file__).resolve().parent.parent
    return str(root / "data" / "bid_agent.db")


def set_db_path(path: str) -> None:
    """Override the default database file path."""
    global _DEFAULT_DB_PATH
    _DEFAULT_DB_PATH = path


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with row factory enabled."""
    path = db_path or _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Schema ─────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    bid_type TEXT NOT NULL,
    status TEXT DEFAULT 'created',
    agent_state_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    parsed_content TEXT,
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    section_name TEXT NOT NULL,
    section_order INTEGER NOT NULL,
    round INTEGER DEFAULT 0,
    content TEXT NOT NULL,
    word_count INTEGER DEFAULT 0,
    is_current BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedbacks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    section_id TEXT REFERENCES sections(id),
    round INTEGER NOT NULL,
    feedback_text TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    target_section TEXT NOT NULL,
    target_paragraph_index INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS configs (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_sections_project ON sections(project_id, section_name, round);
CREATE INDEX IF NOT EXISTS idx_feedbacks_project ON feedbacks(project_id, round);
"""


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    """Initialize the database schema and return a connection."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ── Projects CRUD ──────────────────────────────────────────────────────


def create_project(
    conn: sqlite3.Connection,
    name: str,
    bid_type: str,
    status: str = "created",
) -> str:
    """Create a new project, returning its UUID."""
    project_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, bid_type, status) VALUES (?, ?, ?, ?)",
        (project_id, name, bid_type, status),
    )
    conn.commit()
    return project_id


def get_project(conn: sqlite3.Connection, project_id: str) -> dict | None:
    """Retrieve a project by ID."""
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def update_project_status(conn: sqlite3.Connection, project_id: str, status: str) -> None:
    """Update the project status."""
    conn.execute(
        "UPDATE projects SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, project_id),
    )
    conn.commit()


def save_agent_state(conn: sqlite3.Connection, project_id: str, state: dict) -> None:
    """Serialize and save AgentState to the project."""
    conn.execute(
        "UPDATE projects SET agent_state_json = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(state, ensure_ascii=False, default=str), project_id),
    )
    conn.commit()


def load_agent_state(conn: sqlite3.Connection, project_id: str) -> dict | None:
    """Load and deserialize AgentState from a project."""
    row = conn.execute(
        "SELECT agent_state_json FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row and row["agent_state_json"]:
        return json.loads(row["agent_state_json"])
    return None


def list_projects(conn: sqlite3.Connection) -> list[dict]:
    """List all projects, newest first."""
    rows = conn.execute(
        "SELECT * FROM projects ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Documents CRUD ─────────────────────────────────────────────────────


def add_document(
    conn: sqlite3.Connection,
    project_id: str,
    filename: str,
    file_type: str,
    file_path: str,
) -> str:
    """Add a document record, returning its UUID."""
    doc_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO documents (id, project_id, filename, file_type, file_path) VALUES (?, ?, ?, ?, ?)",
        (doc_id, project_id, filename, file_type, file_path),
    )
    conn.commit()
    return doc_id


def update_document_content(
    conn: sqlite3.Connection,
    doc_id: str,
    parsed_content: str,
    chunk_count: int,
) -> None:
    """Update parsed content for a document."""
    conn.execute(
        "UPDATE documents SET parsed_content = ?, chunk_count = ? WHERE id = ?",
        (parsed_content, chunk_count, doc_id),
    )
    conn.commit()


def get_project_documents(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Get all documents for a project."""
    rows = conn.execute(
        "SELECT * FROM documents WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Sections CRUD ──────────────────────────────────────────────────────


def save_section(
    conn: sqlite3.Connection,
    project_id: str,
    section_name: str,
    section_order: int,
    content: str,
    round_num: int = 0,
) -> str:
    """Save a section version, returning its UUID."""
    section_id = str(uuid.uuid4())
    word_count = len(content)
    conn.execute(
        """INSERT INTO sections (id, project_id, section_name, section_order, round, content, word_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (section_id, project_id, section_name, section_order, round_num, content, word_count),
    )
    conn.commit()
    return section_id


def get_sections(
    conn: sqlite3.Connection,
    project_id: str,
    round_num: int | None = None,
    current_only: bool = True,
) -> list[dict]:
    """Get sections for a project, optionally filtered by round."""
    query = "SELECT * FROM sections WHERE project_id = ?"
    params: list[Any] = [project_id]
    if round_num is not None:
        query += " AND round = ?"
        params.append(round_num)
    if current_only:
        query += " AND is_current = 1"
    query += " ORDER BY section_order"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── Feedbacks CRUD ─────────────────────────────────────────────────────


def add_feedback(
    conn: sqlite3.Connection,
    project_id: str,
    round_num: int,
    feedback_text: str,
    feedback_type: str,
    target_section: str,
    section_id: str | None = None,
    target_paragraph_index: int | None = None,
) -> str:
    """Add a feedback record, returning its UUID."""
    fb_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO feedbacks (id, project_id, section_id, round, feedback_text,
           feedback_type, target_section, target_paragraph_index)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (fb_id, project_id, section_id, round_num, feedback_text, feedback_type, target_section, target_paragraph_index),
    )
    conn.commit()
    return fb_id


def get_feedbacks(
    conn: sqlite3.Connection,
    project_id: str,
    round_num: int | None = None,
) -> list[dict]:
    """Get feedback records for a project."""
    query = "SELECT * FROM feedbacks WHERE project_id = ?"
    params: list[Any] = [project_id]
    if round_num is not None:
        query += " AND round = ?"
        params.append(round_num)
    query += " ORDER BY created_at"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── Configs CRUD ───────────────────────────────────────────────────────


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a configuration key-value pair."""
    conn.execute(
        """INSERT INTO configs (key, value, updated_at) VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')""",
        (key, value),
    )
    conn.commit()


def get_config(conn: sqlite3.Connection, key: str) -> str | None:
    """Get a configuration value by key."""
    row = conn.execute("SELECT value FROM configs WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_all_configs(conn: sqlite3.Connection) -> dict[str, str]:
    """Get all configuration key-value pairs."""
    rows = conn.execute("SELECT key, value FROM configs").fetchall()
    return {r["key"]: r["value"] for r in rows}


def delete_project(conn: sqlite3.Connection, project_id: str) -> None:
    """Delete a project and all related records (cascade).

    Deletes in order: feedbacks → sections → documents → project.
    """
    conn.execute("DELETE FROM feedbacks WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM sections WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM documents WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
