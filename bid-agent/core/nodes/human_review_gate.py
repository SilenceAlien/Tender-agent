"""HumanReviewGate node — mandatory human-in-the-loop checkpoint.

P1-3 (landing-pipeline-expansion.md §4.4): inserted between ScoreSimulator
and DocumentAssembler.  The pipeline must pause here so a human reviewer
can inspect the generated sections + predicted score before the final
DOCX is assembled.

Legal requirement: 标书 must be signed by the legal representative, so a
human review step is not optional — it is a compliance gate.

Two operating modes:
    1. Interactive (Streamlit GUI): the node sets ``pending_review=True``
       and returns; the GUI displays the review panel and calls
       ``resume_after_review()`` with the human verdict.
    2. Headless (CI / batch): configurable via ``auto_approve`` — when
       True, the gate auto-passes (useful for testing but NOT for
       production bids).

Contract:
    def human_review_gate(state) -> dict
    Input:  state.sections, state.score_simulation, state.quality_report
    Output: {review_status: "pending"|"approved"|"rejected",
             review_comments: [...],
             node_status}
    Routing:
        approved  → DocumentAssembler
        rejected  → FeedbackProcessor (with reviewer comments)
"""

import logging
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)


def human_review_gate(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
    auto_approve: bool = False,
) -> dict:
    """Pause the pipeline for mandatory human review.

    Args:
        state: AgentState with sections, score_simulation, quality_report
        llm_fn: Unused (accepted for graph binding compatibility)
        auto_approve: When True, auto-approve without human input.
                      Should ONLY be True in testing/CI — never in production.

    Returns:
        {review_status, review_comments, node_status}

    In interactive mode (auto_approve=False), the node sets
    ``review_status="pending"`` and returns.  The GUI's review_panel
    calls ``resume_after_review()`` to inject the human verdict into
    state, after which the graph's conditional edge routes accordingly
    (R02 fix: previously the GUI never called resume_after_review(),
    making the approved/rejected routes dead code).
    """
    sections = state.get("sections", {})
    score_sim = state.get("score_simulation", {})
    quality = state.get("quality_report", {})

    if not sections:
        # N10 fix: return pending instead of rejected.  Auto-reject would
        # route to FeedbackProcessor and trigger a revision loop with no
        # content to revise.  Pending pauses the pipeline gracefully.
        logger.warning("HumanReviewGate: no sections to review — returning pending")
        return {
            "review_status": "pending",
            "review_comments": ["无章节内容可审核"],
            "node_status": {
                **state.get("node_status", {}),
                "HumanReviewGate": NodeStatus.COMPLETED.value,
            },
        }

    # Auto-approve mode (testing only)
    if auto_approve:
        logger.info("HumanReviewGate: auto-approved (testing mode)")
        return {
            "review_status": "approved",
            "review_comments": [],
            "node_status": {
                **state.get("node_status", {}),
                "HumanReviewGate": NodeStatus.COMPLETED.value,
            },
        }

    # If the state already carries a verdict (e.g. GUI called
    # resume_after_review() and re-invoked the graph), respect it instead
    # of overwriting with "pending".  This makes the gate idempotent.
    existing_status = state.get("review_status", "")
    if existing_status in ("approved", "rejected"):
        logger.info(f"HumanReviewGate: respecting existing verdict = {existing_status}")
        return {
            "review_status": existing_status,
            "review_comments": state.get("review_comments", []),
            "node_status": {
                **state.get("node_status", {}),
                "HumanReviewGate": NodeStatus.COMPLETED.value,
            },
        }

    # Interactive mode: mark as pending and return
    # The GUI will call resume_after_review() with the human verdict
    predicted_score = score_sim.get("total", {}).get("predicted", "N/A")
    max_score = score_sim.get("total", {}).get("max", "N/A")
    rank = score_sim.get("total", {}).get("rank_estimate", "未知")
    quality_verdict = quality.get("verdict", "未知")

    logger.info(
        f"HumanReviewGate: awaiting human review "
        f"(sections={len(sections)}, score={predicted_score}/{max_score}, "
        f"rank={rank}, quality={quality_verdict})"
    )

    return {
        "review_status": "pending",
        "review_comments": [],
        "review_context": {
            "section_count": len(sections),
            "section_names": list(sections.keys()),
            "predicted_score": predicted_score,
            "max_score": max_score,
            "rank_estimate": rank,
            "quality_verdict": quality_verdict,
        },
        "node_status": {
            **state.get("node_status", {}),
            "HumanReviewGate": NodeStatus.COMPLETED.value,
        },
    }


def resume_after_review(
    state: AgentState,
    verdict: str,
    comments: list[str] | None = None,
) -> dict:
    """Apply the human reviewer's verdict to the state.

    Called by the GUI after the reviewer submits their decision.  The
    graph's conditional edge then routes based on the verdict.

    Args:
        state: Current AgentState (with review_status="pending")
        verdict: "approved" or "rejected"
        comments: Optional reviewer comments (required when rejected)

    Returns:
        Updated state fragment with review_status and review_comments.
    """
    comments = comments or []
    if verdict == "rejected" and not comments:
        comments = ["审核未通过（未提供具体意见）"]

    logger.info(f"HumanReviewGate: reviewer verdict = {verdict} ({len(comments)} comments)")

    return {
        "review_status": verdict,
        "review_comments": comments,
        # If rejected, convert reviewer comments into feedback_history
        # entries so FeedbackProcessor + SectionGenerator can act on them
        "feedback_history": (
            state.get("feedback_history", []) + [
                {
                    "round": state.get("current_round", 0),
                    "feedback_text": f"【人工审核意见】{c}",
                    "target_section": "",  # empty = all sections reviewed
                    "scope": "global",
                    "source": "human_review",
                }
                for c in comments
            ]
            if verdict == "rejected"
            else state.get("feedback_history", [])
        ),
    }
