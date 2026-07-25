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


# ── Format-rule field parsing helpers (no DOCX rendering needed) ────────

# Matches "上3.7/下3.5/左2.8/右2.6cm" or "上3.7cm 下3.5cm 左2.8cm 右2.6cm"
# (separators / units are bridged with .*? so the pattern is unit-agnostic)
_MARGIN_PAT = re.compile(
    r"上\s*([\d.]+).*?下\s*([\d.]+).*?左\s*([\d.]+).*?右\s*([\d.]+)",
    re.IGNORECASE,
)


def _parse_margin(raw: str) -> dict | None:
    """Parse a page_margin string into {top,bottom,left,right} in cm.

    Returns None if the string can't be parsed (caller flags a manual check).
    """
    if not raw:
        return None
    m = _MARGIN_PAT.search(raw.replace("：", " ").replace(":", " "))
    if not m:
        return None
    return {
        "top": m.group(1),
        "bottom": m.group(2),
        "left": m.group(3),
        "right": m.group(4),
    }


def _has_toc(sections: dict[str, str]) -> bool:
    """Best-effort detection of a table-of-contents section / heading."""
    for name, content in sections.items():
        if "目录" in name:
            return True
    for content in sections.values():
        # A line that is exactly "目录" (possibly with leading spaces)
        if re.search(r"(?:^|\n)\s*目录\s*(?:\n|$)", content):
            return True
    return False


