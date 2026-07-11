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
        verdict == FAIL    → END (terminate, avoid wasting generation cost)
        verdict == WARNING → continue (some quals missing but alternatives exist)
        verdict == PASS    → continue
"""

import json
import logging
from pathlib import Path
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

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

    # Load company qualifications
    if company_quals is None:
        company_quals = _load_company_quals(quals_path)

    if not required_quals:
        # No qualification requirements → auto-pass
        logger.info("EligibilityChecker: no qualification requirements — PASS")
        return {
            "eligibility_report": {
                "verdict": "PASS",
                "missing_quals": [],
                "matched_quals": [],
                "risk_level": "low",
                "summary": "招标文件无资质要求",
            },
            "node_status": {
                **state.get("node_status", {}),
                "EligibilityChecker": NodeStatus.COMPLETED.value,
            },
        }

    if not company_quals:
        # No company data → WARNING (can't verify, but don't block)
        logger.warning(
            "EligibilityChecker: no company quals data — cannot verify "
            f"{len(required_quals)} requirements (WARNING)"
        )
        return {
            "eligibility_report": {
                "verdict": "WARNING",
                "missing_quals": list(required_quals),
                "matched_quals": [],
                "risk_level": "medium",
                "summary": "企业资质库为空，无法验证资质是否满足要求",
            },
            "node_status": {
                **state.get("node_status", {}),
                "EligibilityChecker": NodeStatus.COMPLETED.value,
            },
        }

    # Match each required qualification against company quals
    matched: list[str] = []
    missing: list[str] = []

    for req in required_quals:
        found = any(_qual_matches(req, avail) for avail in company_quals)
        if found:
            matched.append(req)
        else:
            missing.append(req)

    if missing:
        # Distinguish hard fail (missing required quals) from warning
        # For MVP: any missing qualification is a FAIL since tender thresholds
        # are typically mandatory.  This can be refined with a "mandatory" flag.
        verdict = "FAIL"
        risk_level = "high"
        summary = f"缺少 {len(missing)} 项资质要求：{', '.join(missing[:3])}"
    else:
        verdict = "PASS"
        risk_level = "low"
        summary = f"企业资质满足全部 {len(required_quals)} 项要求"

    logger.info(
        f"EligibilityChecker: verdict={verdict}, "
        f"matched={len(matched)}, missing={len(missing)}"
    )

    return {
        "eligibility_report": {
            "verdict": verdict,
            "missing_quals": missing,
            "matched_quals": matched,
            "risk_level": risk_level,
            "summary": summary,
        },
        "node_status": {
            **state.get("node_status", {}),
            "EligibilityChecker": NodeStatus.COMPLETED.value,
        },
    }
