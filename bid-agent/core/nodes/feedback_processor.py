"""FeedbackProcessor node — classifies and routes user/QA feedback.

Contract (from node_interfaces.md):
    def feedback_processor(state: AgentState) -> dict
    Input:  state.quality_report, state.sections
    Output: {feedback_history: [FeedbackRecord], current_round: +1, node_status: {...}}
    Constraints:
    - Classification: content_fix | style_adjust | structure | score_align
    - Scope: global (all sections) | local (single section/paragraph)
    - target_section extracted from compliance/consistency issues
    - Increments current_round (never exceeds max_rounds in routing)
"""

import logging
from datetime import datetime, timezone
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)


def _learn_consistency_lessons(state: dict, llm_fn: Callable[[str], str] | None = None) -> None:
    """Phase 007: 从当前状态中的一致性问题中自动学习经验。

    在 FeedbackProcessor 处理完反馈后调用。将 cross_ref_report、
    compliance_report、quality_report 中的一致性问题提取为
    可复用经验规则，持久化到 ConsistencyLessonStore。

    默认使用规则模板模式（不传 llm_fn），避免每个问题触发一次 LLM
    调用导致管道延迟。LLM 提取可通过环境变量 BID_CONSISTENCY__LLM_EXTRACT
    设置为 "true" 来显式启用。

    失败时静默处理，不阻断管道流程。
    """
    try:
        import os
        from core.evolution.consistency_lesson_extractor import ConsistencyLessonExtractor
        bid_type = state.get("bid_type", "")
        # 默认不传 llm_fn，使用高效的规则模板模式。
        # 仅在显式启用时才用 LLM 提取更深层规则。
        use_llm = os.environ.get("BID_CONSISTENCY__LLM_EXTRACT", "").lower() in ("true", "1", "yes")
        extract_llm = llm_fn if use_llm else None
        extractor = ConsistencyLessonExtractor(llm_fn=extract_llm)
        ids = extractor.learn_from_state(state, bid_type)
        if ids:
            logger.info(f"Self-evolution: learned {len(ids)} consistency lessons")
    except Exception as e:
        logger.debug(f"Consistency lesson learning skipped: {e}")

# ── Classification ─────────────────────────────────────────────────────


def _classify_issue(issue: str) -> tuple[str, str]:
    """Classify a quality issue into type and scope.

    Returns: (feedback_type, scope)
    """
    issue_lower = issue.lower()

    # Style rules
    style_keywords = ["字体", "字号", "格式", "行距", "页边", "缩进", "页眉"]
    for kw in style_keywords:
        if kw in issue_lower:
            return ("style_adjust", "global")

    # Structure rules
    structure_keywords = ["结构", "层级", "标题", "目录", "顺序", "组织"]
    for kw in structure_keywords:
        if kw in issue_lower:
            return ("structure", "global")

    # Scoring alignment
    score_keywords = ["评分", "score", "得分", "分值"]
    for kw in score_keywords:
        if kw in issue_lower:
            return ("score_align", "global")

    # Default: content fix
    return ("content_fix", "local")


def _extract_section_name(issue: str, sections: dict[str, str]) -> str:
    """Try to extract the target section name from an issue description."""
    # Look for section names in «...» patterns
    import re
    match = re.search(r"[「「]([^」」]+)[」」]", issue)
    if match:
        name = match.group(1)
        if name in sections:
            return name

    # Look for chapter patterns
    chapter_match = re.search(r"第[一二三四五六七八九十\d]+章\s*[^\s，。]*", issue)
    if chapter_match:
        name = chapter_match.group(0)
        if name in sections:
            return name

    # Default: first section
    return next(iter(sections.keys()), "") if sections else ""


# ── LangGraph Node ─────────────────────────────────────────────────────


def feedback_processor(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """Process quality check failures into structured feedback.

    Reads from quality_report.compliance and consistency to build
    FeedbackRecord entries. Increments current_round.

    Args:
        state: AgentState with quality_report and sections
        llm_fn: Optional LLM for deeper feedback analysis

    Returns:
        {feedback_history: [FeedbackRecord], current_round: N, node_status: {...}}
    """
    quality = state.get("quality_report", {})
    sections = state.get("sections", {})
    feedback_history = list(state.get("feedback_history", []))
    current_round = state.get("current_round", 0)

    issues = []
    issues.extend(quality.get("compliance", []))
    issues.extend(quality.get("consistency", []))

    # Also extract completeness issues
    completeness = quality.get("completeness", {})
    for item_name, found in completeness.items():
        if not found:
            issues.append(f"评分项覆盖不足: {item_name}")

    # ── Read cross-reference report issues ──────────────────────────
    # CrossReferenceChecker stores problems in cross_ref_report.inconsistencies,
    # NOT in quality_report. Without reading these, FeedbackProcessor finds no
    # issues when only cross-ref fails, doesn't increment current_round, and the
    # pipeline loops infinitely (BUG-02).
    cross_ref_report = state.get("cross_ref_report", {})
    for inc in cross_ref_report.get("inconsistencies", []):
        detail = inc.get("detail", "")
        if detail:
            issues.append(f"跨章一致性: {detail}")

    # ── Read compliance report issues ───────────────────────────────
    # ComplianceChecker stores problems in compliance_report.format_issues and
    # compliance_report.legal_issues, NOT in quality_report.
    compliance_report = state.get("compliance_report", {})
    for fi in compliance_report.get("format_issues", []):
        detail = fi.get("detail", "")
        if detail:
            issues.append(f"格式合规: {detail}")
    for li in compliance_report.get("legal_issues", []):
        detail = li.get("detail", "")
        if detail:
            issues.append(f"法律合规: {detail}")

    if not issues:
        # No issues → skip feedback processing
        logger.info("No feedback to process")
        return {
            "feedback_history": feedback_history,
            "current_round": current_round,  # Don't increment without issues
            "node_status": {
                **state.get("node_status", {}),
                "FeedbackProcessor": NodeStatus.COMPLETED.value,
            },
        }

    # ── Phase 007: Self-evolution — learn from consistency issues ────
    # Before building feedback records, learn from the consistency issues
    # found in this round. This ensures lessons are captured even if the
    # feedback loop later exceeds max_rounds and forces assembly.
    _learn_consistency_lessons(state, llm_fn)

    # ── Build feedback records ─────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    new_feedbacks = []

    for issue in issues:
        fb_type, fb_scope = _classify_issue(issue)
        target_section = _extract_section_name(issue, sections) if fb_scope == "local" else ""

        record = {
            "round": current_round + 1,
            "feedback_text": issue,
            "feedback_type": fb_type,
            "scope": fb_scope,
            "target_section": target_section,
            "target_paragraph_index": -1,
            "diff_before": "",  # Filled by SectionGenerator during fix
            "diff_after": "",
            "timestamp": now,
        }
        new_feedbacks.append(record)

    logger.info(
        f"FeedbackProcessor: {len(new_feedbacks)} feedback items "
        f"(round {current_round + 1})"
    )

    return {
        "feedback_history": feedback_history + new_feedbacks,
        "current_round": current_round + 1,
        "node_status": {
            **state.get("node_status", {}),
            "FeedbackProcessor": NodeStatus.COMPLETED.value,
        },
    }
