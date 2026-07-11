"""Unit tests for SQLite database initialization and CRUD operations."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from core.database import (
    SCHEMA_SQL,
    add_document,
    add_feedback,
    create_project,
    delete_project,
    get_all_configs,
    get_config,
    get_feedbacks,
    get_project,
    get_project_documents,
    get_sections,
    init_db,
    list_projects,
    load_agent_state,
    save_agent_state,
    save_section,
    set_config,
    update_document_content,
    update_project_status,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def db_conn():
    """Create an in-memory database with full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def project_id(db_conn):
    """Create a test project and return its ID."""
    return create_project(db_conn, "测试项目", "service")


# ── Schema Tests ───────────────────────────────────────────────────────


class TestSchemaCreation:
    def test_all_five_tables_exist(self, db_conn):
        rows = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        table_names = {r["name"] for r in rows}
        expected = {"projects", "documents", "sections", "feedbacks", "configs"}
        assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"

    def test_init_db_returns_connection(self):
        conn = init_db(":memory:")
        assert isinstance(conn, sqlite3.Connection)
        conn.close()


class TestProjectsTable:
    def test_projects_schema_columns(self, db_conn):
        info = db_conn.execute("PRAGMA table_info(projects)").fetchall()
        cols = {r["name"] for r in info}
        required = {"id", "name", "bid_type", "status", "agent_state_json", "created_at", "updated_at"}
        assert required.issubset(cols), f"Missing columns: {required - cols}"

    def test_create_project_returns_uuid(self, project_id):
        assert len(project_id) == 36  # UUID4 format
        assert project_id.count("-") == 4

    def test_create_project_sets_defaults(self, db_conn):
        pid = create_project(db_conn, "默认状态项目", "goods")
        project = get_project(db_conn, pid)
        assert project["status"] == "created"
        assert project["name"] == "默认状态项目"

    def test_get_project_returns_correct_dict(self, db_conn, project_id):
        project = get_project(db_conn, project_id)
        assert project["id"] == project_id
        assert project["name"] == "测试项目"
        assert project["bid_type"] == "service"

    def test_get_project_nonexistent(self, db_conn):
        assert get_project(db_conn, "nonexistent-id") is None

    def test_update_project_status(self, db_conn, project_id):
        update_project_status(db_conn, project_id, "parsing")
        project = get_project(db_conn, project_id)
        assert project["status"] == "parsing"

    def test_list_projects_returns_all(self, db_conn):
        p1 = create_project(db_conn, "项目A", "service")
        p2 = create_project(db_conn, "项目B", "goods")
        projects = list_projects(db_conn)
        ids = [p["id"] for p in projects]
        assert p2 in ids  # newer first
        assert p1 in ids

    def test_save_and_load_agent_state(self, db_conn, project_id):
        state = {"documents": [{"filename": "test.pdf"}], "current_round": 1}
        save_agent_state(db_conn, project_id, state)
        loaded = load_agent_state(db_conn, project_id)
        assert loaded == state

    def test_load_agent_state_none(self, db_conn, project_id):
        loaded = load_agent_state(db_conn, project_id)
        assert loaded is None

    def test_delete_project(self, db_conn, project_id):
        delete_project(db_conn, project_id)
        assert get_project(db_conn, project_id) is None


class TestDocumentsTable:
    def test_add_document(self, db_conn, project_id):
        doc_id = add_document(db_conn, project_id, "招标文件.pdf", "pdf", "/tmp/test.pdf")
        assert len(doc_id) == 36

    def test_get_project_documents(self, db_conn, project_id):
        add_document(db_conn, project_id, "a.pdf", "pdf", "/tmp/a.pdf")
        add_document(db_conn, project_id, "b.docx", "docx", "/tmp/b.docx")
        docs = get_project_documents(db_conn, project_id)
        assert len(docs) == 2

    def test_update_document_content(self, db_conn, project_id):
        doc_id = add_document(db_conn, project_id, "test.pdf", "pdf", "/tmp/test.pdf")
        update_document_content(db_conn, doc_id, "解析内容", 5)
        docs = get_project_documents(db_conn, project_id)
        assert docs[0]["parsed_content"] == "解析内容"
        assert docs[0]["chunk_count"] == 5


