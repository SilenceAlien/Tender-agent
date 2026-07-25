"""ReqExtractor node — extracts structured requirements from parsed documents.

Contract (from node_interfaces.md):
    def req_extractor(state: AgentState) -> dict
    Input:  state.documents[].parsed_content
    Output: {requirements: {scoring, qualifications, tech_specs, format_rules}, node_status: {...}}
    Constraints:
    - scoring: list[dict] — {item_name, score, criteria}
    - qualifications: list[str]
    - format_rules: must include page_margin, font, line_spacing (default if missing)
    - Non-JSON LLM response → retry once → still fails → FAILED
"""

import json
import logging
import re
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Prompt Template ────────────────────────────────────────────────────

_REQ_EXTRACTION_PROMPT = """你是一位资深的招投标专家。请仔细阅读以下招标文件内容，提取结构化的需求信息。

请以严格的 JSON 格式返回（不要包含任何其他文字），包含以下字段：

{{
  "project_name": "项目名称（从招标文件标题或正文中提取完整项目名称，如未提及则留空字符串）",
  "bid_number": "招标编号/采购编号/项目编号（从招标文件中提取，如未提及则留空字符串）",
  "tenderer_name": "招标人/采购人名称（全称，如未提及则留空字符串）",
  "package_number": "包件号/标段号（如未提及则留空字符串）",
  "scoring": [
    {{
      "item_name": "评分项名称",
      "score": 数字分值,
      "criteria": "评分标准描述"
    }}
  ],
  "qualifications": [
    "资质要求1",
    "资质要求2"
  ],
  "tech_specs": [
    {{
      "spec_name": "技术规格名称",
      "requirement": "具体要求",
      "mandatory": true/false
    }}
  ],
  "format_rules": {{
    "page_margin": "上/下/左/右 单位cm（如未说明则用默认值：上3.7/下3.5/左2.8/右2.6cm）",
    "font": "如未说明则用默认值：正文仿宋_GB2312",
    "line_spacing": "如未说明则用默认值：28磅",
    "title_levels": ["一级标题格式", "二级标题格式"],
    "seal_requirement": "盖章要求",
    "binding": "装订要求"
  }}
}}

提取规则：
1. project_name、bid_number、tenderer_name 是最关键的项目信息，必须从招标文件中尽可能提取
2. 项目名称通常出现在文件标题、招标公告首页或项目概况中
3. 招标编号通常出现在文件封面或招标公告中，格式如 GD2026-001
4. 招标人/采购人通常出现在招标公告中
5. 无法确定的字段留空字符串 ""

招标文件内容如下：
{content}

请务必只返回 JSON，不要包含任何其他文字。"""

# ── Default format rules (政府采购常见规范) ──────────────────────

DEFAULT_FORMAT_RULES = {
    "page_margin": "上3.7cm/下3.5cm/左2.8cm/右2.6cm",
    "font": "正文仿宋_GB2312",
    "line_spacing": "28磅",
    "title_levels": [],
    "seal_requirement": "加盖公章",
    "binding": "胶装",
}

# ── JSON Extraction ────────────────────────────────────────────────────


def _extract_json_from_response(text: str) -> dict | None:
    """Try to extract a JSON object from an LLM response.

    Handles: raw JSON, markdown code blocks, and JSON with surrounding text.
    """
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract from ```json ... ``` block
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try to find the outermost { ... } pair
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ── Validation ─────────────────────────────────────────────────────────


