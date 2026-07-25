"""LangGraph state machine — the 14-node pipeline with conditional routing.

N13 fix: updated from "7-node" to reflect the current 14-node architecture
(including InfoVerificationGate, CrossReferenceChecker, ComplianceChecker,
ScoreSimulator, HumanReviewGate).

Pipeline flow (headless mode — build_graph):
    DocumentParser → ReqExtractor → ContractExtractor → InfoVerificationGate
    → EligibilityChecker → TemplateMatcher → SectionGenerator → QualityChecker
    → CrossReferenceChecker → ComplianceChecker → ScoreSimulator
    → HumanReviewGate → DocumentAssembler → END

Pipeline flow (interactive mode — build_generation_graph):
    Same as above, but HumanReviewGate runs WITHOUT auto_approve.  The pipeline
    pauses at HumanReviewGate (review_status="pending" → __end__).  The GUI's
    review_panel displays sections for user review.

    R02 fix: review_panel now calls resume_after_review() to inject the
    human verdict:
    - approved  → review_panel directly calls doc_assembler() to generate DOCX
    - rejected  → review_panel re-invokes build_generation_graph with
                  review_status="rejected", which routes to FeedbackProcessor
                  → SectionGenerator (revision loop) → ... → HumanReviewGate
                  (pending again for the next review round)

    The export_panel remains as a fallback for manual DOCX export.

LLM Injection (Phase A1 fix):
    LangGraph's add_node only accepts ``(state) -> dict`` signatures and cannot
    pass extra kwargs like ``llm_fn``.  We use ``functools.partial`` to pre-bind
    the LLM callable onto each node before registration, so every node actually
    receives a real ``llm_fn`` instead of silently falling back to mock.
"""

import functools
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from langgraph.graph import END, StateGraph

from core.state import AgentState, NodeStatus, factory_state

logger = logging.getLogger(__name__)

# ── Node Imports ───────────────────────────────────────────────────────

from core.nodes.doc_parser import document_parser
from core.nodes.req_extractor import req_extractor
from core.nodes.contract_extractor import contract_extractor
from core.nodes.info_verification_gate import info_verification_gate, apply_user_corrections  # noqa: F401
from core.nodes.eligibility_checker import eligibility_checker
from core.nodes.template_matcher import template_matcher, build_default_search_fn
from core.nodes.section_generator import generate_all_sections_parallel as section_generator
from core.nodes.quality_checker import quality_checker
from core.nodes.feedback_processor import feedback_processor
from core.nodes.cross_reference_checker import cross_reference_checker
from core.nodes.compliance_checker import compliance_checker
from core.nodes.score_simulator import score_simulator
from core.nodes.human_review_gate import human_review_gate
from core.nodes.doc_assembler import doc_assembler

# Nodes that accept an ``llm_fn`` keyword argument and therefore benefit from
# partial binding.  DocumentParser / TemplateMatcher / DocumentAssembler /
# EligibilityChecker / CrossReferenceChecker / ComplianceChecker do not call
# an LLM directly in MVP, so they are registered as-is.
_LLM_NODES = ("ReqExtractor", "ContractExtractor", "SectionGenerator", "QualityChecker", "FeedbackProcessor", "ScoreSimulator")


# ── Conditional Routing ────────────────────────────────────────────────


def route_after_quality_check(state: AgentState) -> Literal["CrossReferenceChecker", "FeedbackProcessor"]:
    """Conditional edge: PASS → CrossReferenceChecker, FAIL → FeedbackProcessor.

    Phase C2/C3: PASS now routes through the L5 validation chain
    (CrossReferenceChecker → ComplianceChecker → ScoreSimulator) before
    reaching DocumentAssembler.

    Also checks max_rounds to prevent infinite loops.
    """
    verdict = state["quality_report"].get("verdict", "PASS")

    if verdict == "PASS":
        return "CrossReferenceChecker"

    # Check if we've exceeded max rounds
    if state["current_round"] >= state.get("max_rounds", 3):
        # Force assembly even with FAIL — we've hit the revision limit.
        # Still route through the validation chain so the user sees the score.
        return "CrossReferenceChecker"

    return "FeedbackProcessor"


def route_after_cross_ref(state: AgentState) -> Literal["ComplianceChecker", "FeedbackProcessor"]:
    """Phase C3: cross-reference FAIL → feedback loop, PASS → ComplianceChecker."""
    verdict = state.get("cross_ref_report", {}).get("verdict", "PASS")
    if verdict == "FAIL" and state["current_round"] < state.get("max_rounds", 3):
        return "FeedbackProcessor"
    return "ComplianceChecker"


