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
    - N05: Contract deviation — forbidden institutions/industries from
      project_contract must not appear in generated sections
    - N06: Technical parameter consistency — response time, service hours
      etc. must be consistent across chapters

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
                "severity": "high",
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
                "severity": "high",
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


# ── N05: Contract deviation check ─────────────────────────────────────


def _check_contract_deviation(
    sections: dict[str, str],
    project_contract: dict,
) -> list[dict]:
    """N05: Check that forbidden institutions/industries from the project
    contract do not appear in generated sections.

    PRD 4.2.2 验收标准 2: 拼凑残留（其他项目的机构名错误出现）需被检测。
    """
    issues: list[dict] = []

    forbidden_institutions = project_contract.get("forbidden_institutions", [])
    forbidden_industries = project_contract.get("forbidden_industries", [])

    for section_name, content in sections.items():
        for inst in forbidden_institutions:
            if inst and inst in content:
                issues.append({
                    "type": "contract_forbidden_institution",
                    "field": "禁止机构名",
                    "detail": (
                        f"章节「{section_name}」中出现了契约禁止的机构名「{inst}」，"
                        f"可能是拼凑残留，请检查并删除。"
                    ),
                    "section": section_name,
                    "severity": "critical",
                })

        for ind in forbidden_industries:
            if ind and ind in content:
                issues.append({
                    "type": "contract_forbidden_industry",
                    "field": "禁止行业",
                    "detail": (
                        f"章节「{section_name}」中出现了契约禁止的行业提及「{ind}」，"
                        f"可能与本项目无关，请检查。"
                    ),
                    "section": section_name,
                    "severity": "critical",
                })

    return issues


# ── N06: Technical parameter consistency check ────────────────────────


# Common technical parameter patterns: (label, regex)
_TECH_PARAM_PATTERNS = [
    ("响应时间", re.compile(r"响应时间[^\d]{0,5}(\d+)\s*(?:秒|分钟|min|s)")),
    ("服务时间", re.compile(r"服务时间[^\d]{0,5}(\d+)\s*(?:小时|h)")),
    ("到达现场", re.compile(r"到达现场[^\d]{0,5}(\d+)\s*(?:小时|分钟|min|h)")),
    ("故障恢复", re.compile(r"故障恢复[^\d]{0,5}(\d+)\s*(?:小时|分钟|min|h)")),
    ("保修期", re.compile(r"保修期[^\d]{0,5}(\d+)\s*(?:年|个月|月)")),
    ("服务期", re.compile(r"服务期[^\d]{0,5}(\d+)\s*(?:年|个月|月)")),
]


def _check_tech_parameter_consistency(sections: dict[str, str]) -> list[dict]:
    """N06: Check that technical parameters are consistent across chapters.

    PRD 4.2.2: 检查技术参数跨章节一致（技术方案章 vs 服务方案章）。
    Scans for common technical parameters (response time, service hours, etc.)
    and flags if different chapters report different values.
    """
    issues: list[dict] = []

    # Collect parameter values per section
    param_values: dict[str, dict[str, list[str]]] = {}  # {param_name: {section: [values]}}

    for section_name, content in sections.items():
        for param_name, pattern in _TECH_PARAM_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                param_values.setdefault(param_name, {})[section_name] = matches

    # Check for inconsistencies
    for param_name, section_map in param_values.items():
        if len(section_map) < 2:
            continue  # Need at least 2 sections to compare
        # Get the first value from each section
        section_values = {s: vals[0] for s, vals in section_map.items()}
        unique_values = set(section_values.values())
        if len(unique_values) > 1:
            issues.append({
                "type": "tech_parameter_mismatch",
                "field": param_name,
                "detail": (
                    f"技术参数「{param_name}」在不同章节中取值不一致："
                    f"{section_values}"
                ),
                "sections": list(section_values.keys()),
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
    project_contract = state.get("project_contract", {})

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
    # N05: contract deviation check
    inconsistencies.extend(_check_contract_deviation(sections, project_contract))
    # N06: technical parameter consistency check
    inconsistencies.extend(_check_tech_parameter_consistency(sections))

    # Verdict: FAIL on high/critical-severity issues
    # Case-insensitive check: spec uses "CRITICAL" (uppercase), code uses "critical" (lowercase)
    has_high = any(
        i.get("severity", "").lower() in ("high", "critical")
        for i in inconsistencies
    )
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
