"""AgentState — the unified state object flowing through LangGraph nodes.

Uses TypedDict (required by LangGraph StateGraph for proper state management).
Each node returns a partial dict — LangGraph merges fields using reducers.
"""

from enum import Enum
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


# ── Constants ──────────────────────────────────────────────────────────

# Ordered list of all 14 pipeline nodes (InfoVerificationGate inserted after
# ContractExtractor — extraction phase ends, user verifies before generation)
ALL_NODES = [
    "DocumentParser",
    "ReqExtractor",
    "ContractExtractor",
    "InfoVerificationGate",  # US#6: human checkpoint after extraction
    "EligibilityChecker",
    "TemplateMatcher",
    "SectionGenerator",
    "QualityChecker",
    "FeedbackProcessor",
    "CrossReferenceChecker",
    "ComplianceChecker",
    "ScoreSimulator",
    "HumanReviewGate",
    "DocumentAssembler",
]


# ── Enums ──────────────────────────────────────────────────────────────


class NodeStatus(str, Enum):
    """Execution status of a pipeline node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FeedbackType(str, Enum):
    """Classification of user feedback."""

    CONTENT_FIX = "content_fix"  # 内容修正
    STYLE_ADJUST = "style_adjust"  # 风格调整
    STRUCTURE_OPTIMIZE = "structure"  # 结构优化
    SCORE_ALIGN = "score_align"  # 评分对齐


class FeedbackScope(str, Enum):
    """Scope of a feedback item."""

    GLOBAL = "global"  # Affects all sections
    LOCAL = "local"  # Affects a single section/paragraph


# ── AgentState TypedDict ───────────────────────────────────────────────


class AgentState(TypedDict):
    """Core state object flowing through the LangGraph pipeline.

    This is the single source of truth shared across all 7 nodes.
    LangGraph uses TypedDict field types to determine merge strategy.
    """

    # ── Input ──────────────────────────────────────────────────────────
    # Uploaded documents with parsed content.
    # Each: {"filename": str, "content": str, "type": "pdf"|"docx"}
    documents: list[dict]

    # ── Extracted Tables (P1-1) ────────────────────────────────────────
    # Structured tables pulled directly from the source PDF/DOCX by
    # DocumentParser.  Each: {page, header, rows, type, source}.
    # ReqExtractor consumes scoring/qualification tables directly when
    # available, avoiding a costly LLM call.
    extracted_tables: list[dict]

    # ── Bid Type (Phase B3) ────────────────────────────────────────────
    # The category of bid document being produced.  Drives prompt selection,
    # template filtering, and quality-check weighting throughout the pipeline.
    # Values: "服务类" | "货物类" | "工程类" | "运维类" | "集成类" | "劳务管理服务类" | ...
    bid_type: str

    # ── Bid Subtype (95+优化 补强二) ────────────────────────────────────
    # 劳务外包类下的子类型, 用于子类型分区检索和提示词路由.
    # Values: "食堂餐饮" | "工业生产线" | "保安保洁" | "仓储物流" | "HRO" | "BPO" | ""
    # Set by SubtypeRouter in ReqExtractor, confirmed by user in InfoVerificationGate.
    bid_subtype: str

    # ── User Extra Requirements (补充说明) ─────────────────────────────
    # Free-text from the upload panel.  ContractExtractor will call LLM to
    # extract structured fields (bidder_name, project_location, etc.) and
    # rewrite strategy guidance into project_contract.notes.
    extra_reqs: str

    # ── Document Chunk Size ────────────────────────────────────────────
    # Controls the character length of text chunks produced by DocumentParser.
    # Smaller → finer retrieval granularity but may lose context.
    # Larger → more context per chunk but slower LLM processing.
    # Default: 1000 (set by upload panel, falls back to doc_parser.CHUNK_SIZE)
    chunk_size: int

    # ── Project Context Contract ───────────────────────────────────────
    # Built by ContractExtractor from extra_reqs (LLM) + requirements (招标文件)
    # + form fields (user input).  Injected into every section prompt to
    # guarantee cross-chapter consistency on project name, bidder, industry, etc.
    project_contract: dict  # ProjectContextContract.to_dict()

    # ── Extracted Requirements ─────────────────────────────────────────
    requirements: dict  # {"scoring": [...], "qualifications": [...], "tech_specs": [...], "format_rules": {...}}

    # ── Info Verification Gate (US#6) ──────────────────────────────────
    # Human checkpoint after extraction, before generation.
    # "pending" | "confirmed" — pipeline pauses when pending; GUI resumes
    # after the user reviews/edits the extracted key information.
    info_verification_status: str
    # Summary of extracted key info for GUI display — includes field_sources
    # annotating where each value came from (招标文件 / 补充说明 / LLM / 用户填写 / 缺失).
    info_summary: dict
    # User-confirmed/edited field values (highest priority in ContractExtractor
    # merge).  Applied via apply_user_corrections() after the user clicks confirm.
    user_confirmed_fields: dict

    # ── Eligibility Check (Phase C1) ───────────────────────────────────
    # L1 decision gate: enterprise qualification vs tender threshold.
    # verdict: "PASS" | "FAIL" | "WARNING"
    eligibility_report: dict

    # ── Template Matching ──────────────────────────────────────────────
    matched_templates: list[str]  # Template IDs, sorted by similarity desc
    selected_template_id: str  # User-confirmed template identifier

    # ── Generated Content ──────────────────────────────────────────────
    sections: dict[str, str]  # {"章节名": "生成内容"}
    current_section: str  # Currently generating section name

    # ── Quality Check ──────────────────────────────────────────────────
    quality_report: dict  # {"verdict": "PASS"|"FAIL", "completeness": {...}, ...}

    # ── Cross-Reference Check (Phase C3) ───────────────────────────────
    # Cross-section consistency (personnel counts, amounts, dates).
    cross_ref_report: dict

    # ── Compliance Check (Phase C2) ────────────────────────────────────
    # Format + legal compliance (absolute terms, fabricated performance).
    compliance_report: dict

    # ── Score Simulation (Phase B4) ────────────────────────────────────
    # Predicted evaluation scores per scoring item + total + rank estimate.
    score_simulation: dict

    # ── Human Review Gate (P1-3) ───────────────────────────────────────
    # "pending" | "approved" | "rejected"
    review_status: str
    review_comments: list[str]
    review_context: dict

    # ── Feedback Loop ──────────────────────────────────────────────────
    feedback_history: list[dict]  # list[FeedbackRecord]
    global_constraints: list[str]  # Layer 1 — never discarded
    compressed_history: str  # Layer 2 — summary of older rounds
    context_window_size: int  # Layer 3 — recent N rounds kept (default 3)
    current_round: int  # Current revision round (0 = first draft)
    max_rounds: int  # Maximum revision rounds (default 3)

    # ── Node Status Tracking ───────────────────────────────────────────
    node_status: dict[str, str]  # node_name → NodeStatus value

    # ── Export ────────────────────────────────────────────────────────
    # Path to the generated DOCX file.  Set by DocumentAssembler.
    # N01 fix: previously not in TypedDict → LangGraph silently dropped it.
    export_path: str

    # ── Messages ───────────────────────────────────────────────────────
    # Annotated with add_messages reducer — appends, not overwrites
    messages: Annotated[list, add_messages]


# ── Factory ────────────────────────────────────────────────────────────


def factory_state(**overrides) -> AgentState:
    """Factory helper: create a fully initialized AgentState with optional overrides.

    Dict-type fields (node_status, quality_report, requirements) are deep-merged
    so partial overrides don't lose default keys.
    """
    state: AgentState = {
        "documents": [],
        "extracted_tables": [],  # P1-1
        "bid_type": "",  # Phase B3: empty = auto-detect / use generic prompts
        "bid_subtype": "",  # 95+优化 补强二: 劳务外包子类型
        "extra_reqs": "",  # 补充说明, 由上传页面传入
        "chunk_size": 1000,  # 文档分块大小, 由上传页面传入
        "project_contract": {},  # ProjectContextContract 序列化, 由 ContractExtractor 构建
        "requirements": {
            "scoring": [],
            "qualifications": [],
            "tech_specs": [],
            "format_rules": {},
        },
        "info_verification_status": "",  # US#6
        "info_summary": {},  # US#6
        "user_confirmed_fields": {},  # US#6
        "eligibility_report": {},  # Phase C1
        "matched_templates": [],
        "selected_template_id": "",
        "sections": {},
        "current_section": "",
        "quality_report": {
            "verdict": "PASS",
            "completeness": {},
            "compliance": [],
            "consistency": [],
            "total_items": 0,
            "passed_items": 0,
        },
        "cross_ref_report": {},  # Phase C3
        "compliance_report": {},  # Phase C2
        "score_simulation": {},  # Phase B4
        "review_status": "",  # P1-3
        "review_comments": [],  # P1-3
        "review_context": {},  # P1-3
        "feedback_history": [],
        "global_constraints": [],
        "compressed_history": "",
        "context_window_size": 3,
        "current_round": 0,
        "max_rounds": 3,
        "node_status": {name: NodeStatus.PENDING.value for name in ALL_NODES},
        "messages": [],
        "export_path": "",  # N01 fix: ensure LangGraph preserves this field
    }

    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            merged = {**state[key], **value}
            state[key] = merged  # type: ignore[literal-required]
        else:
            state[key] = value  # type: ignore[literal-required]

    return state