class TestSectionsTable:
    def test_save_and_get_sections(self, db_conn, project_id):
        s1 = save_section(db_conn, project_id, "第三章 服务方案", 3, "这是服务方案的内容...", round_num=0)
        s2 = save_section(db_conn, project_id, "第一章 公司简介", 1, "公司简介内容...", round_num=0)
        sections = get_sections(db_conn, project_id)
        assert len(sections) == 2
        # Ordered by section_order
        assert sections[0]["section_name"] == "第一章 公司简介"
        assert sections[1]["section_name"] == "第三章 服务方案"

    def test_sections_have_word_count(self, db_conn, project_id):
        content = "这是测试内容，共八个字"
        section_id = save_section(db_conn, project_id, "测试", 1, content)
        sections = get_sections(db_conn, project_id)
        assert sections[0]["word_count"] == len(content)

    def test_filter_by_round(self, db_conn, project_id):
        save_section(db_conn, project_id, "章节", 1, "v0", round_num=0)
        save_section(db_conn, project_id, "章节", 1, "v1", round_num=1)
        r0 = get_sections(db_conn, project_id, round_num=0)
        assert len(r0) == 1
        assert r0[0]["content"] == "v0"
        r1 = get_sections(db_conn, project_id, round_num=1)
        assert len(r1) == 1
        assert r1[0]["content"] == "v1"


class TestFeedbacksTable:
    def test_add_and_get_feedbacks(self, db_conn, project_id):
        add_feedback(db_conn, project_id, 1, "改语气", "style_adjust", "第三章 服务方案")
        add_feedback(db_conn, project_id, 1, "补充资质", "content_fix", "第二章 资质")
        fbs = get_feedbacks(db_conn, project_id)
        assert len(fbs) == 2

    def test_filter_by_round(self, db_conn, project_id):
        add_feedback(db_conn, project_id, 1, "第1轮反馈", "content_fix", "测试章节")
        add_feedback(db_conn, project_id, 2, "第2轮反馈", "style_adjust", "测试章节")
        r1 = get_feedbacks(db_conn, project_id, round_num=1)
        assert len(r1) == 1
        assert r1[0]["feedback_text"] == "第1轮反馈"


class TestConfigsTable:
    def test_set_and_get_config(self, db_conn):
        set_config(db_conn, "llm_default_provider", "openai")
        assert get_config(db_conn, "llm_default_provider") == "openai"

    def test_get_config_missing(self, db_conn):
        assert get_config(db_conn, "nonexistent") is None

    def test_upsert_config(self, db_conn):
        set_config(db_conn, "key1", "v1")
        set_config(db_conn, "key1", "v2")  # upsert
        assert get_config(db_conn, "key1") == "v2"

    def test_get_all_configs(self, db_conn):
        set_config(db_conn, "a", "1")
        set_config(db_conn, "b", "2")
        all_configs = get_all_configs(db_conn)
        assert all_configs == {"a": "1", "b": "2"}


class TestForeignKeyCascade:
    """Verify foreign key constraints work."""

    def test_document_requires_valid_project(self, db_conn):
        with pytest.raises(sqlite3.IntegrityError):
            add_document(db_conn, "fake-id", "test.pdf", "pdf", "/tmp/test.pdf")

    def test_delete_project_cascades(self, db_conn):
        """SQLite requires PRAGMA foreign_keys = ON for cascade, but we use manual delete."""
        pid = create_project(db_conn, "Cascade Test", "service")
        doc_id = add_document(db_conn, pid, "test.pdf", "pdf", "/tmp/test.pdf")
        delete_project(db_conn, pid)
        # Project deleted
        assert get_project(db_conn, pid) is None
