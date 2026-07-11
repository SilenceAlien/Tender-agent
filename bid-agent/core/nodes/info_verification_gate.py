"""InfoVerificationGate node — human checkpoint after extraction, before generation.

US#6: inserted between ContractExtractor and EligibilityChecker.  The pipeline
pauses here so the user can review/edit the extracted key information (project
name, bid number, tenderer, bidder, etc.) before the expensive LLM generation
phase begins.

Two operating modes (mirrors HumanReviewGate's pattern):
    1. Interactive (Streamlit GUI): the node sets ``info_verification_status="pending"``
       and returns; the GUI displays the verification form and calls
       ``apply_user_corrections()`` with the user's confirmed/edited values.
    2. Headless (CI / batch): ``auto_confirm=True`` — the gate auto-passes
       without human input (useful for testing, NOT for production).

Contract:
    def info_verification_gate(state) -> dict
    Input:  state.requirements, state.project_contract, state.bid_type
    Output: {info_verification_status: "pending"|"confirmed",
             info_summary: {...},
             node_status}
    Routing:
        confirmed → EligibilityChecker (Phase 2 begins)
        pending   → __end__ (pause for human input; GUI resumes later)
"""

import logging
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)


# ── Key fields displayed in the verification form ───────────────────────

# Fields shown to the user for review, in display order.
# Each entry: (state_key, display_label, required)
_VERIFICATION_FIELDS = [
    ("project_name", "项目名称", True),
    ("bid_number", "招标编号", True),
    ("tenderer_name", "招标人", True),
    ("package_number", "包件号", False),
    ("bidder_name", "投标人", True),
    ("project_location", "项目地点", False),
    ("industry", "行业属性", False),
    ("duration", "工期/服务期", False),
    ("warranty", "质保期", False),
    ("service_target", "服务对象", False),
]


# ── Field source annotation ─────────────────────────────────────────────


def _annotate_field_sources(
    requirements: dict,
    project_contract: dict,
    user_fields: dict,
) -> dict[str, str]:
    """Annotate where each field's value originated.

    Priority: 用户填写 > 招标文件 > 补充说明 > 推断 > 缺失

    Args:
        requirements: Extracted by ReqExtractor from the tender document.
        project_contract: Built by ContractExtractor (LLM + requirements merge).
        user_fields: Form fields from the upload panel (form_bidder_name etc.).

    Returns:
        {field_name: source_label} where source_label is one of:
        "用户填写" | "招标文件" | "补充说明" | "缺失"
    """
    sources: dict[str, str] = {}

    # Fields that come primarily from requirements (招标文件)
    req_fields = {
        "project_name": requirements.get("project_name", ""),
        "bid_number": requirements.get("bid_number", ""),
        "tenderer_name": requirements.get("tenderer_name", ""),
        "package_number": requirements.get("package_number", ""),
    }

    # Fields that come primarily from contract (补充说明 / LLM)
    contract_fields = {
        "bidder_name": project_contract.get("bidder_name", ""),
        "project_location": project_contract.get("project_location", ""),
        "industry": project_contract.get("industry", ""),
        "duration": project_contract.get("duration", ""),
        "warranty": project_contract.get("warranty", ""),
        "service_target": project_contract.get("service_target", ""),
    }

    # Cross-source mappings: some fields have secondary sources in contract
    # e.g., bid_number may also come from contract.project_code (LLM extracted)
    contract_secondary = {
        "bid_number": project_contract.get("project_code", ""),
        "project_name": project_contract.get("project_name", ""),
        "tenderer_name": project_contract.get("tenderer_name", ""),
        "package_number": project_contract.get("package_number", ""),
    }

    all_fields = {**req_fields, **contract_fields}

    for field_name, value in all_fields.items():
        # Check user form fields first (highest priority)
        if user_fields.get(field_name):
            sources[field_name] = "用户填写"
        elif value:
            # Value found in primary source
            if field_name in req_fields:
                sources[field_name] = "招标文件"
            else:
                sources[field_name] = "补充说明"
        elif field_name in contract_secondary and contract_secondary[field_name]:
            # Value not in primary source but found in contract (LLM extracted)
            sources[field_name] = "补充说明"
        else:
            sources[field_name] = "缺失"

    return sources


# ── Info summary builder ────────────────────────────────────────────────


def _build_info_summary(state: AgentState) -> dict:
    """Build a summary dict of extracted key info for GUI display.

    Includes:
    - All verification fields with their current values
    - field_sources: where each value came from
    - scoring_count, qualification_count: extracted table stats
    """
    requirements = state.get("requirements", {})
    contract = state.get("project_contract", {})
    user_fields = state.get("user_confirmed_fields", {})

    # Build the field values, prioritizing user_confirmed > contract > requirements
    summary_fields = {}
    for field_name, _, _ in _VERIFICATION_FIELDS:
        if field_name == "bid_number":
            # bid_number in contract is stored as project_code
            summary_fields[field_name] = (
                user_fields.get(field_name, "")
                or requirements.get(field_name, "")
                or contract.get("project_code", "")
            )
        elif field_name == "package_number":
            summary_fields[field_name] = (
                user_fields.get(field_name, "")
                or requirements.get(field_name, "")
                or contract.get("package_number", "")
            )
        elif field_name in ("project_name", "tenderer_name"):
            summary_fields[field_name] = (
                user_fields.get(field_name, "")
                or requirements.get(field_name, "")
                or contract.get(field_name, "")
            )
        else:
            # Fields primarily from contract
            summary_fields[field_name] = (
                user_fields.get(field_name, "")
                or contract.get(field_name, "")
            )

    # Add bid_type
    summary_fields["bid_type"] = state.get("bid_type", "")

    # Annotate sources
    field_sources = _annotate_field_sources(requirements, contract, user_fields)

    # Count extracted items
    scoring = requirements.get("scoring", [])
    qualifications = requirements.get("qualifications", [])

    return {
        **summary_fields,
        "field_sources": field_sources,
        "scoring_count": len(scoring),
        "qualification_count": len(qualifications),
    }


