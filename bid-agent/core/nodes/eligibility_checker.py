"""EligibilityChecker node — verifies enterprise qualifications meet tender thresholds.

Phase C1 (landing-pipeline-expansion.md §4.1): L1 decision gate inserted
after ReqExtractor.  If the enterprise lacks a hard-threshold qualification,
the pipeline terminates immediately — there is no point generating 8 chapters
of content for a bid that will be rejected at the door.

Contract:
    def eligibility_checker(state: AgentState) -> dict
    Input:  state.requirements.qualifications, state.company_quals (or JSON file)
    Output: {eligibility_report: {verdict, missing_quals, matched_quals, risk_level},
             node_status}
    Routing:
        verdict == FAIL    → continue (F3 fix: was END, now continues so user gets draft)
        verdict == WARNING → continue (some quals missing but alternatives exist)
        verdict == PASS    → continue
"""

import json
import logging
import re
from pathlib import Path

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Non-certificate requirement patterns ──────────────────────────────
# The company_quals.json database stores *certificates and licenses* (e.g.
# "ISO9001认证", "人力资源服务许可证").  However, ReqExtractor often extracts
# many non-certificate requirements from tender documents that are NOT
# verifiable against a certificate database:
#
#   1. Generic legal boilerplate (政府采购法 第22条)
#   2. Case/experience requirements ("提供...合作案例", "业绩要求")
#   3. Documentation/process requirements ("提供...标准", "提供...流程", "提供...制度")
#   4. Status/declaration requirements ("已通过资格预审", "承诺...")
#   5. General capability statements ("具有...能力", "具备...条件")
#   6. Financial document requirements ("提供...财务报表", "纳税证明")
#   7. Legal representative documents ("法定代表人授权书", "身份证复印件")
#   8. Catch-all submission requirements ("提供其他资质、资格证书等")
#
# These are "soft requirements" that the bid document addresses through
# narrative content, not through certificates.  Including them in the
# certificate matching causes false FAILs because they can never match
# against entries like "ISO9001认证".
#
# We filter them out before matching, and auto-pass them.

# Category 1: Generic legal boilerplate (政府采购法 第22条)
_GENERIC_LEGAL_PATTERNS = [
    "中华人民共和国境内",
    "独立承担民事责任",
    "独立承担民事",
    "良好的商业信誉",
    "健全的财务会计制度",
    "履行合同所必需的设备和专业技术能力",
    "履行合同所必需",
    "依法缴纳税收",
    "依法缴纳社会保障资金",
    "无重大违法记录",
    "法律、行政法规规定的其他条件",
    "参加政府采购活动前三年内",
    "在经营活动中",
    "具有独立法人资格",
    "独立法人资格",
    "营业执照",
    "合法经营",
    "符合国家有关规定",
]

# Category 2: Case/experience requirements
# e.g. "提供2020年1月1日后有同类企业质检验货外包的合作案例"
_CASE_PATTERNS = [
    "合作案例",
    "业绩要求",
    "业绩证明",
    "同类项目",
    "类似项目",
    "项目业绩",
    "服务案例",
    "成功案例",
    "提供.*案例",
    "提供.*业绩",
]

# Category 3: Documentation/process requirements
# e.g. "提供质检验货服务标准、流程及制度"
_DOC_PATTERNS = [
    "提供.*标准",
    "提供.*流程",
    "提供.*制度",
    "提供.*手册",
    "提供.*方案",
    "提供.*规程",
    "提供.*规范",
    "提供.*管理体系",
    "服务标准",
    "服务流程",
    "管理制度",
    # 独立方案类关键词（不带"提供"前缀的情况）
    # ReqExtractor 有时会将"项目团队及运营方案"等内容要求提取为资质
    ".*运营方案",
    ".*培训支持方案",
    ".*实施方案",
    ".*管理方案",
    ".*服务方案",
    ".*技术方案",
    ".*保障方案",
    ".*应急预案",
    ".*团队.*方案",
    ".*人力资源.*方案",
]

# Category 4: Status/declaration requirements
# e.g. "已通过资格预审"
_STATUS_PATTERNS = [
    "已通过资格预审",
    "通过资格预审",
    "资格预审",
    "承诺",
    "声明",
    "保证",
    "自愿",
]

# Category 5: General capability statements (not certifiable)
# e.g. "具有履行合同所必需的设备和专业技术能力"
_CAPABILITY_PATTERNS = [
    "具有.*能力",
    "具备.*能力",
    "具有.*条件",
    "具备.*条件",
    "具有.*设备",
    "具备.*设备",
    "具有.*人员",
    "具备.*人员",
    "具有.*技术",
    "具备.*技术",
]