def route_after_compliance(state: AgentState) -> Literal["ScoreSimulator", "FeedbackProcessor"]:
    """Phase C2: compliance FAIL → feedback loop, PASS → ScoreSimulator."""
    verdict = state.get("compliance_report", {}).get("verdict", "PASS")
    if verdict == "FAIL" and state["current_round"] < state.get("max_rounds", 3):
        return "FeedbackProcessor"
    return "ScoreSimulator"


def route_after_review(state: AgentState) -> Literal["DocumentAssembler", "FeedbackProcessor", "__end__"]:
    """P1-3: human review verdict determines next step.

    - approved  → DocumentAssembler (final assembly)
    - rejected  → FeedbackProcessor (revise based on reviewer comments)
    - pending   → __end__ (pause for human input; GUI resumes later)
    """
    status = state.get("review_status", "")
    if status == "approved":
        return "DocumentAssembler"
    if status == "rejected":
        if state["current_round"] < state.get("max_rounds", 3):
            return "FeedbackProcessor"
        # Exceeded max rounds — force assembly even after rejection
        logger.warning("HumanReviewGate rejected but max_rounds reached — forcing assembly")
        return "DocumentAssembler"
    # Pending or empty → pause pipeline (interactive mode)
    return "__end__"


def route_after_feedback(state: AgentState) -> Literal["SectionGenerator", "__end__"]:
    """After processing feedback, determine next step.

    Returns to SectionGenerator for content revision.
    FIXME (T018): End pipeline in error state if feedback is too vague.
    """
    return "SectionGenerator"


def route_after_parser(state: AgentState) -> Literal["ReqExtractor", "__end__"]:
    """Phase B5: early termination if DocumentParser failed.

    If no documents parsed successfully, there is nothing to extract —
    continuing would waste an LLM call on empty content.
    """
    status = state.get("node_status", {}).get("DocumentParser", "")
    if status == NodeStatus.FAILED.value:
        logger.warning("DocumentParser FAILED — terminating pipeline early")
        return "__end__"
    return "ReqExtractor"


def route_after_extraction(state: AgentState) -> Literal["ContractExtractor", "__end__"]:
    """Phase B5: early termination if ReqExtractor failed.

    If requirement extraction failed, there are no scoring items to guide
    generation — continuing produces a generic, untargeted bid document.
    """
    status = state.get("node_status", {}).get("ReqExtractor", "")
    if status == NodeStatus.FAILED.value:
        logger.warning("ReqExtractor FAILED — terminating pipeline early")
        return "__end__"
    return "ContractExtractor"


def route_after_verification(state: AgentState) -> Literal["EligibilityChecker", "__end__"]:
    """US#6: route after InfoVerificationGate.

    - confirmed → EligibilityChecker (Phase 2 generation begins)
    - pending   → __end__ (pause for human input; GUI resumes later)
    """
    status = state.get("info_verification_status", "")
    if status == "confirmed":
        return "EligibilityChecker"
    # Pending or empty → pause pipeline (interactive mode)
    return "__end__"


def route_after_eligibility(state: AgentState) -> Literal["TemplateMatcher"]:
    """Phase C1: route based on enterprise qualification check result.

    F3 fix: Previously a FAIL verdict terminated the pipeline silently
    (return "__end__"), causing zero output with no user-visible error.
    In practice, real tender documents almost always trigger FAIL because
    company_quals.json cannot perfectly cover all extracted requirements.

    Now ALL verdicts (PASS / WARNING / FAIL) route to TemplateMatcher so the
    user always gets a draft document.  The ``eligibility_report`` remains
    in state for the user to review — they can decide whether to proceed
    with a bid that fails hard eligibility thresholds.
    """
    report = state.get("eligibility_report", {})
    verdict = report.get("verdict", "PASS")
    if verdict == "FAIL":
        missing = report.get("missing_quals", [])
        logger.warning(
            f"EligibilityChecker FAIL — missing {len(missing)} quals: "
            f"{missing[:3]}. Continuing pipeline (user will see eligibility "
            f"report in final state)."
        )
    return "TemplateMatcher"


# ── Graph Construction ─────────────────────────────────────────────────