def _check_format_compliance(sections: dict[str, str], format_rules: dict) -> list[dict]:
    """Check format-related compliance issues (M6 content-level fix).

    Previously this node only verified that format_rules *declared* certain
    fields (declaration-level).  M6 requires checking the real format elements
    per system-workflow.md §⑪:
      - 页边距（上下左右）
      - 字体（正文仿宋 / 标题黑体楷体）
      - 行距
      - 目录（TOC）
      - 页眉页脚
      - seal_requirement（签章要求）

    We cannot render a DOCX here, so for items that need a real renderer we
    emit explicit **人工确认** items (severity=low) instead of silently
    passing.  Where the generated section text lets us, we do a content-level
    sanity check.  format_rules fields are compared/validated when present.
    """
    issues: list[dict] = []

    # 1. Minimum content per section (keep)
    for name, content in sections.items():
        if len(content.strip()) < 100:
            issues.append({
                "item": "内容过短",
                "section": name,
                "detail": f"仅 {len(content)} 字，建议至少 200 字",
                "severity": "medium",
            })

    # No format_rules at all → everything is unrenderable, flag as manual check
    if not format_rules:
        issues.append({
            "item": "格式规则缺失",
            "section": "全局",
            "detail": (
                "未提供 format_rules，页边距/字体/行距/目录/页眉页脚/签章"
                "均需在 Word 中人工确认"
            ),
            "severity": "medium",
        })
        return issues

    # 2. 页边距 — content-level: parse declared margins, list for manual confirm
    margin_raw = format_rules.get("page_margin")
    if margin_raw:
        parsed = _parse_margin(margin_raw)
        if parsed:
            issues.append({
                "item": "页边距声明核对",
                "section": "全局",
                "detail": (
                    f"format_rules 声明页边距：上{parsed['top']}/下{parsed['bottom']}/"
                    f"左{parsed['left']}/右{parsed['right']}cm。"
                    "需在 Word 中人工确认实际页面设置与之一致"
                ),
                "severity": "low",
                "check_status": "manual_confirm",
            })
        else:
            issues.append({
                "item": "页边距格式异常",
                "section": "全局",
                "detail": (
                    f"page_margin 值「{margin_raw}」无法解析，"
                    "请在 Word 中人工确认页边距设置"
                ),
                "severity": "medium",
            })
    else:
        issues.append({
            "item": "页边距未声明",
            "section": "全局",
            "detail": "format_rules 未声明 page_margin，需在 Word 中人工确认页边距",
            "severity": "medium",
        })

    # 3. 字体 — content-level: 正文应仿宋，标题应黑体/楷体
    font_raw = format_rules.get("font")
    if font_raw:
        has_body_fang = "仿宋" in font_raw
        has_title_hei = ("黑体" in font_raw) or ("楷体" in font_raw)
        if not has_body_fang:
            issues.append({
                "item": "正文字体未声明仿宋",
                "section": "全局",
                "detail": (
                    f"font 声明「{font_raw}」未包含仿宋体，"
                    "正文通常须用仿宋_GB2312，请在 Word 中人工确认"
                ),
                "severity": "medium",
            })
        if not has_title_hei:
            issues.append({
                "item": "标题字体未声明黑体/楷体",
                "section": "全局",
                "detail": (
                    f"font 声明「{font_raw}」未声明标题用黑体或楷体，"
                    "需在 Word 中人工确认标题字体"
                ),
                "severity": "low",
                "check_status": "manual_confirm",
            })
        # Real font rendering can't be verified statically → explicit confirm
        issues.append({
            "item": "字体需人工确认",
            "section": "全局",
            "detail": f"声明字体「{font_raw}」，实际 Word 字体/字形需在渲染后人工确认",
            "severity": "low",
            "check_status": "manual_confirm",
        })
    else:
        issues.append({
            "item": "字体未声明",
            "section": "全局",
            "detail": "format_rules 未声明 font，需在 Word 中人工确认字体",
            "severity": "medium",
        })

    # 4. 行距 — content-level: declared value, real rendering manual confirm
    line_spacing = format_rules.get("line_spacing")
    if line_spacing:
        issues.append({
            "item": "行距需人工确认",
            "section": "全局",
            "detail": f"声明行距「{line_spacing}」，真实行距需在 Word 中人工确认",
            "severity": "low",
            "check_status": "manual_confirm",
        })
    else:
        issues.append({
            "item": "行距未声明",
            "section": "全局",
            "detail": "format_rules 未声明 line_spacing，需在 Word 中人工确认行距",
            "severity": "medium",
        })

    # 5. 目录（TOC）— content-level detection + manual confirm
    if _has_toc(sections):
        issues.append({
            "item": "目录已生成",
            "section": "全局",
            "detail": "检测到目录章节/标题，目录格式（页码对齐等）需在 Word 中人工确认",
            "severity": "low",
            "check_status": "manual_confirm",
        })
    else:
        issues.append({
            "item": "目录待确认",
            "section": "全局",
            "detail": "未检测到目录章节，标书通常需含目录，请在 Word 中人工确认是否已生成目录",
            "severity": "low",
            "check_status": "manual_confirm",
        })

    # 6. 页眉页脚 — format_rules may declare header/footer; else manual confirm
    header = format_rules.get("header")
    footer = format_rules.get("footer")
    if header or footer:
        declared = []
        if header:
            declared.append(f"页眉「{header}」")
        if footer:
            declared.append(f"页脚「{footer}」")
        issues.append({
            "item": "页眉页脚声明核对",
            "section": "全局",
            "detail": f"format_rules 声明{'、'.join(declared)}，需在 Word 中人工确认实际页眉页脚",
            "severity": "low",
            "check_status": "manual_confirm",
        })
    else:
        issues.append({
            "item": "页眉页脚待确认",
            "section": "全局",
            "detail": "format_rules 未声明 header/footer，页眉页脚需在 Word 中人工确认",
            "severity": "low",
            "check_status": "manual_confirm",
        })

    # 7. 签章要求（seal_requirement）— declaration + content-level reflection
    seal = format_rules.get("seal_requirement")
    if not seal:
        issues.append({
            "item": "签章要求未声明",
            "section": "全局",
            "detail": "format_rules 中未声明签章要求（seal_requirement），标书须加盖公章",
            "severity": "medium",
        })
    else:
        # Content-level: does the document text actually mention 公章/盖章?
        seal_mentioned = any(
            re.search(r"公章|盖章|签章|骑缝", content) for content in sections.values()
        )
        if seal_mentioned:
            issues.append({
                "item": "签章要求已体现",
                "section": "全局",
                "detail": f"声明签章要求「{seal}」，且章节文本已提及盖章/公章，实际盖章位置需在 Word 中人工确认",
                "severity": "low",
                "check_status": "manual_confirm",
            })
        else:
            issues.append({
                "item": "签章要求待确认",
                "section": "全局",
                "detail": (
                    f"声明签章要求「{seal}」，但章节文本未提及公章/盖章，"
                    "请确认是否需在投标函/签章页说明盖章要求，并在 Word 中人工确认"
                ),
                "severity": "low",
                "check_status": "manual_confirm",
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

        # 4. M5 fix: Intellectual property infringement check (著作权法)
        # Detect potential unauthorized use of third-party cases/images
        ip_patterns = [
            r"(?:本案例|本项目|本产品).{0,10}(?:著作权|版权所有|©|\(c\))",
            r"未经授权.{0,10}(?:使用|引用|转载)",
        ]
        for pat in ip_patterns:
            if re.search(pat, content):
                issues.append({
                    "item": "知识产权风险",
                    "section": section_name,
                    "severity": "medium",
                    "detail": "可能存在未经授权使用他人案例/图片的情况，请确认授权或脱敏处理",
                })

        # 5. M5 fix: Confidential info leakage check (保密法)
        # N3 fix: 收敛正则，避免误报 FAIL。
        #   仅当上下文表明"实际泄露了第三方保密信息"才判违规：
        #     (a) 敏感标识符（手机号/身份证号/银行账号）必须带有敏感上下文
        #         关键词（账号/卡号/手机/身份证…）锚定，避免把合同编号、
        #         项目编号等普通长数字串误报为银行账号/身份证号；
        #     (b) 文本型泄露：明确"泄露/披露/外泄 了 XX 商业秘密/保密数据"，
        #         且不在正面承诺语境（承诺/遵守/不泄露）中。
        #   "我方承诺保密""遵守保密协议""不会泄露"等正面声明判合规。
        _sensitive_prefix = (
            r"(?:手机|联系|银行|对公|账户|账号|卡号|身份证|身份證|"
            r"证号|证件号|统一社会信用代码|开户)"
        )
        phone_pattern = re.compile(_sensitive_prefix + r"[:：]?\s*1[3-9]\d{9}")
        id_card_pattern = re.compile(_sensitive_prefix + r"[:：]?\s*\d{17}[\dXx]")
        bank_account_pattern = re.compile(_sensitive_prefix + r"[:：]?\s*\d{16,19}")

        for pat_name, pat in [
            ("手机号", phone_pattern),
            ("身份证号", id_card_pattern),
            ("银行账号", bank_account_pattern),
        ]:
            matches = pat.findall(content)
            if matches:
                issues.append({
                    "item": "保密信息泄露",
                    "section": section_name,
                    "severity": "high",
                    "detail": f"检测到 {len(matches)} 处疑似{pat_name}，需脱敏处理",
                })

        # 文本型泄露：泄露/披露/外泄 + 第三方保密内容；
        # 前置正面承诺/否定词（承诺/遵守/不泄露/绝不…）则不算泄露。
        # 注：Python re 不支持变长后顾，故用可选捕获组判断前缀语境。
        _leak_verbs = r"(?:泄露|泄漏|披露|外泄|透漏)"
        _positive_ctx = (
            r"(?:承诺|保证|确保|遵守|严守|严格|不会|决不|绝不|未|没有|不予以?|不予)"
        )
        leak_pattern = re.compile(
            r"(" + _positive_ctx + r")?"
            + _leak_verbs
            + r"[^。；;]{0,15}?(?:商业秘密|保密(?:数据|信息|资料|内容|文件)|"
            r"机密|敏感信息|客户资料|内部资料)"
        )
        leak_match = leak_pattern.search(content)
        if leak_match and not leak_match.group(1):
            issues.append({
                "item": "保密信息泄露",
                "section": section_name,
                "severity": "high",
                "detail": "文本中出现第三方保密信息泄露表述，请确认已脱敏或获得授权",
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