# Category 6: Financial document requirements
# e.g. "提供2023年-2026年7月31日以前的年度/半年度财务报表（资产负债表、现金流量表、利润表、纳税证明）"
_FINANCIAL_PATTERNS = [
    "提供.*财务报表",
    "提供.*财务报告",
    "提供.*资产负债表",
    "提供.*现金流量表",
    "提供.*利润表",
    "提供.*纳税证明",
    "提供.*审计报告",
    "财务报表",
    "财务报告",
    "纳税证明",
    "资产负债表",
    "现金流量表",
    "利润表",
    "审计报告",
]

# Category 7: Legal representative / identity documents
# e.g. "提供法定代表人授权书、法人授权代表身份证复印件"
_LEGAL_REP_PATTERNS = [
    "提供.*法定代表人",
    "提供.*法人授权",
    "提供.*身份证",
    "法定代表人授权书",
    "法人授权代表",
    "身份证复印件",
    "法人代表",
    "授权委托书",
    "提供.*授权书",
]

# Category 8: Catch-all submission requirements
# e.g. "提供其他资质、资格证书等（授权代理证书文件）"
# These ask the bidder to "provide other certificates if any" — they're not
# specific certificate names that can be matched.
_CATCHALL_PATTERNS = [
    "提供其他.*资质",
    "提供其他.*证书",
    "提供其他.*资格",
    "其他资质.*证书",
    "其他.*资格证书",
    "如有.*证书.*提供",
    "如有.*资质.*提供",
]

# Category 9: Invoice/tax capability requirements
# e.g. "能开具增值税专用发票，发票项目为"*生产生活服务*鉴证咨询服务*质检服务费""
# These are capability declarations about invoicing ability, not specific
# certificates. Any VAT general taxpayer can issue VAT invoices.
_INVOICE_PATTERNS = [
    "能开具.*发票",
    "能开具.*增值税",
    "能提供.*发票",
    "增值税专用发票",
    "开具.*发票",
    "发票项目.*",
    "能开具",
]

_ALL_SOFT_PATTERNS = (
    [(p, None) for p in _GENERIC_LEGAL_PATTERNS]
    + [(p, re.compile(p)) for p in _CASE_PATTERNS]
    + [(p, re.compile(p)) for p in _DOC_PATTERNS]
    + [(p, None) for p in _STATUS_PATTERNS]
    + [(p, re.compile(p)) for p in _CAPABILITY_PATTERNS]
    + [(p, re.compile(p)) for p in _FINANCIAL_PATTERNS]
    + [(p, re.compile(p)) for p in _LEGAL_REP_PATTERNS]
    + [(p, re.compile(p)) for p in _CATCHALL_PATTERNS]
    + [(p, re.compile(p)) for p in _INVOICE_PATTERNS]
)


def _is_generic_qual(qual: str) -> bool:
    """Check if a requirement is a non-certificate (soft) requirement.

    Soft requirements include generic legal boilerplate, case/experience
    requirements, documentation requirements, status declarations, and
    general capability statements.  These are addressed by the bid
    document's narrative content, not by certificates in company_quals.json.

    Only specific, certifiable qualifications (e.g. "ISO9001认证",
    "人力资源服务许可证") should remain for certificate matching.
    """
    for pattern, compiled in _ALL_SOFT_PATTERNS:
        if compiled is not None:
            # Regex pattern
            if compiled.search(qual):
                return True
        else:
            # Literal substring pattern
            if pattern in qual:
                return True
    return False


# ── Company qualifications database ────────────────────────────────────
# In production this would be a real database.  For MVP we load from a JSON
# file that the enterprise maintains with their actual certificates.

_DEFAULT_QUALS_PATH = Path(__file__).parent.parent.parent / "data" / "company_quals.json"


def _load_company_quals(quals_path: str | None = None) -> list[str]:
    """Load the enterprise's qualification list from JSON.

    The file is a simple array of qualification strings, e.g.:
        ["ISO9001质量管理体系认证", "ISO27001信息安全管理体系认证", ...]

    Returns an empty list if the file doesn't exist (the checker will then
    issue a WARNING rather than blocking the pipeline).
    """
    path = Path(quals_path) if quals_path else _DEFAULT_QUALS_PATH
    if not path.exists():
        logger.info(f"Company quals file not found at {path} — treating as empty")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(q) for q in data if q]
        if isinstance(data, dict) and "qualifications" in data:
            return [str(q) for q in data["qualifications"] if q]
    except Exception as e:
        logger.warning(f"Failed to load company quals from {path}: {e}")
    return []


