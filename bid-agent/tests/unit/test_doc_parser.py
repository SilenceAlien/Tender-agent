"""Unit tests for DocumentParser node."""

import tempfile
from pathlib import Path

import pytest

from core.nodes.doc_parser import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    _load_docx,
    _load_pdf,
    _parse_single_doc,
    chunk_text,
    document_parser,
)
from core.state import NodeStatus, factory_state


# ── Helpers ────────────────────────────────────────────────────────────


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def sample_pdf_path():
    """Create a 3-page PDF with text using PyMuPDF (fitz).

    Note: CID font substitution means extracted text may use glyph IDs for
    non-ASCII chars. We test ASCII for reliability; Chinese text is
    fully tested via the DOCX fixture.
    """
    import fitz

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    doc = fitz.open()
    for page_num in range(3):
        page = doc.new_page()
        text = f"Page {page_num + 1}: Bid Content. " * 150
        page.insert_text((50, 50), text, fontsize=11, fontname="helv")
    doc.save(path)
    doc.close()
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def sample_docx_path():
    """Create a 5-paragraph DOCX using python-docx."""
    from docx import Document

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        path = f.name

    doc = Document()
    for i in range(5):
        doc.add_paragraph(f"第{i + 1}段 测试内容。 " * 80)  # ~1200 chars per paragraph
    doc.save(path)
    yield path
    Path(path).unlink(missing_ok=True)


# ── Chunking Tests ─────────────────────────────────────────────────────


class TestChunkText:
    def test_empty_text(self):
        assert chunk_text("") == []

    def test_whitespace_only(self):
        assert chunk_text("   \n\n  ") == []

    def test_smaller_than_chunk_size(self):
        text = "短文本，不超过 chunk size。"
        chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_exactly_chunk_size(self):
        text = "A" * 1000
        chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) == 1
        assert chunks[0] == "A" * 1000

    def test_larger_than_chunk_size(self):
        text = "A" * 2500
        chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) >= 3
        # Verify overlap: adjacent chunks share content
        assert chunks[0][-200:] == chunks[1][:200]

    def test_overlap_between_chunks(self):
        text = "A" * 1600
        chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) == 2

    def test_newline_boundary_preferred(self):
        text = "第一段内容\n\n" + "B" * 900 + "\n\n第二段内容"
        chunks = chunk_text(text, chunk_size=500, chunk_overlap=100)
        assert all("\n\n" not in c[300:400] if len(c) > 400 else True for c in chunks)

    def test_no_empty_chunks(self):
        text = "test " * 300
        chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
        assert all(c.strip() for c in chunks)


# ── PDF Loading Tests ──────────────────────────────────────────────────


class TestLoadPdf:
    def test_load_3_page_pdf(self, sample_pdf_path):
        text = _load_pdf(sample_pdf_path)
        assert "Page 1" in text
        assert "Page 2" in text
        assert "Page 3" in text
        assert len(text) > 200

    def test_nonexistent_file(self):
        with pytest.raises(Exception):
            _load_pdf("/nonexistent/file.pdf")


# ── DOCX Loading Tests ────────────────────────────────────────────────


class TestLoadDocx:
    def test_load_5_paragraph_docx(self, sample_docx_path):
        text = _load_docx(sample_docx_path)
        assert "第1段" in text
        assert "第5段" in text
        assert len(text) > 500

    def test_nonexistent_file(self):
        with pytest.raises(Exception):
            _load_docx("/nonexistent/file.docx")


# ── Single Doc Parsing Tests ───────────────────────────────────────────


class TestParseSingleDoc:
    def test_parse_pdf_success(self, sample_pdf_path):
        record = {"filename": "test.pdf", "type": "pdf", "path": sample_pdf_path}
        result = _parse_single_doc(record)
        assert result["status"] == "parsed"
        assert result["parsed_content"]
        assert result["chunk_count"] > 0
        assert result["error"] is None

    def test_parse_docx_success(self, sample_docx_path):
        record = {"filename": "test.docx", "type": "docx", "path": sample_docx_path}
        result = _parse_single_doc(record)
        assert result["status"] == "parsed"
        assert result["parsed_content"]
        assert result["chunk_count"] > 0

    def test_parse_unsupported_type(self):
        record = {"filename": "test.txt", "type": "txt", "path": "/tmp/test.txt"}
        result = _parse_single_doc(record)
        assert result["status"] == "failed"
        assert "Unsupported" in result["error"]

    def test_parse_pdf_chunks_exist(self, sample_pdf_path):
        record = {"filename": "test.pdf", "type": "pdf", "path": sample_pdf_path}
        result = _parse_single_doc(record)
        assert len(result["chunks"]) == result["chunk_count"] > 0

    def test_chunk_size_parameter_used(self, sample_pdf_path):
        """Chunks should respect CHUNK_SIZE=1000 and CHUNK_OVERLAP=200."""
        record = {"filename": "test.pdf", "type": "pdf", "path": sample_pdf_path}
        result = _parse_single_doc(record)
        for chunk in result["chunks"][:-1]:
            assert len(chunk) <= CHUNK_SIZE + 50, f"Chunk too long: {len(chunk)}"

    def test_missing_file_graceful_failure(self):
        record = {"filename": "missing.pdf", "type": "pdf", "path": "/tmp/nonexistent.pdf"}
        result = _parse_single_doc(record)
        assert result["status"] == "failed"
        assert result["error"]


# ── Node Integration Tests ─────────────────────────────────────────────


class TestDocumentParserNode:
    def test_empty_documents(self):
        state = factory_state()
        result = document_parser(state)
        assert result["documents"] == []
        assert result["node_status"]["DocumentParser"] == NodeStatus.COMPLETED.value

    def test_single_pdf(self, sample_pdf_path):
        state = factory_state(documents=[
            {"filename": "test.pdf", "type": "pdf", "path": sample_pdf_path}
        ])
        result = document_parser(state)
        assert result["documents"][0]["status"] == "parsed"
        assert result["node_status"]["DocumentParser"] == NodeStatus.COMPLETED.value

    def test_all_failed_marks_node_failed(self):
        state = factory_state(documents=[
            {"filename": "bad.pdf", "type": "pdf", "path": "/nonexistent.pdf"}
        ])
        result = document_parser(state)
        assert result["documents"][0]["status"] == "failed"
        assert result["node_status"]["DocumentParser"] == NodeStatus.FAILED.value

    def test_mixed_success_and_failure(self, sample_pdf_path):
        state = factory_state(documents=[
            {"filename": "good.pdf", "type": "pdf", "path": sample_pdf_path},
            {"filename": "bad.pdf", "type": "pdf", "path": "/nonexistent.pdf"},
        ])
        result = document_parser(state)
        assert result["documents"][0]["status"] == "parsed"
        assert result["documents"][1]["status"] == "failed"
        assert result["node_status"]["DocumentParser"] == NodeStatus.COMPLETED.value

    def test_multiple_documents(self, sample_pdf_path, sample_docx_path):
        state = factory_state(documents=[
            {"filename": "a.pdf", "type": "pdf", "path": sample_pdf_path},
            {"filename": "b.docx", "type": "docx", "path": sample_docx_path},
        ])
        result = document_parser(state)
        assert all(d["status"] == "parsed" for d in result["documents"])
        assert result["node_status"]["DocumentParser"] == NodeStatus.COMPLETED.value