def _validate_requirements(raw: dict) -> dict:
    """Validate and normalize extracted requirements. Apply defaults where missing."""
    result = {
        "project_name": "",
        "bid_number": "",
        "tenderer_name": "",
        "package_number": "",
        "scoring": [],
        "qualifications": [],
        "tech_specs": [],
        "format_rules": dict(DEFAULT_FORMAT_RULES),
    }

    # Project metadata (extracted from tender document)
    for field in ("project_name", "bid_number", "tenderer_name", "package_number"):
        val = raw.get(field, "")
        if isinstance(val, str) and val.strip():
            result[field] = val.strip()

    # Scoring
    scoring = raw.get("scoring", [])
    if isinstance(scoring, list):
        for item in scoring:
            if isinstance(item, dict) and "item_name" in item:
                result["scoring"].append({
                    "item_name": item.get("item_name", ""),
                    "score": item.get("score", 0),
                    "criteria": item.get("criteria", ""),
                })

    # Qualifications
    qualifications = raw.get("qualifications", [])
    if isinstance(qualifications, list):
        result["qualifications"] = [str(q) for q in qualifications if q]

    # Tech specs
    tech_specs = raw.get("tech_specs", [])
    if isinstance(tech_specs, list):
        for spec in tech_specs:
            if isinstance(spec, dict) and "spec_name" in spec:
                result["tech_specs"].append({
                    "spec_name": spec.get("spec_name", ""),
                    "requirement": spec.get("requirement", ""),
                    "mandatory": bool(spec.get("mandatory", True)),
                })

    # Format rules — merge with defaults
    extracted_format = raw.get("format_rules", {})
    if isinstance(extracted_format, dict):
        for key in DEFAULT_FORMAT_RULES:
            if key in extracted_format and extracted_format[key]:
                result["format_rules"][key] = extracted_format[key]

    return result


# ── Input size guard (Phase B5, F4 fix) ────────────────────────────────
# Large PDFs (e.g. the 9.7MB 集成类 tender) can produce >100k chars of text.
#
# F4 fix: Instead of hard-truncating to 30k (which silently discarded 68%+
# of large tender documents), we now split into chunks of MAX_INPUT_CHARS
# each and call the LLM for every chunk, then merge results with
# deduplication.  This preserves all content while staying within each
# LLM call's context window.
#
# 30000 chars (~7500 tokens Chinese) is a safe per-chunk size that covers
# most content while leaving room for the prompt template and JSON output.
MAX_INPUT_CHARS = 30000


# ── LLM Call (injectable for testing) ──────────────────────────────────


def _call_llm_for_extraction(
    content: str, llm_fn: Callable[[str], str] | None = None
) -> dict | None:
    """Call LLM to extract requirements. Returns parsed dict or None.

    If llm_fn is not provided, uses a simple mock that returns empty results.
    In production, this would be wired to ModelRouter (T013).
    """
    if llm_fn is None:
        # Simple mock: return empty requirements when no LLM available
        logger.warning("No LLM function provided — returning empty requirements")
        return {
            "project_name": "",
            "bid_number": "",
            "tenderer_name": "",
            "package_number": "",
            "scoring": [],
            "qualifications": [],
            "tech_specs": [],
            "format_rules": {},
        }

    prompt = _REQ_EXTRACTION_PROMPT.format(content=content)
    response = llm_fn(prompt)
    return _extract_json_from_response(response)


# ── F4 fix: Chunked extraction for large documents ─────────────────────


