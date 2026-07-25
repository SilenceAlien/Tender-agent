"""FeedbackProcessor node — classifies and routes user/QA feedback.

Contract (from node_interfaces.md):
    def feedback_processor(state: AgentState) -> dict
    Input:  state.quality_report, state.sections, state.feedback_history,
            state.global_constraints, state.compressed_history,
            state.context_window_size
    Output: {feedback_history: [FeedbackRecord], global_constraints: [str],
             compressed_history: str, context_window_size: int,
             current_round: +1, node_status: {...}}
    Constraints:
    - Classification: content_fix | style_adjust | structure | score_align
    - Scope: global (all sections) | local (single section/paragraph)
    - target_section extracted from compliance/consistency issues
    - Increments current_round (never exceeds max_rounds in routing)
    - Three-layer feedback history (M4): global_constraints (Layer 1,
      scope=global, never discarded) / compressed_history (Layer 2,
      dedup+merge) / context_window_size (Layer 3, default 3 → recent N rounds)
    - feedback_history 保持扁平 list[dict]，SectionGenerator 直接消费，向后兼容
"""

import logging
from datetime import datetime, timezone
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Three-layer feedback history (M4 fix) ───────────────────────────────
# 规范 ⑨ 要求反馈历史分三层组织：
#   Layer 1 — global_constraints: 跨所有章节的全局硬性约束，永不丢弃
#   Layer 2 — compressed_history: 压缩后的历史反馈（去重/合并同类）
#   Layer 3 — context_window_size: 默认 3，仅保留最近 N 轮作为当前上下文窗口
# 注意：feedback_history 仍保持扁平 list[dict]（SectionGenerator 直接消费该列表，
# 向后兼容），三层结构由本模块从上述扁平列表派生并回写到 state 对应字段。
DEFAULT_CONTEXT_WINDOW_SIZE = 3


def _feedback_dedup_key(record: dict) -> tuple:
    """合并同类反馈的稳定键。

    相同 (scope, target_section, feedback_type, 归一化问题) 折叠为一条，
    保留**最新**一条（后写入覆盖先写入，last-write-wins）。
    """
    text = (record.get("feedback_text") or "").strip().lower()
    return (
        record.get("scope", ""),
        record.get("target_section", ""),
        record.get("feedback_type", "content_fix"),
        text,
    )


def build_global_constraints(
    history: list[dict], existing: list[str] | None = None
) -> list[str]:
    """Layer 1 — 收集跨章节全局硬性约束（scope=global）。

    跨所有轮次累积、去重，全局约束永不丢弃。
    """
    constraints: list[str] = list(existing or [])
    seen = set(constraints)
    for rec in history:
        if rec.get("scope") == "global":
            text = (rec.get("feedback_text") or "").strip()
            if text and text not in seen:
                constraints.append(text)
                seen.add(text)
    return constraints


def compress_feedback_history(history: list[dict]) -> list[dict]:
    """Layer 2 — 对同类反馈去重/合并，相同问题仅保留最新一条。"""
    merged: dict[tuple, dict] = {}
    for rec in history:
        merged[_feedback_dedup_key(rec)] = rec  # 后续轮次覆盖先前轮次
    return list(merged.values())


def format_compressed_history(compressed: list[dict]) -> str:
    """将压缩后的反馈列表渲染为单一摘要字符串（写入 state.compressed_history）。"""
    if not compressed:
        return ""
    lines = []
    for rec in compressed:
        scope = rec.get("scope", "")
        ftype = rec.get("feedback_type", "content_fix")
        target = rec.get("target_section", "")
        round_no = rec.get("round", "?")
        loc = f"@{target}" if target else ""
        lines.append(
            f"[R{round_no}] ({scope}/{ftype}{loc}) {rec.get('feedback_text', '')}"
        )
    return "\n".join(lines)


def get_recent_context_window(
    history: list[dict], size: int = DEFAULT_CONTEXT_WINDOW_SIZE
) -> list[dict]:
    """Layer 3 — 仅保留最近 `size` 轮反馈，供下游构造 prompt 上下文使用。"""
    if not history or size <= 0:
        return list(history)
    rounds = sorted({rec.get("round", 0) for rec in history})
    recent_rounds = set(rounds[-size:])
    return [rec for rec in history if rec.get("round", 0) in recent_rounds]


def build_feedback_layers(
    history: list[dict], context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE
) -> dict:
    """一次性导出三层结构（供下游消费/调试）。"""
    compressed = compress_feedback_history(history)
    return {
        "global_constraints": build_global_constraints(history),
        "compressed_history": format_compressed_history(compressed),
        "context_window_size": context_window_size,
        "recent_context": get_recent_context_window(history, context_window_size),
    }


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

    # ── M4: 读取三层反馈历史结构（保持向后兼容）──────────────────────
    context_window_size = (
        state.get("context_window_size", DEFAULT_CONTEXT_WINDOW_SIZE)
        or DEFAULT_CONTEXT_WINDOW_SIZE
    )
    global_constraints = list(state.get("global_constraints", []))
    compressed_history = state.get("compressed_history", "")

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
            "global_constraints": global_constraints,
            "compressed_history": compressed_history,
            "context_window_size": context_window_size,
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

    # ── M4: 重构反馈历史为三层结构 ───────────────────────────────────
    full_history = feedback_history + new_feedbacks
    # Layer 1: 跨章节全局约束（scope=global）累积、去重、永不丢弃
    new_global_constraints = build_global_constraints(full_history, global_constraints)
    # Layer 2: 压缩历史（同类去重/合并，保留最新一条）
    new_compressed_history = format_compressed_history(
        compress_feedback_history(full_history)
    )
    # Layer 3: context_window_size 常量维持默认 3，供下游构造 prompt 上下文
    #          时通过 get_recent_context_window() 仅取最近 N 轮。

    return {
        # 扁平列表保持原样（SectionGenerator 直接消费，向后兼容）
        "feedback_history": full_history,
        # 三层结构回写 state
        "global_constraints": new_global_constraints,
        "compressed_history": new_compressed_history,
        "context_window_size": context_window_size,
        "current_round": current_round + 1,
        "node_status": {
            **state.get("node_status", {}),
            "FeedbackProcessor": NodeStatus.COMPLETED.value,
        },
    }
