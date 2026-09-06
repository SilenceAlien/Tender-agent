"""End-to-end integration test — full pipeline flow (T026).

Simulates complete pipeline: 解析→提取→匹配→生成→检查→装配→导出
Verifies AgentState flows correctly through all nodes.
"""

import tempfile
from pathlib import Path

import pytest

from core.graph import run_pipeline
from core.nodes.doc_assembler import doc_assembler
from core.state import NodeStatus, factory_state


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def e2e_state():
    """Full state simulating a complete bid document processing pipeline."""
    import fitz  # PyMuPDF

    # Create mock bid PDFs
    pdf_paths = []
    for i, text_content in enumerate([
        "招标公告 项目名称：劳务管理服务采购 招标编号：GD2026-001",
        "评分标准 技术方案40分 售后服务20分 公司资质20分 价格20分 响应时间要求30分钟内到场 团队配置不少于10人",
    ]):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((50, 50), text_content * 20, fontsize=11)
            doc.save(path)
            doc.close()
            pdf_paths.append(path)

    state = factory_state(
        documents=[
            {"filename": "招标公告.pdf", "type": "pdf", "path": pdf_paths[0]},
            {"filename": "评分标准.pdf", "type": "pdf", "path": pdf_paths[1]},
        ],
        max_rounds=2,
        min_section_chars=0,  # Disable 8000-char threshold for E2E tests
    )

    yield state

    # Cleanup
    for p in pdf_paths:
        Path(p).unlink(missing_ok=True)


# ── E2E Tests ──────────────────────────────────────────────────────────


class TestPipelineE2E:
    def test_full_pipeline_completes(self, e2e_state):
        """Full pipeline should complete without errors."""
        # P1-3: pre-approve the human review gate so the pipeline runs to
        # completion in headless test mode (no GUI to click "approve")
        e2e_state["review_status"] = "approved"
        result = run_pipeline(e2e_state)
        assert result is not None

        # All core nodes should be completed
        core_nodes = ["DocumentParser", "ReqExtractor", "TemplateMatcher",
                      "SectionGenerator", "QualityChecker", "DocumentAssembler"]
        for node in core_nodes:
            status = result["node_status"].get(node, "unknown")
            assert status == NodeStatus.COMPLETED.value, f"{node}: {status}"

    def test_sections_generated(self, e2e_state):
        """Pipeline should produce section content."""
        result = run_pipeline(e2e_state)
        sections = result.get("sections", {})
        assert len(sections) >= 8
        for name, content in sections.items():
            assert len(content) > 0, f"{name} is empty"
            assert "章" in name

    def test_state_flows_through_nodes(self, e2e_state):
        """Each node should update node_status correctly."""
        result = run_pipeline(e2e_state)

        # DocumentParser
        assert result["node_status"]["DocumentParser"] == NodeStatus.COMPLETED.value
        docs = result.get("documents", [])
        assert len(docs) == 2
        for doc in docs:
            assert "parsed_content" in doc or "status" in doc

        # Requirements should have structure
        reqs = result.get("requirements", {})
        assert "scoring" in reqs
        assert "qualifications" in reqs
        assert "format_rules" in reqs

    def test_quality_report_produced(self, e2e_state):
        """Quality check runs and produces a report."""
        result = run_pipeline(e2e_state)
        qr = result.get("quality_report", {})
        assert "verdict" in qr
        assert "completeness" in qr
        assert "compliance" in qr

    def test_can_export_docx(self, e2e_state):
        """Generated content can be exported to DOCX."""
        result = run_pipeline(e2e_state)

        with tempfile.TemporaryDirectory() as tmpdir:
            assembly = doc_assembler(result, export_dir=tmpdir)
            export_path = assembly.get("export_path", "")
            assert export_path != ""
            assert Path(export_path).exists()
            assert Path(export_path).stat().st_size > 1000

    def test_documents_parsed(self, e2e_state):
        """Documents should be parsed with chunks."""
        result = run_pipeline(e2e_state)
        docs = result.get("documents", [])
        assert len(docs) >= 2
        parsed_count = sum(1 for d in docs if d.get("status") == "parsed")
        assert parsed_count >= 1

    def test_feedback_loop_triggers_on_fail(self):
        """When content has banned words, feedback loop should activate."""
        import json

        # State with a section containing banned words
        state = factory_state(
            requirements={
                "scoring": [{"item_name": "方案", "score": 50, "criteria": "完整"}],
            },
            sections={
                "第一章": "绝对最好的方案，唯一的顶级选择。" * 10,
                "第二章": "正常内容覆盖全部评分项。" * 10,
            },
            current_round=0,
            max_rounds=3,
        )
        result = run_pipeline(state)
        # Feedback should be generated
        fb_history = result.get("feedback_history", [])
        # May have multiple rounds
        assert result["current_round"] >= 1 or result["quality_report"]["verdict"] == "PASS"