def _split_into_chunks(text: str, chunk_size: int = MAX_INPUT_CHARS) -> list[str]:
    """Split text into chunks of approximately ``chunk_size`` characters.

    Tries to break at newline boundaries for cleaner chunks.  Each chunk
    is self-contained enough for the LLM to extract requirements from it.

    F4 fix: replaces the old single-blob truncation that silently discarded
    68%+ of large tender documents (e.g. 94k chars → 30k).
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break

        # Try to find a newline near the chunk boundary (within last 20% of chunk)
        search_start = start + int(chunk_size * 0.8)
        newline_pos = text.rfind("\n", search_start, end)
        if newline_pos > start:
            end = newline_pos + 1
        else:
            # No good newline — try sentence boundary
            for sep in ("。", "；", ".", ";"):
                pos = text.rfind(sep, search_start, end)
                if pos > start:
                    end = pos + 1
                    break

        chunks.append(text[start:end])
        start = end

    return chunks


def _merge_extractions(extractions: list[dict]) -> dict:
    """Merge multiple LLM extraction results into a single unified result.

    Deduplicates scoring items by ``item_name``, qualifications by exact
    string, and tech_specs by ``spec_name``.  Format rules are merged with
    first non-empty value winning.  Project metadata is taken from the
    first extraction that has a non-empty value.
    """
    if not extractions:
        return {}

    merged: dict = {
        "project_name": "",
        "bid_number": "",
        "tenderer_name": "",
        "package_number": "",
        "scoring": [],
        "qualifications": [],
        "tech_specs": [],
        "format_rules": {},
    }

    seen_scoring: set[str] = set()  # lowercased item_name
    seen_quals: set[str] = set()
    seen_specs: set[str] = set()  # lowercased spec_name

    for ext in extractions:
        if not ext or not isinstance(ext, dict):
            continue

        # Project metadata: first non-empty wins
        for field in ("project_name", "bid_number", "tenderer_name", "package_number"):
            val = ext.get(field, "")
            if not merged[field] and isinstance(val, str) and val.strip():
                merged[field] = val.strip()

        # Scoring items: deduplicate by item_name (case-insensitive)
        for item in ext.get("scoring", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("item_name", "")).strip().lower()
            if name and name not in seen_scoring:
                seen_scoring.add(name)
                merged["scoring"].append(item)

        # Qualifications: deduplicate by exact string
        for q in ext.get("qualifications", []):
            q_str = str(q).strip()
            if q_str and q_str not in seen_quals:
                seen_quals.add(q_str)
                merged["qualifications"].append(q_str)

        # Tech specs: deduplicate by spec_name (case-insensitive)
        for spec in ext.get("tech_specs", []):
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("spec_name", "")).strip().lower()
            if name and name not in seen_specs:
                seen_specs.add(name)
                merged["tech_specs"].append(spec)

        # Format rules: first non-empty value for each key wins
        for key, val in ext.get("format_rules", {}).items():
            if not merged["format_rules"].get(key) and val:
                merged["format_rules"][key] = val

    return merged


# ── Table-derived requirements (P1-1) ──────────────────────────────────


def _parse_scoring_table(table: dict) -> list[dict]:
    """Convert an extracted scoring table into the scoring-item schema.

    Scoring tables typically have columns like:
        评分项 | 分值 | 评分标准
    or:    序号 | 评分内容 | 满分 | 评分细则

    We map by header keyword rather than position so column order varies.
    """
    header = table.get("header", [])
    rows = table.get("rows", [])
    if not header or not rows:
        return []

    # Find column indices by header keyword
    name_col = score_col = criteria_col = -1
    # Pass 1: match specific keywords (avoid generic "分" matching "评分项")
    for i, h in enumerate(header):
        if name_col < 0 and any(k in h for k in ("评分项", "评分内容", "项目名称", "内容", "指标")):
            name_col = i
        if score_col < 0 and any(k in h for k in ("分值", "满分", "得分", "最高分")):
            score_col = i
        if criteria_col < 0 and any(k in h for k in ("评分标准", "评分细则", "标准", "细则", "说明")):
            criteria_col = i
    # Pass 2: fallback to generic "分" for score, but skip columns already claimed
    if score_col < 0:
        for i, h in enumerate(header):
            if i != name_col and i != criteria_col and "分" in h:
                score_col = i
                break

    # Fallback: if no header match, assume [name, score, criteria] order
    if name_col < 0 and len(header) >= 1:
        name_col = 0
    if score_col < 0 and len(header) >= 2:
        score_col = 1
    if criteria_col < 0 and len(header) >= 3:
        criteria_col = 2

    items: list[dict] = []
    for row in rows:
        # Skip rows that are too short or are sub-headers
        if len(row) <= max(name_col, score_col):
            continue
        name = row[name_col] if name_col < len(row) else ""
        if not name or name in ("合计", "总分", "小计"):
            continue
        score_str = row[score_col] if score_col < len(row) else "0"
        # Parse score: extract first integer
        score_match = re.search(r"\d+(?:\.\d+)?", str(score_str))
        score = float(score_match.group(0)) if score_match else 0
        criteria = row[criteria_col] if criteria_col >= 0 and criteria_col < len(row) else ""
        items.append({
            "item_name": name.strip(),
            "score": score,
            "criteria": criteria.strip(),
            "source": "table",  # marks provenance for debugging
        })
    return items


def _parse_qualification_table(table: dict) -> list[str]:
    """Convert an extracted qualification table into a list of qual names."""
    header = table.get("header", [])
    rows = table.get("rows", [])
    if not rows:
        return []

    # Find the column most likely to contain the qualification name
    name_col = -1
    for i, h in enumerate(header):
        if any(k in h for k in ("资质", "资格", "证书", "名称", "要求")):
            name_col = i
            break
    if name_col < 0:
        name_col = 0

    quals: list[str] = []
    for row in rows:
        if name_col >= len(row):
            continue
        name = row[name_col].strip()
        if name and name not in ("合计", "无", "—"):
            quals.append(name)
    return quals


def _requirements_from_tables(tables: list[dict]) -> tuple[list[dict], list[str]]:
    """Extract scoring items and qualifications from structured tables.

    Returns (scoring_items, qualifications) — both may be empty if no
    relevant tables were found.
    """
    scoring_items: list[dict] = []
    qualifications: list[str] = []

    for tbl in tables:
        tbl_type = tbl.get("type", "unknown")
        if tbl_type == "scoring":
            scoring_items.extend(_parse_scoring_table(tbl))
        elif tbl_type == "qualification":
            qualifications.extend(_parse_qualification_table(tbl))

    return scoring_items, qualifications


# ── LangGraph Node ─────────────────────────────────────────────────────


def req_extractor(
    state: AgentState, llm_fn: Callable[[str], str] | None = None
) -> dict:
    """Extract structured requirements from parsed document content.

    P1-1: if DocumentParser already extracted scoring/qualification tables,
    consume them directly (no LLM call needed for those fields).  LLM is
    still used for tech_specs and format_rules, which aren't typically
    tabulated.

    Uses the configured LLM (or mock) to extract scoring, qualifications,
    tech specs, and format rules. Retries once on JSON parse failure.
    """
    documents = state.get("documents", [])
    extracted_tables = state.get("extracted_tables", []) or []
    bid_type = state.get("bid_type", "")

    # ── P1-1: consume structured tables first (no LLM cost) ────────────
    table_scoring, table_quals = _requirements_from_tables(extracted_tables)
    if table_scoring or table_quals:
        logger.info(
            f"ReqExtractor: consumed {len(table_scoring)} scoring items + "
            f"{len(table_quals)} qualifications from extracted tables (no LLM call)"
        )

    if not documents and not extracted_tables:
        logger.warning("No documents to extract requirements from")
        return {
            "requirements": state.get("requirements", {}),
            "node_status": {**state.get("node_status", {}), "ReqExtractor": NodeStatus.COMPLETED.value},
        }

    # Concatenate all document contents
    all_content_parts: list[str] = []
    for doc in documents:
        parsed = doc.get("parsed_content", "")
        if parsed:
            all_content_parts.append(
                f"--- 文件: {doc.get('filename', 'unknown')} ---\n{parsed}"
            )

    if not all_content_parts and not table_scoring and not table_quals:
        logger.warning("All documents have empty parsed_content and no tables")
        return {
            "requirements": state.get("requirements", {}),
            "node_status": {**state.get("node_status", {}), "ReqExtractor": NodeStatus.FAILED.value},
        }

    # If we already have scoring + quals from tables, we may still want
    # tech_specs and format_rules from the LLM.  But if there's no content
    # to feed the LLM, return what we have.
    if not all_content_parts:
        requirements = {
            "project_name": "",
            "bid_number": "",
            "tenderer_name": "",
            "package_number": "",
            "scoring": table_scoring,
            "qualifications": table_quals,
            "tech_specs": [],
            "format_rules": {},
        }
        logger.info("ReqExtractor: returning table-derived requirements only (no text content for LLM)")
        return {
            "requirements": requirements,
            "node_status": {**state.get("node_status", {}), "ReqExtractor": NodeStatus.COMPLETED.value},
        }

    combined = "\n\n".join(all_content_parts)

    # F4 fix: chunked extraction replaces single-blob truncation.
    # Previously, content >30k chars was hard-truncated, silently discarding
    # 68%+ of large tender documents.  Now we split into chunks and call the
    # LLM for each, then merge results with deduplication.
    chunks = _split_into_chunks(combined, MAX_INPUT_CHARS)

    if len(chunks) == 1:
        # Single chunk — use the original single-call path (with retry)
        raw = _call_llm_for_extraction(chunks[0], llm_fn)
        if raw is None and llm_fn is not None:
            logger.warning("First LLM call returned invalid JSON — retrying")
            raw = _call_llm_for_extraction(chunks[0], llm_fn)
    else:
        # Multiple chunks — extract from each and merge
        logger.info(
            f"ReqExtractor: input {len(combined)} chars split into {len(chunks)} "
            f"chunks (chunk_size={MAX_INPUT_CHARS}) — extracting from each"
        )
        extractions: list[dict] = []
        for i, chunk in enumerate(chunks):
            chunk_raw = _call_llm_for_extraction(chunk, llm_fn)
            # Retry once per chunk on JSON parse failure
            if chunk_raw is None and llm_fn is not None:
                logger.warning(f"Chunk {i+1}/{len(chunks)} returned invalid JSON — retrying")
                chunk_raw = _call_llm_for_extraction(chunk, llm_fn)
            if chunk_raw is not None:
                extractions.append(chunk_raw)
            else:
                logger.warning(f"Chunk {i+1}/{len(chunks)} failed after retry — skipping")

        if extractions:
            raw = _merge_extractions(extractions)
            logger.info(
                f"ReqExtractor: merged {len(extractions)}/{len(chunks)} chunks → "
                f"{len(raw.get('scoring', []))} scoring items, "
                f"{len(raw.get('qualifications', []))} qualifications, "
                f"{len(raw.get('tech_specs', []))} tech specs"
            )
        else:
            raw = None

    if raw is None:
        # P1-1: if LLM failed but we have table-derived data, use it
        if table_scoring or table_quals:
            logger.warning(
                "ReqExtractor: LLM failed, but using table-derived requirements"
            )
            requirements = _validate_requirements({
                "scoring": table_scoring,
                "qualifications": table_quals,
                "tech_specs": [],
                "format_rules": {},
            })
            return {
                "requirements": requirements,
                "node_status": {**state.get("node_status", {}), "ReqExtractor": NodeStatus.COMPLETED.value},
            }
        logger.error("ReqExtractor: failed to parse LLM response after retry")
        return {
            "requirements": state.get("requirements", {}),
            "node_status": {**state.get("node_status", {}), "ReqExtractor": NodeStatus.FAILED.value},
        }

    requirements = _validate_requirements(raw)

    # P1-1: prefer table-derived scoring/quals over LLM output when tables
    # were available (tables are structured ground truth; LLM may hallucinate)
    if table_scoring:
        logger.info(
            f"ReqExtractor: overriding {len(requirements['scoring'])} LLM scoring items "
            f"with {len(table_scoring)} table-derived items (higher confidence)"
        )
        requirements["scoring"] = table_scoring
    if table_quals:
        # Merge: keep LLM quals, append table quals not already present
        existing = set(requirements.get("qualifications", []))
        for q in table_quals:
            if q not in existing:
                requirements.setdefault("qualifications", []).append(q)

    logger.info(
        f"ReqExtractor: extracted {len(requirements['scoring'])} scoring items, "
        f"{len(requirements['qualifications'])} qualifications, "
        f"{len(requirements['tech_specs'])} tech specs"
    )
    if requirements.get("project_name"):
        logger.info(
            f"ReqExtractor: project_name='{requirements['project_name']}', "
            f"bid_number='{requirements.get('bid_number', '')}', "
            f"tenderer_name='{requirements.get('tenderer_name', '')}'"
        )

    # ── 95+优化 补强二: 子类型识别 (仅劳务外包类) ─────────────────────
    bid_subtype = ""
    subtype_info = {}
    if bid_type and ("劳务外包" in bid_type or "劳务管理服务" in bid_type):
        try:
            from core.retrieval.subtype_router import detect_subtype
            # 使用前3000字进行子类型识别
            detect_text = combined[:3000] if combined else ""
            # 获取 LLM 函数 (复用管道的 llm_fn)
            subtype_llm = None
            if llm_fn is None:
                from core.graph import get_pipeline_llm
                subtype_llm = get_pipeline_llm()
            else:
                subtype_llm = llm_fn
            subtype_info = detect_subtype(detect_text, subtype_llm)
            bid_subtype = subtype_info.get("bid_subtype", "")
            if bid_subtype:
                logger.info(
                    f"ReqExtractor: detected bid_subtype='{bid_subtype}' "
                    f"(method={subtype_info.get('method', '?')}, "
                    f"confidence={subtype_info.get('confidence', 0):.2f})"
                )
        except Exception as e:
            logger.warning(f"SubtypeRouter detection failed: {e}")

    result = {
        "requirements": requirements,
        "node_status": {**state.get("node_status", {}), "ReqExtractor": NodeStatus.COMPLETED.value},
    }
    if bid_subtype:
        result["bid_subtype"] = bid_subtype
    return result