# ── LangGraph Node ──────────────────────────────────────────────────────


def info_verification_gate(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
    auto_confirm: bool = False,
) -> dict:
    """Pause the pipeline for human review of extracted key information.

    Args:
        state: AgentState with requirements, project_contract, bid_type
        llm_fn: Unused (accepted for graph binding compatibility)
        auto_confirm: When True, auto-confirm without human input.
                      Should ONLY be True in testing/CI — never in production.

    Returns:
        {info_verification_status, info_summary, node_status}

    In interactive mode (auto_confirm=False), the node sets
    ``info_verification_status="pending"`` and returns.  The GUI displays
    the verification form and calls ``apply_user_corrections()`` to inject
    the user's confirmed/edited values, after which the graph's conditional
    edge routes to EligibilityChecker (Phase 2).
    """
    # Auto-confirm mode (testing only)
    if auto_confirm:
        logger.info("InfoVerificationGate: auto-confirmed (testing mode)")
        summary = _build_info_summary(state)
        return {
            "info_verification_status": "confirmed",
            "info_summary": summary,
            "node_status": {
                **state.get("node_status", {}),
                "InfoVerificationGate": NodeStatus.COMPLETED.value,
            },
        }

    # If the state already carries a confirmed status (e.g. GUI called
    # apply_user_corrections() and re-invoked the graph), respect it.
    existing_status = state.get("info_verification_status", "")
    if existing_status == "confirmed":
        logger.info("InfoVerificationGate: respecting existing confirmed status")
        summary = _build_info_summary(state)
        return {
            "info_verification_status": "confirmed",
            "info_summary": summary,
            "node_status": {
                **state.get("node_status", {}),
                "InfoVerificationGate": NodeStatus.COMPLETED.value,
            },
        }

    # Interactive mode: build summary and mark as pending
    summary = _build_info_summary(state)

    project_name = summary.get("project_name", "(未提取)")
    bidder_name = summary.get("bidder_name", "(未提取)")
    scoring_count = summary.get("scoring_count", 0)
    qual_count = summary.get("qualification_count", 0)

    logger.info(
        f"InfoVerificationGate: awaiting human review "
        f"(project={project_name[:30]}, bidder={bidder_name[:20]}, "
        f"scoring={scoring_count}, quals={qual_count})"
    )

    return {
        "info_verification_status": "pending",
        "info_summary": summary,
        "node_status": {
            **state.get("node_status", {}),
            # RUNNING (not COMPLETED) because the gate is paused waiting
            # for user input — it hasn't finished its work yet.
            "InfoVerificationGate": NodeStatus.RUNNING.value,
        },
    }


def apply_user_corrections(
    state: AgentState,
    corrected_fields: dict,
) -> dict:
    """Apply the user's confirmed/edited field values to the state.

    Called by the GUI after the user reviews the extracted info and clicks
    "confirm".  The corrected fields are stored as ``user_confirmed_fields``
    with the highest priority in ContractExtractor's merge logic.

    Args:
        state: Current AgentState (with info_verification_status="pending")
        corrected_fields: User's confirmed/edited field values, e.g.:
            {"project_name": "XXX项目", "bidder_name": "广州华南人力有限公司"}

    Returns:
        Updated state fragment with:
        - info_verification_status="confirmed"
        - user_confirmed_fields=corrected_fields
        - project_contract: re-merged with user corrections (highest priority)
    """
    from core.contract import ProjectContextContract
    from core.nodes.contract_extractor import _merge_contract_sources

    logger.info(
        f"InfoVerificationGate: user confirmed with {len(corrected_fields)} field corrections"
    )

    # Re-merge the contract with user-confirmed fields as highest priority
    requirements = state.get("requirements", {})
    bid_type = state.get("bid_type", "")
    existing_contract = state.get("project_contract", {})

    # Get the LLM-extracted values from the existing contract for re-merge
    # (we don't have the original LLM output, so we use contract as-is for
    # the "LLM extracted" layer, and let user_confirmed_fields override)
    llm_extracted = {
        "bidder_name": existing_contract.get("bidder_name", ""),
        "project_location": existing_contract.get("project_location", ""),
        "industry": existing_contract.get("industry", ""),
        "duration": existing_contract.get("duration", ""),
        "warranty": existing_contract.get("warranty", ""),
        "service_target": existing_contract.get("service_target", ""),
        "project_name": existing_contract.get("project_name", ""),
        "tenderer_name": existing_contract.get("tenderer_name", ""),
        "project_code": existing_contract.get("project_code", ""),
        "package_number": existing_contract.get("package_number", ""),
        "rewritten_notes": existing_contract.get("notes", ""),
    }

    contract = _merge_contract_sources(
        llm_extracted=llm_extracted,
        requirements=requirements,
        bid_type=bid_type,
        user_confirmed_fields=corrected_fields,
    )

    # Rebuild global_constraints if notes changed
    global_constraints = list(state.get("global_constraints", []))
    if contract.notes and contract.notes not in global_constraints:
        # Replace any existing notes-based constraint
        global_constraints = [c for c in global_constraints if c != existing_contract.get("notes", "")]
        global_constraints.append(contract.notes)

    return {
        "info_verification_status": "confirmed",
        "user_confirmed_fields": corrected_fields,
        "project_contract": contract.to_dict(),
        "global_constraints": global_constraints,
        "node_status": {
            **state.get("node_status", {}),
            "InfoVerificationGate": NodeStatus.COMPLETED.value,
        },
    }