def build_graph(
    llm_fns: dict[str, Callable[[str], str] | None] | None = None,
    search_fn: Callable[[str, int], list[tuple[str, float]]] | None = None,
) -> StateGraph:
    """Construct and compile the full LangGraph pipeline (headless mode).

    InfoVerificationGate is registered with ``auto_confirm=True`` so the
    pipeline runs end-to-end without pausing.  For interactive (GUI) mode,
    use ``build_extraction_graph()`` + ``build_generation_graph()`` instead.

    Args:
        llm_fns: Optional mapping of node_name → LLM callable.  When provided,
            each LLM-consuming node is registered via ``functools.partial`` so
            that the real ``llm_fn`` reaches it instead of silently defaulting
            to ``None`` (the root cause of the mock-only bug in workflow-review
            §漏洞1).  Nodes absent from the dict fall back to their built-in
            behaviour (global LLM or mock).
        search_fn: Optional search function for TemplateMatcher. If None,
            attempts to build a default one from the template library (BUG-05 fix).

    Returns:
        A compiled StateGraph ready for invocation.
    """
    llm_fns = llm_fns or {}

    # BUG-05 fix: inject search_fn into TemplateMatcher so it actually searches
    # the template library instead of always returning empty matches.
    if search_fn is None:
        search_fn = build_default_search_fn()

    def _bind(node_name: str, fn):
        """Pre-bind ``llm_fn`` onto a node if one is configured for it."""
        if node_name in llm_fns and llm_fns[node_name] is not None:
            return functools.partial(fn, llm_fn=llm_fns[node_name])
        return fn

    # Create graph with AgentState as the state schema
    graph = StateGraph(AgentState)

    # ── Add all 14 nodes (LLM nodes get partial-bound llm_fn) ─────────
    graph.add_node("DocumentParser", document_parser)
    graph.add_node("ReqExtractor", _bind("ReqExtractor", req_extractor))
    graph.add_node("ContractExtractor", _bind("ContractExtractor", contract_extractor))
    # US#6: auto_confirm=True in headless mode (no GUI to pause for)
    graph.add_node("InfoVerificationGate", functools.partial(info_verification_gate, auto_confirm=True))
    graph.add_node("EligibilityChecker", eligibility_checker)
    # BUG-05 fix: inject search_fn so TemplateMatcher actually queries the template library
    graph.add_node("TemplateMatcher", functools.partial(template_matcher, search_fn=search_fn) if search_fn else template_matcher)
    graph.add_node("SectionGenerator", _bind("SectionGenerator", section_generator))
    graph.add_node("QualityChecker", _bind("QualityChecker", quality_checker))
    graph.add_node("FeedbackProcessor", _bind("FeedbackProcessor", feedback_processor))
    graph.add_node("CrossReferenceChecker", cross_reference_checker)
    graph.add_node("ComplianceChecker", compliance_checker)
    graph.add_node("ScoreSimulator", _bind("ScoreSimulator", score_simulator))
    # Headless mode: auto_approve=True so pipeline runs end-to-end without pausing
    graph.add_node("HumanReviewGate", functools.partial(human_review_gate, auto_approve=True))
    graph.add_node("DocumentAssembler", doc_assembler)

    # ── Define edges ───────────────────────────────────────────────────
    # Main flow with early-failure routing (Phase B5 + C1)
    graph.add_conditional_edges(
        "DocumentParser",
        route_after_parser,
        {"ReqExtractor": "ReqExtractor", "__end__": END},
    )
    graph.add_conditional_edges(
        "ReqExtractor",
        route_after_extraction,
        {"ContractExtractor": "ContractExtractor", "__end__": END},
    )
    # US#6: ContractExtractor → InfoVerificationGate → (confirmed) → EligibilityChecker
    graph.add_edge("ContractExtractor", "InfoVerificationGate")
    graph.add_conditional_edges(
        "InfoVerificationGate",
        route_after_verification,
        {"EligibilityChecker": "EligibilityChecker", "__end__": END},
    )
    # F3 fix: EligibilityChecker always routes to TemplateMatcher (even on FAIL).
    # Previously FAIL → __end__ caused silent zero-output dead end.
    graph.add_conditional_edges(
        "EligibilityChecker",
        route_after_eligibility,
        {"TemplateMatcher": "TemplateMatcher"},
    )
    graph.add_edge("TemplateMatcher", "SectionGenerator")
    graph.add_edge("SectionGenerator", "QualityChecker")

    # L5 validation chain: QualityChecker → CrossRef → Compliance → ScoreSim
    graph.add_conditional_edges(
        "QualityChecker",
        route_after_quality_check,
        {
            "CrossReferenceChecker": "CrossReferenceChecker",
            "FeedbackProcessor": "FeedbackProcessor",
        },
    )
    graph.add_conditional_edges(
        "CrossReferenceChecker",
        route_after_cross_ref,
        {
            "ComplianceChecker": "ComplianceChecker",
            "FeedbackProcessor": "FeedbackProcessor",
        },
    )
    graph.add_conditional_edges(
        "ComplianceChecker",
        route_after_compliance,
        {
            "ScoreSimulator": "ScoreSimulator",
            "FeedbackProcessor": "FeedbackProcessor",
        },
    )

    # Retry loop: FeedbackProcessor → SectionGenerator
    graph.add_conditional_edges(
        "FeedbackProcessor",
        route_after_feedback,
        {
            "SectionGenerator": "SectionGenerator",
        },
    )

    # ScoreSimulator → HumanReviewGate (P1-3: mandatory human checkpoint)
    graph.add_edge("ScoreSimulator", "HumanReviewGate")

    # P1-3: HumanReviewGate → DocumentAssembler (approved) / FeedbackProcessor (rejected) / END (pending)
    graph.add_conditional_edges(
        "HumanReviewGate",
        route_after_review,
        {
            "DocumentAssembler": "DocumentAssembler",
            "FeedbackProcessor": "FeedbackProcessor",
            "__end__": END,
        },
    )

    # Terminal
    graph.add_edge("DocumentAssembler", END)

    # ── Entry point ────────────────────────────────────────────────────
    graph.set_entry_point("DocumentParser")

    return graph.compile()


