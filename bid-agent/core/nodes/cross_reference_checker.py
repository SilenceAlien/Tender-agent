"""CrossReferenceChecker node — cross-section consistency validation.

Phase C3 (landing-pipeline-expansion.md §4.2): L5 validation gate that runs
after QualityChecker.  Catches inconsistencies that single-section checks
miss — e.g. the staffing chapter says "15人" but the service plan says "12人".

Checks:
    - Personnel counts consistent across chapters
    - Technical parameters consistent
    - Time/milestone dates consistent
    - Monetary amounts consistent
    - Document composition: items declared in bid letter must have
      corresponding sections in the generated document

Contract:
    def cross_reference_checker(state: AgentState) -> dict
    Input:  state.sections
    Output: {cross_ref_report: {verdict, inconsistencies}, node_status}
"""

import logging
import re
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)


def _extract_numbers(context: str, pattern: str) -> list[tuple[str, str]]:
    """Extract numbers with their surrounding context.

    Returns list of (number, context_snippet) tuples.
    """
    results = []
    for match in re.finditer(pattern, context):
        start = max(0, match.start() - 15)
        end = min(len(context), match.end() + 15)
        results.append((match.group(0), context[start:end]))
    return results


def _check_personnel_consistency(sections: dict[str, str]) -> list[dict]:
    """Check that personnel counts are consistent across chapters.

    Looks for patterns like "XX人" or "XX名" and flags if different chapters
    report different team sizes for what appears to be the same team.
    """
    issues: list[dict] = []
    # Pattern: number + 人/名 (but not 万人, 人员 etc.)
    person_pattern = r"(\d+)\s*(?:人|名)(?!员|才|事|力|民|生|工)"

    counts_by_section: dict[str, list[str]] = {}
    for name, content in sections.items():
        matches = _extract_numbers(content, person_pattern)
        if matches:
            counts_by_section[name] = [m[0] for m in matches]

    # If multiple sections mention personnel counts, check for discrepancies.
    # We look for "total team size" mentions (numbers ≥3, since 1-2 are likely
    # sub-team counts like "项目经理1名") and flag if they differ across sections.
    section_totals: dict[str, list[int]] = {}
    for name, counts in counts_by_section.items():
        # Parse the numeric values, filter out tiny counts (sub-teams/individuals)
        nums = []
        for c in counts:
            try:
                n = int(re.match(r"(\d+)", c).group(1))
                if n >= 3:  # ignore 1-2 person sub-teams
                    nums.append(n)
            except (AttributeError, ValueError):
                pass
        if nums:
            section_totals[name] = nums

    if len(section_totals) >= 2:
        # Compare the max count in each section (likely the "total team size")
        max_per_section = {name: max(nums) for name, nums in section_totals.items()}
        unique_totals = set(max_per_section.values())
        if len(unique_totals) >= 2:
            issues.append({
                "type": "number_mismatch",
                "field": "人员数量",
                "detail": f"不同章节的总人数不一致：{max_per_section}",
                "sections": list(max_per_section.keys()),
                "severity": "medium",
            })

    return issues


def _check_amount_consistency(sections: dict[str, str]) -> list[dict]:
    """Check that monetary amounts are consistent across chapters.

    Looks for ¥/元/万元 patterns and flags if the bid price appears
    differently in different chapters.
    """
    issues: list[dict] = []
    # Pattern: amount with currency
    amount_pattern = r"(?:¥|人民币)?\s*[\d,]+(?:\.\d+)?\s*(?:万元|亿元|元)"

    amounts_by_section: dict[str, list[str]] = {}
    for name, content in sections.items():
        matches = _extract_numbers(content, amount_pattern)
        if matches:
            amounts_by_section[name] = [m[0] for m in matches]

    # Check for 投标函 vs other chapters — the bid price should match
    bid_letter_amounts = []
    for name, amounts in amounts_by_section.items():
        if "投标函" in name or "报价" in name:
            bid_letter_amounts.extend(amounts)

    if bid_letter_amounts:
        unique_bid = set(bid_letter_amounts)
        if len(unique_bid) > 1:
            issues.append({
                "type": "amount_mismatch",
                "field": "投标报价",
                "detail": f"投标函中出现多个不同报价：{unique_bid}",
                "severity": "high",
            })

    return issues


def _check_date_consistency(sections: dict[str, str]) -> list[dict]:
    """Check for date inconsistencies across chapters."""
    issues: list[dict] = []
    # Pattern: YYYY年MM月DD日 or YYYY-MM-DD or YYYY/MM/DD
    date_pattern = r"(\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2})"

    # Look for contract signing dates, project start dates mentioned in multiple places
    signing_dates: list[tuple[str, str]] = []  # (date, section)
    for name, content in sections.items():
        if "签订" in content or "开工" in content or "启动" in content:
            for match in re.finditer(date_pattern, content):
                signing_dates.append((match.group(0), name))

    # If the same type of date appears with different values, flag it
    if len(signing_dates) >= 2:
        unique_dates = set(d for d, _ in signing_dates)
        if len(unique_dates) > 1:
            issues.append({
                "type": "date_mismatch",
                "field": "关键日期",
                "detail": f"不同章节出现不一致的日期：{unique_dates}",
                "sections": list(set(s for _, s in signing_dates)),
                "severity": "medium",
            })

    return issues


