"""DocumentParser node — loads PDF/DOCX/DOC files, extracts text and tables.

Contract (from node_interfaces.md):
    def document_parser(state: AgentState) -> dict
    Input:  state.documents (list of file records)
    Output: {documents: [附加 parsed_content + tables], extracted_tables: [...],
             node_status: {"DocumentParser": COMPLETED}}
    Constraints:
    - chunk_size=1000, chunk_overlap=200
    - 解析失败的文件标记 status=FAILED 并记录错误原因
    - 至少成功解析 1 个文件才标记 COMPLETED
    - P1-1: PDF 表格结构化提取（评分表/资质表直接转 JSON）
"""

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# Keywords used to classify extracted tables by header content
_SCORING_KEYWORDS = {"评分", "分值", "得分", "满分", "评分项", "评分标准", "评议"}
_QUALIFICATION_KEYWORDS = {"资质", "资格", "证书", "许可", "认证", "等级"}

# ── Loader functions ───────────────────────────────────────────────────


def _load_pdf(file_path: str) -> str:
    """Extract full text from a PDF file.

    Primary: PyMuPDF (fitz) — fast, handles CJK well.
    Fallback: pdfminer.six (MinerU base).
    """
    try:
        import fitz

        doc = fitz.open(file_path)
        text_parts: list[str] = []
        for page in doc:
            page_text = page.get_text()
            if page_text and page_text.strip():
                text_parts.append(page_text)
        doc.close()
        if text_parts:
            return "\n".join(text_parts)
    except ModuleNotFoundError:
        pass  # fitz not installed — fall through to pdfminer.six
    except Exception as e:
        # BUG-09 fix: fitz is installed but failed (corrupted/encrypted PDF,
        # unsupported format, etc.). Log and fall through to pdfminer.six
        # instead of crashing.
        logger.warning(f"PyMuPDF failed to parse {file_path}: {e} — trying pdfminer.six")

    # Fallback: pdfminer.six
    from pdfminer.high_level import extract_text as pdfminer_extract_text

    return pdfminer_extract_text(file_path)


def _load_docx(file_path: str) -> str:
    """Extract full text from a DOCX file using python-docx.

    DOCX is a ZIP-based OOXML format.  If the file is not a valid ZIP archive
    (e.g. old-format .doc, corrupted, or encrypted), this function will fail
    with a clear error message.

    Raises:
        ValueError: If the file doesn't exist, is empty, or is not a valid docx.
    """
    from pathlib import Path as _Path
    from zipfile import is_zipfile as _is_zipfile

    p = _Path(file_path)

    if not p.exists():
        raise ValueError(f"文件不存在：{file_path}")

    if p.stat().st_size == 0:
        raise ValueError(f"文件为空：{file_path}")

    if not _is_zipfile(file_path):
        raise ValueError(
            f"文件不是有效的 DOCX 格式（非 ZIP/OOXML 压缩包）。"
            f"如果是旧版 .doc 文件，请用 Word 另存为 .docx 格式后再上传。"
        )

    from docx import Document

    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    if not paragraphs:
        raise ValueError("DOCX 文件中未找到任何文字内容（可能全是图片或表格）")
    return "\n".join(paragraphs)