# ── Two-Phase Sub-Graphs (US#6: interactive mode) ──────────────────────


def build_extraction_graph(
    llm_fns: dict[str, Callable[[str], str] | None] | None = None,
) -> StateGraph:
    """Construct Phase 1: extraction sub-graph (interactive mode).

    DocumentParser → ReqExtractor → ContractExtractor → InfoVerificationGate → END

    InfoVerificationGate runs in interactive mode (auto_confirm=False), so it
    sets ``info_verification_status="pending"`` and the pipeline naturally
    terminates at END.  The GUI displays the verification form, and when the
    user confirms, calls ``apply_user_corrections()`` and then invokes
    ``build_generation_graph()`` with the updated state.

    Args:
        llm_fns: Optional node→LLM mapping (same as build_graph).

    Returns:
        A compiled StateGraph for the extraction phase.
    """
    llm_fns = llm_fns or {}

    def _bind(node_name: str, fn):
        if node_name in llm_fns and llm_fns[node_name] is not None:
            return functools.partial(fn, llm_fn=llm_fns[node_name])
        return fn

    graph = StateGraph(AgentState)

    graph.add_node("DocumentParser", document_parser)
    graph.add_node("ReqExtractor", _bind("ReqExtractor", req_extractor))
    graph.add_node("ContractExtractor", _bind("ContractExtractor", contract_extractor))
    # Interactive mode: auto_confirm=False (default) → pauses for GUI
    graph.add_node("InfoVerificationGate", info_verification_gate)

    graph.add_conditional_edges(
        "DocumentParser",
        route_after_parser,
        {"ReqExtractor": "ReqExtractor", "__end__": END},
    )
    graph.add_conditional_edges(
        "ReqExtractor",
        route_after_extraction,
        {"ContractExtractor": "ContractExtractor", "__end__": END},
    )
    graph.add_edge("ContractExtractor", "InfoVerificationGate")
    # In extraction phase, the gate always returns "pending" (auto_confirm=False).
    # The pipeline terminates here; the GUI displays the verification form and
    # later invokes build_generation_graph() with the confirmed state.
    graph.add_edge("InfoVerificationGate", END)

    graph.set_entry_point("DocumentParser")
    return graph.compile()