# ── Matching ───────────────────────────────────────────────────────────


def _normalize_qual(text: str) -> str:
    """Normalize a qualification string for fuzzy matching.

    Strips whitespace and common suffixes so 'ISO9001认证' matches
    'ISO9001质量管理体系认证'.
    """
    text = text.strip()
    # Remove common trailing words that don't affect the match
    for suffix in ("认证", "证书", "资质", "许可"):
        if text.endswith(suffix) and len(text) > len(suffix) + 2:
            text = text[: -len(suffix)]
    return text.strip()


def _qual_matches(required: str, available: str) -> bool:
    """Check if an available qualification satisfies a requirement.

    Uses normalized substring matching: if the normalized required string
    is a substring of the normalized available string (or vice versa), it's
    a match.  This handles 'ISO9001' matching 'ISO9001质量管理体系认证'.
    """
    req_norm = _normalize_qual(required)
    avail_norm = _normalize_qual(available)
    if not req_norm or not avail_norm:
        return False
    return req_norm in avail_norm or avail_norm in req_norm


# ── Alternative (substitutable) qualification detection ───────────────
# When a required *hard* qualification is missing, the spec (节点⑤) requires
# us to downgrade to WARNING (continue) instead of FAIL (terminate) if the
# enterprise qualification library contains a *semantically substitutable*
# certificate — e.g. missing "ISO9001" but having "GB/T19001" (the Chinese
# national equivalent of the same quality-management-system certification),
# or missing "人力资源服务许可证" but having "人力资源服务资质".
#
# Conservative rule: a substitute is accepted only when some available
# certificate shares the *same capability core* (the Chinese descriptor after
# the leading standard code, e.g. "质量管理体系") as the required one.  This
# captures genuine equivalences while avoiding unrelated certificates.  The
# capability-core comparison uses a *deep* normalizer (repeatedly strips
# certificate-type suffixes incl. "许可证") so that e.g. "许可证" vs "资质"
# variants collapse to the same core — without touching the proven exact
# matching logic in _qual_matches.

# Minimum length (chars) for a capability core to be a reliable substitute
# signal.  Too-short cores (e.g. "质量") are ambiguous and could misclassify
# unrelated certificates.
_MIN_CORE_LEN = 4

# Leading standard-code pattern, e.g. "ISO9001", "GB/T19001", "ISO27001".
_CODE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9/\.\-\+]*)\s*(.*)$")

# Certificate-type suffixes stripped for capability-core extraction.
_CORE_SUFFIXES = ("认证", "证书", "资质", "许可", "许可证")


def _normalize_qual_deep(text: str) -> str:
    """Deep-normalize a qualification for capability-core extraction.

    Like _normalize_qual but strips certificate-type suffixes *repeatedly*
    (handles nested "认证证书") and also strips "许可证".  Used only for
    alternative detection so the exact-matching path stays unchanged.
    """
    text = text.strip()
    changed = True
    while changed:
        changed = False
        for suffix in _CORE_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix) + 1:
                text = text[: -len(suffix)]
                changed = True
                break
    return text.strip()


def _split_code_and_core(text: str) -> tuple[str, str]:
    """Split a normalized qualification into (leading standard-code, core).

    Examples:
        'ISO9001质量管理体系'   -> ('ISO9001', '质量管理体系')
        'GB/T19001质量管理体系' -> ('GB/T19001', '质量管理体系')
        '人力资源服务'          -> ('', '人力资源服务')
    """
    m = _CODE_RE.match(text)
    if m and m.group(2):
        return m.group(1), m.group(2)
    return "", text


def _find_qual_alternative(required: str, company_quals: list[str]) -> str | None:
    """Return a substitutable qualification from the company library, or None.

    Conservative: only certificates whose *capability core* (the Chinese
    descriptor, e.g. "质量管理体系") contains or is contained by the required
    one (length >= _MIN_CORE_LEN) count as substitutes.  This requires the
    same capability domain, so unrelated certificates are not misclassified.
    """
    req_norm = _normalize_qual_deep(required)
    code_req, core_req = _split_code_and_core(req_norm)
    if len(core_req) < _MIN_CORE_LEN:
        return None
    for avail in company_quals:
        avail_norm = _normalize_qual_deep(avail)
        code_avail, core_avail = _split_code_and_core(avail_norm)
        if len(core_avail) < _MIN_CORE_LEN:
            continue
        # Same capability domain → substitutable (handles both ISO9001 vs
        # GB/T19001 with different codes and pure-Chinese 许可证/资质 variants).
        if core_req in core_avail or core_avail in core_req:
            return avail
    return None


# ── LangGraph Node ─────────────────────────────────────────────────────


