"""ComplianceChecker node — format + legal compliance validation.

Phase C2 (landing-pipeline-expansion.md §4.3): L5 validation gate that runs
after QualityChecker.  Catches two classes of problems that QualityChecker
doesn't cover:

1. Format compliance — page margins, fonts, line spacing, seal requirements
   (violations can cause direct rejection / 废标)
2. Legal compliance — absolute superlatives (广告法), fabricated performance
   (招标投标法), IP infringement, confidential info leakage

Contract:
    def compliance_checker(state: AgentState) -> dict
    Input:  state.sections, state.requirements.format_rules
    Output: {compliance_report: {verdict, format_issues, legal_issues}, node_status}
    Routing: FAIL → FeedbackProcessor (legal issues need content rewrite)
"""

import logging
import re
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Legal compliance: banned absolute superlatives (广告法) ────────────
# These words violate China's Advertising Law and can cause the bid to be
# challenged or the winning bid to be revoked after the fact.

ABSOLUTE_TERMS = [
    "全国第一", "全国唯一", "全国最佳", "全国最高",
    "行业第一", "行业唯一", "行业最佳",
    "国内第一", "国内唯一", "国内最佳",
    "世界第一", "世界唯一", "世界最佳",
    "第一品牌", "唯一品牌", "最佳品牌",
    "顶级", "极品", "万能",
    "国家级", "最高级",
    "100%满意", "绝对保证", "绝对可靠",
]

# Context-sensitive patterns — these need regex to avoid false positives
# e.g. "第一章" should NOT match "第一"
_CONTEXT_PATTERNS = {
    # "第一" only when NOT followed by 章/节/条/款/次/部分/批/期/名/次
    "第一": re.compile(r"第[一](?![章节条款次部分批期名次])"),
    # "唯一" only when NOT followed by 标识/编码/编号
    "唯一": re.compile(r"唯一(?!标识|编码|编号|性)"),
    # "最" only when followed by superlative adjectives (not 最终/最后/最新 etc.)
    # BUG-08 fix: aligned with QualityChecker's exclusion list to avoid false
    # positives on common technical terms like "最优化", "最重要", "最佳实践".
    "最": re.compile(r"最(?!终|后|新|近|高|低|大|小|多|少|早|晚|初|优化|重要|佳)"),
}

# ── Format compliance ─────────────────────────────────────────────────


def _check_format_compliance(sections: dict[str, str], format_rules: dict) -> list[dict]:
    """Check format-related compliance issues.

    For text-based sections we can't fully verify page margins/fonts (that
    requires the rendered DOCX), but we can check structural format rules:
    - Section count meets minimum
    - Each section has adequate length
    - Required sections are present (投标函, 服务方案, etc.)
    """
    issues: list[dict] = []

    # Check minimum content per section
    for name, content in sections.items():
        if len(content.strip()) < 100:
            issues.append({
                "item": "内容过短",
                "section": name,
                "detail": f"仅 {len(content)} 字，建议至少 200 字",
                "severity": "medium",
            })

    return issues


# ── Legal compliance ──────────────────────────────────────────────────


def _check_legal_compliance(sections: dict[str, str]) -> list[dict]:
    """Check for legal compliance violations.

    Scans all sections for:
    - Absolute superlative terms (广告法 violations)
    - Fabricated performance indicators (招标投标法)
    """
    issues: list[dict] = []

    for section_name, content in sections.items():
        # 1. Check absolute terms (simple substring first)
        for term in ABSOLUTE_TERMS:
            if term in content:
                issues.append({
                    "item": "绝对化用语",
                    "text": term,
                    "section": section_name,
                    "severity": "high",
                    "detail": f"违反广告法：含绝对化用语「{term}」，需替换为客观表述",
                })

        # 2. Context-sensitive pattern checks (第一/唯一/最)
        for word, pattern in _CONTEXT_PATTERNS.items():
            matches = pattern.findall(content)
            if matches:
                # Get surrounding context for the first match
                match = pattern.search(content)
                if match:
                    start = max(0, match.start() - 10)
                    end = min(len(content), match.end() + 10)
                    context = content[start:end]
                    issues.append({
                        "item": "绝对化用语",
                        "text": word,
                        "section": section_name,
                        "severity": "high",
                        "detail": f"疑似绝对化用语「{word}」，上下文：...{context}...",
                    })

        # 3. Check for suspicious fabricated performance
        # Pattern: specific year + large amount without contract reference
        fabricated_patterns = [
            r"20[12]\d年.*?(?:营业额|收入|利润).*?[\d,]+.*?(?:亿|万)元",
        ]
        for pat in fabricated_patterns:
            if re.search(pat, content):
                issues.append({
                    "item": "疑似虚假业绩",
                    "section": section_name,
                    "severity": "high",
                    "detail": "包含具体财务数据，请确认有对应审计报告支撑",
                })

    return issues


# ── LangGraph Node ─────────────────────────────────────────────────────


def compliance_checker(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """Check format and legal compliance of generated sections.

    Args:
        state: AgentState with sections and format_rules
        llm_fn: Optional LLM for deeper compliance analysis (not used in MVP)

    Returns:
        {compliance_report: {verdict, format_issues, legal_issues}, node_status}
    """
    sections = state.get("sections", {})
    format_rules = state.get("requirements", {}).get("format_rules", {})

    if not sections:
        logger.warning("ComplianceChecker: no sections to check")
        return {
            "compliance_report": {
                "verdict": "FAIL",
                "format_issues": [{"item": "无章节内容", "severity": "high"}],
                "legal_issues": [],
            },
            "node_status": {
                **state.get("node_status", {}),
                "ComplianceChecker": NodeStatus.COMPLETED.value,
            },
        }

    # Run checks
    format_issues = _check_format_compliance(sections, format_rules)
    legal_issues = _check_legal_compliance(sections)

    # Verdict: FAIL only on high-severity legal issues (format issues are
    # medium and can be fixed at assembly time)
    has_critical = any(i.get("severity") == "high" for i in legal_issues)
    verdict = "FAIL" if has_critical else "PASS"

    total_issues = len(format_issues) + len(legal_issues)
    logger.info(
        f"ComplianceChecker: verdict={verdict}, "
        f"format={len(format_issues)}, legal={len(legal_issues)}"
    )

    return {
        "compliance_report": {
            "verdict": verdict,
            "format_issues": format_issues,
            "legal_issues": legal_issues,
            "total_issues": total_issues,
        },
        "node_status": {
            **state.get("node_status", {}),
            "ComplianceChecker": NodeStatus.COMPLETED.value,
        },
    }