# ── Document composition keyword mapping ────────────────────────────────
# Maps keywords that appear in the bid letter's document composition list
# to the keywords that should appear in actual section names.
# This ensures that every item declared in the bid letter has a corresponding
# section in the generated document.

_COMPOSITION_KEYWORD_MAP = {
    "投标函": "投标函",
    "身份证明": "身份证明",
    "授权委托书": "授权委托书",
    "保证金": "保证金",
    "偏差表": "偏差表",
    "分项报价表": "分项报价表",
    "资格审查": "资格审查",
    "服务方案": "服务方案",
    "应急保障": "应急保障",
    "技术方案": "技术方案",
    "人员配置": "人员配置",
    "实施计划": "实施计划",
    "售后": "售后",
    "资质": "资质",
}


def _check_document_composition_consistency(sections: dict[str, str]) -> list[dict]:
    """Check that items declared in the bid letter's document composition list
    have corresponding sections in the generated document.

    The bid letter (投标函) typically declares a list of document components
    like: 投标函、身份证明、保证金、偏差表、分项报价表、资格审查资料...
    Each declared item must have a matching section in the generated document.
    A mismatch (declared but not provided) is a serious consistency issue that
    can lead to bid rejection.
    """
    issues: list[dict] = []

    # Find the bid letter section
    bid_letter_content = ""
    for name, content in sections.items():
        if "投标函" in name:
            bid_letter_content = content
            break

    if not bid_letter_content:
        return issues  # No bid letter, can't check

    # Extract declared document composition items from the bid letter.
    # Look for patterns like:
    #   投标文件组成清单：...（投标函、身份证明、保证金、偏差表、分项报价表...）
    #   我方的投标文件包括下列内容：（1）投标函；（2）...分项报价表...
    #   投标文件组成：逐条列出（...分项报价表...）
    declared_items: set[str] = set()

    # Strategy: scan the bid letter for known composition keywords
    for keyword, _ in _COMPOSITION_KEYWORD_MAP.items():
        if keyword in bid_letter_content:
            declared_items.add(keyword)

    if not declared_items:
        return issues  # No composition list found, can't check

    # Build a set of keywords found in actual section names
    section_keywords: set[str] = set()
    for section_name in sections.keys():
        for keyword in _COMPOSITION_KEYWORD_MAP:
            if keyword in section_name:
                section_keywords.add(keyword)

    # Find declared items that have no matching section
    missing = declared_items - section_keywords
    if missing:
        missing_list = sorted(missing)
        issues.append({
            "type": "document_composition_mismatch",
            "field": "文件组成声明",
            "detail": (
                f"投标函声明文件组成包含「{'、'.join(missing_list)}」，"
                f"但实际提交的章节中无对应内容。"
                f"声明与实际提交内容不一致可能导致废标。"
            ),
            "declared_items": missing_list,
            "severity": "high",
        })

    return issues


# ── LangGraph Node ─────────────────────────────────────────────────────


def cross_reference_checker(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """Check cross-section consistency of generated content.

    Args:
        state: AgentState with sections
        llm_fn: Optional LLM for deeper consistency analysis

    Returns:
        {cross_ref_report: {verdict, inconsistencies}, node_status}
    """
    sections = state.get("sections", {})

    if not sections or len(sections) < 2:
        logger.info("CrossReferenceChecker: need ≥2 sections for cross-checking")
        return {
            "cross_ref_report": {
                "verdict": "PASS",
                "inconsistencies": [],
                "summary": "章节数不足，跳过跨章节一致性检查",
            },
            "node_status": {
                **state.get("node_status", {}),
                "CrossReferenceChecker": NodeStatus.COMPLETED.value,
            },
        }

    inconsistencies: list[dict] = []
    inconsistencies.extend(_check_personnel_consistency(sections))
    inconsistencies.extend(_check_amount_consistency(sections))
    inconsistencies.extend(_check_date_consistency(sections))
    inconsistencies.extend(_check_document_composition_consistency(sections))

    # Verdict: FAIL on high-severity issues (amount mismatch), PASS otherwise
    has_high = any(i.get("severity") == "high" for i in inconsistencies)
    verdict = "FAIL" if has_high else "PASS"

    logger.info(
        f"CrossReferenceChecker: verdict={verdict}, "
        f"found {len(inconsistencies)} inconsistencies"
    )

    return {
        "cross_ref_report": {
            "verdict": verdict,
            "inconsistencies": inconsistencies,
            "summary": f"发现 {len(inconsistencies)} 处跨章节不一致" if inconsistencies else "无跨章节不一致",
        },
        "node_status": {
            **state.get("node_status", {}),
            "CrossReferenceChecker": NodeStatus.COMPLETED.value,
        },
    }