def eligibility_checker(
    state: AgentState,
    company_quals: list[str] | None = None,
    quals_path: str | None = None,
) -> dict:
    """Check enterprise qualifications against tender requirements.

    Args:
        state: AgentState with requirements.qualifications
        company_quals: Optional explicit list of enterprise qualifications.
                       If None, loads from the JSON database file.
        quals_path: Optional path to the company quals JSON file.

    Returns:
        {eligibility_report, node_status}
    """
    requirements = state.get("requirements", {})
    required_quals = requirements.get("qualifications", []) or []

    # ── Filter out generic legal requirements ───────────────────────────
    # Generic clauses (e.g. "在中华人民共和国境内拥有合法经营权力") are
    # boilerplate that any registered company satisfies.  They should not
    # be matched against specific certificates — doing so causes false FAILs.
    specific_quals: list[str] = []
    generic_quals: list[str] = []
    for q in required_quals:
        if _is_generic_qual(q):
            generic_quals.append(q)
        else:
            specific_quals.append(q)

    if generic_quals:
        logger.info(
            f"EligibilityChecker: filtered {len(generic_quals)} generic legal "
            f"requirements (auto-passed): {[q[:30] for q in generic_quals[:3]]}"
        )

    if not specific_quals:
        # All requirements were generic legal clauses → auto-pass
        logger.info(
            f"EligibilityChecker: all {len(required_quals)} requirements are "
            f"generic legal clauses — PASS"
        )
        return {
            "eligibility_report": {
                "verdict": "PASS",
                "missing_quals": [],
                "matched_quals": list(required_quals),
                "risk_level": "low",
                "summary": f"{len(required_quals)} 项均为通用法定要求，自动通过",
            },
            "node_status": {
                **state.get("node_status", {}),
                "EligibilityChecker": NodeStatus.COMPLETED.value,
            },
        }

    # Load company qualifications
    if company_quals is None:
        company_quals = _load_company_quals(quals_path)

    if not company_quals:
        # No company data → WARNING (can't verify, but don't block)
        logger.warning(
            "EligibilityChecker: no company quals data — cannot verify "
            f"{len(specific_quals)} specific requirements (WARNING)"
        )
        return {
            "eligibility_report": {
                "verdict": "WARNING",
                "missing_quals": list(specific_quals),
                "matched_quals": list(generic_quals),
                "risk_level": "medium",
                "summary": "企业资质库为空，无法验证资质是否满足要求",
            },
            "node_status": {
                **state.get("node_status", {}),
                "EligibilityChecker": NodeStatus.COMPLETED.value,
            },
        }

    # Match each required qualification against company quals
    matched: list[str] = list(generic_quals)  # generic quals are auto-matched
    missing: list[str] = []        # hard fail: missing & no substitutable item
    warning_quals: list[str] = []  # missing but a substitutable item exists

    for req in specific_quals:
        if any(_qual_matches(req, avail) for avail in company_quals):
            matched.append(req)
        elif _find_qual_alternative(req, company_quals) is not None:
            # Missing, but the company library has a semantically equivalent
            # certificate (e.g. ISO9001 vs GB/T19001) → soft warning, continue.
            warning_quals.append(req)
        else:
            missing.append(req)

    if missing:
        # Hard fail: a required specific qualification is missing AND no
        # substitutable certificate exists in the company library → terminate.
        verdict = "FAIL"
        risk_level = "high"
        summary = f"缺少 {len(missing)} 项硬资质（无替代项）：{', '.join(missing[:3])}"
    elif warning_quals:
        # Soft warning: required qualification missing, but a substitutable
        # certificate exists → continue (spec ⑤: WARNING → 继续).
        verdict = "WARNING"
        risk_level = "medium"
        summary = (
            f"缺少 {len(warning_quals)} 项硬资质但存在可替代资质："
            f"{', '.join(warning_quals[:3])}"
        )
    else:
        verdict = "PASS"
        risk_level = "low"
        summary = f"企业资质满足全部 {len(specific_quals)} 项具体要求"

    logger.info(
        f"EligibilityChecker: verdict={verdict}, "
        f"matched={len(matched)}, missing={len(missing)}, "
        f"generic_auto_passed={len(generic_quals)}"
    )

    return {
        "eligibility_report": {
            "verdict": verdict,
            "missing_quals": missing,
            "warning_quals": warning_quals,
            "matched_quals": matched,
            "risk_level": risk_level,
            "summary": summary,
        },
        "node_status": {
            **state.get("node_status", {}),
            "EligibilityChecker": NodeStatus.COMPLETED.value,
        },
    }