def _load_doc(file_path: str) -> str:
    """Extract full text from a legacy .doc (Word 97-2003) file.

    .doc is a proprietary binary OLE format.  Unlike .docx (ZIP/OOXML),
    it cannot be parsed by python-docx.  We use platform-native converters:

    - macOS:   textutil (built-in, excellent CJK support)
    - Linux:   antiword (apt install antiword)
    - Fallback: olefile for raw stream extraction (limited quality)

    Raises:
        ValueError: If the file doesn't exist, is empty, or no converter works.
    """
    p = Path(file_path)

    if not p.exists():
        raise ValueError(f"文件不存在：{file_path}")

    if p.stat().st_size == 0:
        raise ValueError(f"文件为空：{file_path}")

    errors: list[str] = []

    # ── Strategy 1: macOS textutil (built-in, best CJK) ─────────────────
    try:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp_out:
            tmp_out_path = tmp_out.name
        result = subprocess.run(
            ["textutil", "-convert", "txt", file_path, "-output", tmp_out_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            out = Path(tmp_out_path)
            if out.exists() and out.stat().st_size > 0:
                text = out.read_text(encoding="utf-8")
                out.unlink(missing_ok=True)
                if text.strip():
                    return text.strip()
        if result.stderr.strip():
            errors.append(f"textutil: {result.stderr.strip()}")
        Path(tmp_out_path).unlink(missing_ok=True)
    except FileNotFoundError:
        pass  # textutil not found (non-macOS)
    except subprocess.TimeoutExpired:
        errors.append("textutil: 转换超时")
    except Exception as e:
        errors.append(f"textutil: {e}")

    # ── Strategy 2: antiword (Linux) ────────────────────────────────────
    try:
        result = subprocess.run(
            ["antiword", file_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        if result.stderr.strip():
            errors.append(f"antiword: {result.stderr.strip()}")
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        errors.append("antiword: 转换超时")
    except Exception as e:
        errors.append(f"antiword: {e}")

    # ── Strategy 3: olefile raw extraction (last resort) ────────────────
    try:
        import olefile

        ole = olefile.OleFileIO(file_path)
        # The WordDocument stream contains the main text
        if ole.exists("WordDocument"):
            word_stream = ole.openstream("WordDocument").read()
            # Extract readable ASCII/UTF-8 fragments (best-effort)
            text_chars = []
            for byte in word_stream:
                if 32 <= byte < 127 or byte in (10, 13):  # printable + newlines
                    text_chars.append(chr(byte))
            text = "".join(text_chars)
            # Collapse whitespace
            import re
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r"[ \t]{3,}", "  ", text)
            if text.strip():
                return text.strip()
            errors.append("olefile: extracted text is empty")
        else:
            errors.append("olefile: no WordDocument stream found")
        ole.close()
    except ImportError:
        pass  # olefile not installed
    except Exception as e:
        errors.append(f"olefile: {e}")

    # ── All strategies failed ───────────────────────────────────────────
    error_detail = "; ".join(errors) if errors else "无可用的 .doc 解析器"
    raise ValueError(
        f"无法解析 .doc 文件。{error_detail}。"
        f"请将文件用 Word 另存为 .docx 格式后再上传。"
    )


# ── Chunking ───────────────────────────────────────────────────────────


def _classify_table(header_cells: list[str]) -> str:
    """Classify an extracted table by its header content.

    Returns one of: "scoring", "qualification", "unknown".
    """
    if not header_cells:
        return "unknown"
    header_text = " ".join(str(c) for c in header_cells)
    if any(kw in header_text for kw in _SCORING_KEYWORDS):
        return "scoring"
    if any(kw in header_text for kw in _QUALIFICATION_KEYWORDS):
        return "qualification"
    return "unknown"


def _extract_tables_pdf(file_path: str) -> list[dict]:
    """Extract structured tables from a PDF using PyMuPDF's find_tables.

    PyMuPDF (fitz) >= 1.23 provides `page.find_tables()` which detects
    table boundaries via ruling lines / cell alignment and returns a
    TableFinder object.  Each table is converted to a list of rows.

    Returns:
        List of table dicts:
            {page, header, rows, row_count, col_count, type}
    """
    try:
        import fitz
    except ModuleNotFoundError:
        logger.warning("PyMuPDF not available — skipping table extraction")
        return []

    tables_out: list[dict] = []
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        logger.warning(f"Cannot open PDF for table extraction: {e}")
        return []

    try:
        for page_idx, page in enumerate(doc, start=1):
            try:
                finder = page.find_tables()
            except Exception:
                # Some pages have no tables or unsupported layout
                continue
            for tbl in finder.tables:
                try:
                    rows = tbl.extract()
                except Exception:
                    continue
                if not rows:
                    continue
                # Clean cells: convert None → "", strip whitespace
                cleaned = [
                    [("" if c is None else str(c)).strip() for c in row]
                    for row in rows
                ]
                # Drop fully-empty rows
                cleaned = [r for r in cleaned if any(c for c in r)]
                if not cleaned:
                    continue
                header = cleaned[0]
                body = cleaned[1:] if len(cleaned) > 1 else []
                col_count = max(len(r) for r in cleaned)
                tables_out.append({
                    "page": page_idx,
                    "header": header,
                    "rows": body,
                    "row_count": len(body),
                    "col_count": col_count,
                    "type": _classify_table(header),
                })
    finally:
        doc.close()

    logger.info(f"Extracted {len(tables_out)} tables from {file_path}")
    return tables_out


def _extract_tables_docx(file_path: str) -> list[dict]:
    """Extract tables from a DOCX file using python-docx.

    DOCX tables are first-class objects, so extraction is straightforward.
    """
    try:
        from docx import Document
    except ModuleNotFoundError:
        return []

    try:
        doc = Document(file_path)
    except Exception as e:
        logger.warning(f"Cannot open DOCX for table extraction: {e}")
        return []

    tables_out: list[dict] = []
    for tbl_idx, table in enumerate(doc.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(cells)
        if not rows:
            continue
        rows = [r for r in rows if any(c for c in r)]
        if not rows:
            continue
        header = rows[0]
        body = rows[1:] if len(rows) > 1 else []
        col_count = max(len(r) for r in rows) if rows else 0
        tables_out.append({
            "page": tbl_idx,  # DOCX has no pages; use table index instead
            "header": header,
            "rows": body,
            "row_count": len(body),
            "col_count": col_count,
            "type": _classify_table(header),
        })

    logger.info(f"Extracted {len(tables_out)} tables from DOCX {file_path}")
    return tables_out


def chunk_text(
    text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_size characters.

    Uses a simple character-based sliding window. Chunks are trimmed to avoid
    breaking mid-sentence where possible (prefers newline boundaries).
    """
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size
        chunk = text[start:end]

        # Prefer breaking at a newline if one exists in the second half
        # of the chunk (to avoid mid-sentence splits).
        if end < text_len:
            # Search for the last newline within overlap zone
            overlap_start = max(start, end - chunk_overlap)
            nl_pos = text.rfind("\n", overlap_start, end)
            if nl_pos != -1 and nl_pos > start:
                chunk = text[start:nl_pos]
                start = nl_pos + 1  # next chunk starts after newline
            else:
                start = end - chunk_overlap
        else:
            start = text_len  # last chunk

        stripped = chunk.strip()
        if stripped:
            chunks.append(stripped)

    return chunks


# ── Document level parsing ─────────────────────────────────────────────


def _detect_file_type(file_path: str) -> str | None:
    """Detect the actual file type from magic bytes, not the file extension.

    Uses the first few bytes (magic number / signature) to distinguish:
    - PDF:  ``%PDF``
    - DOCX: ``PK\x03\x04`` (ZIP archive — OOXML is a ZIP)
    - DOC:  ``\xD0\xCF`` (OLE2 Compound Document — old Word format)

    Returns ``"pdf"``, ``"docx"``, ``"doc"``, or ``None`` if unknown.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)  # read enough bytes for all signatures
            if header[:4] == b"%PDF":
                return "pdf"
            if header[:4] == b"PK\x03\x04":
                # Could be DOCX or XLSX — assume DOCX unless we know otherwise
                return "docx"
            if header[:2] == b"\xd0\xcf":  # OLE2 Compound Document signature
                return "doc"
            return None
    except Exception:
        return None


def _parse_single_doc(file_record: dict, chunk_size: int = CHUNK_SIZE) -> dict:
    """Parse a single file record and return with parsed_content and chunk_count.

    Auto-detects the real file type from magic bytes, overriding the
    extension-based guess when there is a mismatch (common with .docx
    files that are actually old-format .doc).

    Args:
        file_record: Dict with filename, type, path keys.
        chunk_size: Character length for text chunking (default: CHUNK_SIZE=1000).
    """
    filename = file_record.get("filename", "unknown")
    file_type = file_record.get("type", "").lower()
    file_path = file_record.get("path", "")

    result = {**file_record}

    if not file_path:
        result["status"] = "failed"
        result["error"] = "No file path provided"
        result["parsed_content"] = ""
        result["chunks"] = []
        result["chunk_count"] = 0
        result["tables"] = []
        return result

    # Auto-detect from magic bytes — overrides wrong extensions
    try:
        detected = _detect_file_type(file_path)
        if detected and detected != file_type:
            logger.info(
                f"{filename}: magic bytes say '{detected}' "
                f"(extension says '{file_type}') — correcting type"
            )
            file_type = detected
    except Exception:
        pass  # non-fatal

    try:
        tables: list[dict] = []
        if file_type in ("pdf",):
            raw_text = _load_pdf(file_path)
            tables = _extract_tables_pdf(file_path)
        elif file_type == "docx":
            try:
                raw_text = _load_docx(file_path)
            except ValueError as e:
                error_msg = str(e)
                # If the DOCX file is actually an old .doc format (wrong
                # extension), fall back to _load_doc instead of failing.
                if "不是有效的 DOCX 格式" in error_msg or "非 ZIP/OOXML" in error_msg:
                    logger.warning(
                        f"{filename} has .docx extension but is not a valid "
                        f"DOCX — retrying as legacy .doc format"
                    )
                    raw_text = _load_doc(file_path)
                    # .doc has no programmatic table extraction
                    tables = []
                else:
                    raise
            else:
                tables = _extract_tables_docx(file_path)
        elif file_type == "doc":
            raw_text = _load_doc(file_path)
            # .doc has no programmatic table extraction — leave empty
        else:
            result["status"] = "failed"
            result["error"] = f"Unsupported file type: {file_type}"
            result["parsed_content"] = ""
            result["chunks"] = []
            result["chunk_count"] = 0
            result["tables"] = []
            return result

        chunks = chunk_text(raw_text, chunk_size=chunk_size)
        result["status"] = "parsed"
        result["raw_text"] = raw_text
        result["parsed_content"] = raw_text  # full text for downstream
        result["chunks"] = chunks
        result["chunk_count"] = len(chunks)
        result["tables"] = tables
        result["error"] = None

        table_summary = (
            f", {len(tables)} tables "
            f"({sum(1 for t in tables if t['type']=='scoring')} scoring, "
            f"{sum(1 for t in tables if t['type']=='qualification')} qualification)"
            if tables else ""
        )
        logger.info(
            f"Parsed {filename}: {len(raw_text)} chars → {len(chunks)} chunks{table_summary}"
        )

    except Exception as e:
        logger.error(f"Failed to parse {filename}: {e}")
        result["status"] = "failed"
        result["error"] = str(e)
        result["parsed_content"] = ""
        result["chunks"] = []
        result["chunk_count"] = 0
        result["tables"] = []

    return result


# ── LangGraph Node ─────────────────────────────────────────────────────


def document_parser(state: AgentState) -> dict:
    """Parse uploaded PDF/DOCX files into chunked text + structured tables.

    Updates state.documents in-place with parsed content, chunks, and tables.
    Also collects all tables across documents into state.extracted_tables
    so downstream nodes (ReqExtractor) can consume them directly without
    re-parsing.
    """
    documents = state.get("documents", [])
    if not documents:
        logger.warning("No documents to parse")
        return {
            "documents": [],
            "extracted_tables": [],
            "node_status": {**state["node_status"], "DocumentParser": NodeStatus.COMPLETED.value}
        }

    parsed: list[dict] = []
    success_count = 0
    all_tables: list[dict] = []

    # Read chunk_size from state (defaults to CHUNK_SIZE if not set)
    chunk_size = state.get("chunk_size", CHUNK_SIZE)

    for doc in documents:
        result = _parse_single_doc(doc, chunk_size=chunk_size)
        parsed.append(result)
        if result["status"] == "parsed":
            success_count += 1
            # Collect tables with source filename for traceability
            for tbl in result.get("tables", []):
                tbl_with_source = {**tbl, "source": result.get("filename", "")}
                all_tables.append(tbl_with_source)

    node_status_value = (
        NodeStatus.COMPLETED.value
        if success_count > 0
        else NodeStatus.FAILED.value
    )

    scoring_count = sum(1 for t in all_tables if t["type"] == "scoring")
    qual_count = sum(1 for t in all_tables if t["type"] == "qualification")
    logger.info(
        f"DocumentParser: {success_count}/{len(documents)} docs parsed, "
        f"{len(all_tables)} tables extracted "
        f"({scoring_count} scoring, {qual_count} qualification)"
    )

    return {
        "documents": parsed,
        "extracted_tables": all_tables,
        "node_status": {**state["node_status"], "DocumentParser": node_status_value},
    }