def build_generation_graph(
    llm_fns: dict[str, Callable[[str], str] | None] | None = None,
    search_fn: Callable[[str, int], list[tuple[str, float]]] | None = None,
) -> StateGraph:
    """Construct Phase 2: generation sub-graph (interactive mode).

    N02/R02 design note: In interactive mode, HumanReviewGate is registered
    WITHOUT auto_approve.  The pipeline pauses at HumanReviewGate
    (review_status="pending" → __end__).  The GUI's review_panel calls
    resume_after_review() with the human verdict:
    - approved → directly calls doc_assembler() for DOCX generation
    - rejected → re-invokes this graph, triggering FeedbackProcessor →
      SectionGenerator revision loop, then back to HumanReviewGate.

    EligibilityChecker → TemplateMatcher → SectionGenerator → QualityChecker
    → CrossReferenceChecker → ComplianceChecker → ScoreSimulator
    → HumanReviewGate → [pending → END; approved → DocumentAssembler; rejected → FeedbackProcessor]

    Entry point is EligibilityChecker.  The state passed in should already
    have ``info_verification_status="confirmed"`` and ``user_confirmed_fields``
    populated by ``apply_user_corrections()``.

    Args:
        llm_fns: Optional node→LLM mapping (same as build_graph).
        search_fn: Optional search function for TemplateMatcher (BUG-05 fix).

    Returns:
        A compiled StateGraph for the generation phase.
    """
    llm_fns = llm_fns or {}

    # BUG-05 fix: inject search_fn into TemplateMatcher
    if search_fn is None:
        search_fn = build_default_search_fn()

    def _bind(node_name: str, fn):
        if node_name in llm_fns and llm_fns[node_name] is not None:
            return functools.partial(fn, llm_fn=llm_fns[node_name])
        return fn

    graph = StateGraph(AgentState)

    graph.add_node("EligibilityChecker", eligibility_checker)
    # BUG-05 fix: inject search_fn so TemplateMatcher actually queries the template library
    graph.add_node("TemplateMatcher", functools.partial(template_matcher, search_fn=search_fn) if search_fn else template_matcher)
    graph.add_node("SectionGenerator", _bind("SectionGenerator", section_generator))
    graph.add_node("QualityChecker", _bind("QualityChecker", quality_checker))
    graph.add_node("FeedbackProcessor", _bind("FeedbackProcessor", feedback_processor))
    graph.add_node("CrossReferenceChecker", cross_reference_checker)
    graph.add_node("ComplianceChecker", compliance_checker)
    graph.add_node("ScoreSimulator", _bind("ScoreSimulator", score_simulator))
    graph.add_node("HumanReviewGate", human_review_gate)
    graph.add_node("DocumentAssembler", doc_assembler)

    # F3 fix: EligibilityChecker always routes to TemplateMatcher (even on FAIL).
    graph.add_conditional_edges(
        "EligibilityChecker",
        route_after_eligibility,
        {"TemplateMatcher": "TemplateMatcher"},
    )
    graph.add_edge("TemplateMatcher", "SectionGenerator")
    graph.add_edge("SectionGenerator", "QualityChecker")

    graph.add_conditional_edges(
        "QualityChecker",
        route_after_quality_check,
        {
            "CrossReferenceChecker": "CrossReferenceChecker",
            "FeedbackProcessor": "FeedbackProcessor",
        },
    )
    graph.add_conditional_edges(
        "CrossReferenceChecker",
        route_after_cross_ref,
        {
            "ComplianceChecker": "ComplianceChecker",
            "FeedbackProcessor": "FeedbackProcessor",
        },
    )
    graph.add_conditional_edges(
        "ComplianceChecker",
        route_after_compliance,
        {
            "ScoreSimulator": "ScoreSimulator",
            "FeedbackProcessor": "FeedbackProcessor",
        },
    )
    graph.add_conditional_edges(
        "FeedbackProcessor",
        route_after_feedback,
        {"SectionGenerator": "SectionGenerator"},
    )

    graph.add_edge("ScoreSimulator", "HumanReviewGate")
    graph.add_conditional_edges(
        "HumanReviewGate",
        route_after_review,
        {
            "DocumentAssembler": "DocumentAssembler",
            "FeedbackProcessor": "FeedbackProcessor",
            "__end__": END,
        },
    )
    graph.add_edge("DocumentAssembler", END)

    graph.set_entry_point("EligibilityChecker")
    return graph.compile()


# ── Convenience ────────────────────────────────────────────────────────

# Global LLM function — set via set_pipeline_llm() before running pipeline.
# If None, nodes use their built-in mock.
_pipeline_llm_fn = None


def set_pipeline_llm(fn) -> None:
    """Set the LLM function to use for all pipeline nodes.

    Args:
        fn: A callable (prompt: str) -> str, or None to use mock.
    """
    global _pipeline_llm_fn
    _pipeline_llm_fn = fn


def get_pipeline_llm():
    """Get the currently configured LLM function."""
    return _pipeline_llm_fn


def run_pipeline(
    initial_state: AgentState | None = None,
    llm_fns: dict[str, Callable[[str], str] | None] | None = None,
) -> AgentState:
    """Run the full pipeline with an optional initial state.

    Args:
        initial_state: Optional pre-populated AgentState. Creates default if None.
        llm_fns: Optional node→LLM mapping forwarded to ``build_graph``.

    Returns:
        The final AgentState after all nodes complete.
    """
    app = build_graph(llm_fns=llm_fns)
    state = initial_state if initial_state is not None else factory_state()
    result = app.invoke(state)
    return result


# ── Pipeline snapshot export ────────────────────────────────────────────

# Node display order and labels for snapshot naming
_NODE_LABELS = [
    ("DocumentParser", "文档解析"),
    ("ReqExtractor", "需求提取"),
    ("ContractExtractor", "契约构建"),
    ("InfoVerificationGate", "信息校验"),
    ("EligibilityChecker", "资质校验"),
    ("TemplateMatcher", "模板匹配"),
    ("SectionGenerator", "章节生成"),
    ("QualityChecker", "质量检查"),
    ("FeedbackProcessor", "反馈处理"),
    ("CrossReferenceChecker", "跨章一致性"),
    ("ComplianceChecker", "合规审查"),
    ("ScoreSimulator", "评分模拟"),
    ("HumanReviewGate", "人工审核"),
    ("DocumentAssembler", "文档装配"),
]

def _serialize_state(snapshot: dict) -> dict:
    """Deep-copy the full state snapshot for JSON export — no truncation."""
    import copy

    return copy.deepcopy(snapshot)


def run_pipeline_with_snapshots(
    initial_state: AgentState | None = None,
    export_dir: str | None = None,
    llm_fns: dict[str, Callable[[str], str] | None] | None = None,
) -> tuple[AgentState, list[str]]:
    """Run the pipeline and save a JSON snapshot after every node.

    Uses LangGraph's ``stream(mode="updates")`` to capture the actual node
    name and state delta after each node executes.  Each snapshot is written
    as a separate JSON file so you can inspect the pipeline's intermediate
    outputs.

    Args:
        initial_state: Optional pre-populated AgentState.
        export_dir: Directory for snapshot files.
                    Defaults to ``<project_root>/data/exports/pipeline_snapshots/``.
        llm_fns: Optional node→LLM mapping forwarded to ``build_graph``.

    Returns:
        (final_state, snapshot_paths) — final AgentState and list of JSON file paths.
    """
    app = build_graph(llm_fns=llm_fns)
    state = initial_state if initial_state is not None else factory_state()

    # Determine export directory
    if export_dir is None:
        from config.settings import get_settings
        try:
            s = get_settings()
            base = s.resolve_path(s.get("paths.export_dir", "data/exports"))
        except Exception:
            base = Path("data/exports")
        export_dir = str(base / "pipeline_snapshots")

    snap_dir = Path(export_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Timestamp for this run
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # BUG-13 fix: use stream_mode="updates" to get the actual node name from
    # each chunk, instead of relying on a linear step counter that doesn't
    # match the real execution sequence (feedback loops, conditional routing).
    _LABEL_MAP = {name: label for name, label in _NODE_LABELS}

    step = 0
    snapshot_paths: list[str] = []
    last_state: dict = dict(state)  # running full state

    # stream(mode="updates") yields {node_name: state_update_dict} per step
    for chunk in app.stream(state, stream_mode="updates"):
        # chunk is {node_name: {update_dict}} — may have multiple nodes
        for node_name, update in chunk.items():
            # Merge the update into the running state
            if isinstance(update, dict):
                last_state.update(update)

            label = _LABEL_MAP.get(node_name, node_name)

            filename = f"{ts}_{step + 1:02d}_{node_name}.json"
            filepath = snap_dir / filename

            sanitized = _serialize_state(dict(last_state))
            sanitized["_meta"] = {
                "node": node_name,
                "label": label,
                "step": step,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(sanitized, f, ensure_ascii=False, indent=2)

            snapshot_paths.append(str(filepath))
            logger.info(f"Snapshot saved: {filepath}")

            step += 1

    logger.info(f"Pipeline complete — {len(snapshot_paths)} snapshots saved to {snap_dir}")

    return last_state, snapshot_paths
