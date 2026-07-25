"""QualityChecker node — validates generated sections.

Contract (from node_interfaces.md):
    def quality_checker(state: AgentState) -> dict
    Input:  state.sections, state.requirements
    Output: {quality_report: {verdict, completeness, compliance, consistency}, node_status: {...}}
    Constraints:
    - completeness: each scoring item has a corresponding section
    - compliance: no banned words, format rules followed
    - consistency: consistent company names, dates, figures across sections
    - PASS only when all checks pass
"""

import logging
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Banned words (政府采购常见违禁词) ──────────────────────────────────

BANNED_WORDS = ["绝对", "最", "第一", "唯一", "顶级", "国家级", "最高级", "最佳"]

# ── Format-fixed short chapter (格式固定型短章) ─────────────────────
# 仅授权委托书（ch2_authorization）由 spec ⑦ 明确列为「格式固定型」，
# 由模板确定性生成，本就不可能达到 8000 字门槛；若硬性判 <8000 字 FAIL
# 并进入反馈环重生成，会浪费多轮重生成（spec ⑧ 在超轮次后强制通过，
# 不会无限循环，但此类章节无重生成必要）。故仅豁免其字数门槛，仍做
# 「非空」校验。其余章节（含 ch7_schedule / ch8_after_sales）按 spec ⑧
# 走正常 8000 字门槛 + 超轮次强制通过，不在此豁免。
FORMAT_FIXED_SHORT_CHAPTERS = {
    "ch2_authorization",
}

# ── Local checkers (zero-LLM) ─────────────────────────────────────────


def _check_completeness(
    requirements: dict, sections: dict[str, str]
) -> tuple[dict[str, bool], list[str]]:
    """Check that each scoring item has related content in the generated sections.

    Phase B1 fix: the old heuristic accepted coverage as soon as *any* section
    had >100 chars AND the section count ≥ scoring-item count — meaning 8
    chapters of off-topic text would pass every check.  Now we require each
    scoring item to be explicitly mentioned in at least one section, falling
    back to a keyword overlap test only when the item name is very short.
    """
    completeness: dict[str, bool] = {}
    issues: list[str] = []

    scoring_items = requirements.get("scoring", [])
    if not scoring_items:
        return {}, []

    # Pre-compute a lowercase corpus for keyword overlap testing
    corpus_lower = " ".join(c.lower() for c in sections.values())

    for item in scoring_items:
        item_name = item.get("item_name", "")
        if not item_name:
            continue
        name_lower = item_name.lower()

        # 1) Explicit mention of the full item name in any section
        found_explicit = any(name_lower in c.lower() for c in sections.values())

        # 2) Keyword overlap — split the item name into meaningful tokens and
        #    require at least 60% of them to appear somewhere.  This catches
        #    paraphrased coverage (e.g. "人员配置方案" covered by a section that
        #    talks about "人员" and "配置") without the old blank cheque.
        tokens = [t for t in name_lower.replace("，", " ").split() if len(t) >= 2]
        if not found_explicit and tokens:
            hits = sum(1 for t in tokens if t in corpus_lower)
            found_keyword = hits >= max(1, int(len(tokens) * 0.6))
        else:
            found_keyword = False

        found = found_explicit or found_keyword
        completeness[item_name] = found
        if not found:
            issues.append(f"评分项「{item_name}」在生成内容中未找到对应论述")

    return completeness, issues


def _check_compliance(sections: dict[str, str]) -> list[str]:
    """Check for banned words and basic format compliance.

    Uses word boundary matching to avoid flagging "第一章" as "第一".
    """
    import re
    violations: list[str] = []

    # Words that need word-boundary checking (e.g., "第一" should not match "第一章")
    context_sensitive = {"第一", "唯一", "最"}
    word_boundary_map = {
        "第一": re.compile(r"(?<!第)第一(?!\s*[章节条款])"),
        "唯一": re.compile(r"唯一(?!标识|编码|编号)"),
        "最": re.compile(r"最(?!终|后|新|近|高|低|大|小|多|少|早|晚|初|优化|重要)"),
    }

    for section_name, content in sections.items():
        for word in BANNED_WORDS:
            if word in context_sensitive and word in word_boundary_map:
                if word_boundary_map[word].search(content):
                    violations.append(f"「{section_name}」包含违禁词「{word}」")
            elif word in content:
                violations.append(f"「{section_name}」包含违禁词「{word}」")

    # Check minimum content length per section
    for section_name, content in sections.items():
        if len(content) < 50:
            violations.append(f"「{section_name}」内容过短（{len(content)}字）")

    return violations


# Common prefixes that get captured by the regex but are NOT part of
# the company name — verbs, prepositions, year characters, etc.
_COMPANY_PREFIX_NOISE = [
    "致", "参加", "年", "的", "和", "与", "及", "或", "由", "向", "给",
    "系", "是", "为", "对", "将", "被", "把", "让", "使", "令",
    # Full sentence-fragment markers — if these appear in the extracted
    # name, it's a sentence fragment, not a company name
]

# Words that indicate the extracted text is a sentence fragment, not a
# real company name.  If any of these appear in the middle of the
# extracted name (not just at the end), it's likely a false positive.
_SENTENCE_FRAGMENT_MARKERS = [
    "符合国家", "技术条件", "国家和", "现行技术", "技术政策", "技术标准",
    "集团有限公司现行", "信用评价", "投标文件中", "物资买卖",
]

# Maximum length of a realistic Chinese company name (including suffix).
# Most real company names are 8-25 chars.  Anything > 30 chars is almost
# certainly a sentence fragment that happens to end with a suffix.
_MAX_COMPANY_NAME_LEN = 30


def _extract_company_names(content: str) -> list[str]:
    """Extract full company names from text.

    Matches Chinese company name patterns:
    - XX有限公司 / XX有限责任公司 / XX股份有限公司 / XX集团有限公司

    Phase B6 fixes:
      - Strip common non-company prefixes (致, 参加, 年, 系, etc.)
      - Filter out sentence fragments (e.g. "投标文件中的技术条件符合国家和中国国家铁路集团有限公司")
      - Filter out names exceeding realistic length (>30 chars)
      - Skip 【待填写】 placeholder content
    """
    import re

    # Skip content that is entirely a placeholder
    if re.search(r"【待填写[^】]*】", content) and len(content.strip()) < 50:
        return []

    company_suffixes = (
        r"(?:股份有限公司|有限责任公司|有限公司|集团有限公司|"
        r"实业发展有限公司|科技发展有限公司|"
        r"人力资源有限公司|劳务有限公司|管理有限公司|服务有限公司)"
    )
    name_pattern = re.compile(
        r"([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9·]{1,29}" + company_suffixes + r")"
    )

    names: list[str] = []
    for m in name_pattern.finditer(content):
        name = m.group(1).strip()

        # ── Length filter: skip sentence fragments ────────────────────
        if len(name) > _MAX_COMPANY_NAME_LEN:
            continue

        # ── Sentence-fragment filter ──────────────────────────────────
        if any(marker in name for marker in _SENTENCE_FRAGMENT_MARKERS):
            continue

        # ── Strip common non-company prefixes ─────────────────────────
        # Iteratively strip single-char and multi-char prefixes
        changed = True
        while changed:
            changed = False
            for prefix in _COMPANY_PREFIX_NOISE:
                if name.startswith(prefix) and len(name) > len(prefix) + 4:
                    name = name[len(prefix):]
                    changed = True

        # ── Strip "XXX系" prefix (e.g. "李强系广州粤通..." → "广州粤通...") ──
        # Pattern: 2-4 Chinese chars + "系" + real company name
        xi_match = re.match(r"^[\u4e00-\u9fa5]{2,4}系(.+)", name)
        if xi_match and len(xi_match.group(1)) > 4:
            name = xi_match.group(1)

        # ── Skip 【待填写】 placeholders that leaked into matches ─────
        if "待填写" in name or "【" in name or "】" in name:
            continue

        if len(name) > 4 and name not in names:
            names.append(name)

    # Also try "投标人：XXX" pattern — but skip placeholders
    bidder_pattern = re.compile(
        r"(?:投标人|投标方|供应商|公司名称)\s*[：:为是]\s*"
        r"([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9·]{3,40})"
    )
    for m in bidder_pattern.finditer(content):
        name = re.sub(r"[，,。；;（(].*$", "", m.group(1)).strip()

        # Skip placeholder content
        if "待填写" in name or "【" in name or "】" in name:
            continue

        # Apply same filters as above
        if len(name) > _MAX_COMPANY_NAME_LEN:
            continue
        if any(marker in name for marker in _SENTENCE_FRAGMENT_MARKERS):
            continue

        if len(name) >= 4 and name not in names:
            names.append(name)

    return names


def _build_known_companies(state: AgentState) -> set[str]:
    """Build a whitelist of company names known from the tender document & contract.

    Sources:
      1. state.documents[].parsed_content — extract company names from the
         actual tender document uploaded by the user
      2. state.requirements.tenderer_name — extracted by ReqExtractor
      3. state.project_contract.tenderer_name / bidder_name — from ContractExtractor

    Only companies in this whitelist are considered "real" for consistency
    checking.  Companies that appear in the generated bid document but NOT
    in the tender document (e.g. "中国国家铁路集团有限公司" from template
    boilerplate) are filtered out.
    """
    known: set[str] = set()

    # 1. Extract from tender document content
    documents = state.get("documents", [])
    for doc in documents:
        parsed = doc.get("parsed_content", "") or doc.get("content", "")
        if parsed:
            known.update(_extract_company_names(parsed))

    # 2. From requirements (extracted by ReqExtractor from tender document)
    requirements = state.get("requirements", {})
    tenderer = requirements.get("tenderer_name", "")
    if tenderer and len(tenderer) >= 4:
        known.add(tenderer)

    # 3. From project contract (merged from form + tender + LLM)
    contract = state.get("project_contract", {})
    for field in ("tenderer_name", "bidder_name"):
        val = contract.get(field, "")
        if val and len(val) >= 4:
            known.add(val)

    return known


def _check_consistency(
    sections: dict[str, str],
    state: AgentState | None = None,
) -> list[str]:
    """Check for cross-section company name inconsistencies.

    Extracts full company names from each section and compares them.
    Reports exactly which sections use which names, so the user can see
    the specific inconsistency instead of a vague warning.

    Phase B6 fixes:
      - If state is provided, build a whitelist of known companies from the
        tender document and contract.  Only companies in the whitelist are
        checked for consistency.  Companies not in the tender document are
        filtered out (e.g. "中国国家铁路集团有限公司" from template boilerplate).
      - 【待填写】 placeholders are not treated as inconsistencies.
    """
    import re

    issues: list[str] = []

    # ── Build whitelist of known companies from tender document ──────
    known_companies: set[str] = set()
    if state is not None:
        known_companies = _build_known_companies(state)
        if known_companies:
            logger.info(
                f"_check_consistency: whitelist has {len(known_companies)} "
                f"known companies from tender document & contract"
            )

    # ── Helper: check if a name is in the whitelist (fuzzy) ──────────
    import difflib
    def _is_known(name: str, whitelist: set[str]) -> bool:
        """Check if name matches any whitelisted company (fuzzy, ratio≥0.8)."""
        if not whitelist:
            return True  # no whitelist → don't filter
        for known in whitelist:
            ratio = difflib.SequenceMatcher(None, name, known).ratio()
            if ratio >= 0.8:
                return True
            # Also check if one contains the other (handles partial matches)
            if len(name) >= 4 and len(known) >= 4:
                if name in known or known in name:
                    return True
        return False

    # Extract company names per section
    names_by_section: dict[str, list[str]] = {}
    for section_name, content in sections.items():
        found = _extract_company_names(content)

        # ── Filter: only keep companies in the whitelist ─────────────
        if known_companies:
            found = [n for n in found if _is_known(n, known_companies)]

        if found:
            names_by_section[section_name] = found

    if not names_by_section:
        return issues

    # Collect all unique company names across all sections
    all_names: set[str] = set()
    for names in names_by_section.values():
        all_names.update(names)

    # If only one unique company name → consistent
    if len(all_names) <= 1:
        return issues

    # Phase B6: If we have a whitelist and ALL extracted names are in it,
    # this is NOT an inconsistency — the tenderer and bidder naturally
    # appear in different sections (e.g. tenderer in 投标函, bidder in
    # 法定代表人身份证明).  Only flag when there are names that DON'T
    # match any known company (which would indicate a real mix-up).
    if known_companies:
        all_in_whitelist = all(_is_known(n, known_companies) for n in all_names)
        if all_in_whitelist:
            return issues

    # Multiple names found — group by core name (strip suffixes) to detect
    # whether they're truly different companies or just suffix variants
    def _core_name(name: str) -> str:
        return re.sub(
            r"(股份有限公司|有限责任公司|有限公司|集团有限公司|"
            r"实业发展有限公司|科技发展有限公司|"
            r"人力资源有限公司|劳务有限公司|管理有限公司|服务有限公司)$",
            "",
            name,
        )

    # Use similarity comparison so "广州华南人力" and "广州华南人力资源"
    # are recognized as the same company (ratio >= 0.8)
    core_names = [_core_name(n) for n in all_names]
    unique_cores: list[str] = []
    for core in core_names:
        matched = False
        for existing in unique_cores:
            ratio = difflib.SequenceMatcher(None, core, existing).ratio()
            if ratio >= 0.8:
                matched = True
                break
        if not matched:
            unique_cores.append(core)

    # Only report if there are 2+ distinct core names
    if len(unique_cores) <= 1:
        # All names share the same core — just suffix variants, not a real issue
        return issues

    # Build a detailed inconsistency report
    # For each section, list the company names it uses
    detail_lines: list[str] = []
    for section_name, names in names_by_section.items():
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_names: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                unique_names.append(n)
        detail_lines.append(f"  • 「{section_name}」: {' / '.join(unique_names)}")

    # List all distinct company names found
    all_unique = sorted(all_names)
    detail_lines.insert(0, f"  发现 {len(all_unique)} 个不同公司名称:")
    for name in all_unique:
        detail_lines.insert(1, f"    - {name}")

    issues.append(
        "跨章节公司名称引用不一致:\n"
        + "\n".join(detail_lines)
    )

    return issues


# ── LLM-aided check ────────────────────────────────────────────────────

_QUALITY_CHECK_PROMPT = """你是一位严苛的招标文件评审专家。请检查以下标书章节内容的质量。

评分标准：
{scoring_context}

已生成章节内容：
{sections_context}

请以严格的 JSON 格式返回（不要包含其他文字）：
{{
  "verdict": "PASS" 或 "FAIL",
  "score": 0到100之间的数字,
  "completeness_issues": ["问题1", "问题2"],
  "compliance_issues": ["违规1", "违规2"],
  "consistency_issues": ["不一致1", "不一致2"],
  "summary": "简要总结"
}}

检查要点：
1. 完整性：每个评分项是否都有对应章节论述？
2. 合规性：是否有违禁词（如"绝对""第一""唯一"）？格式是否符合招标要求？
3. 一致性：公司名称、日期、数据在不同章节间是否一致？
4. 质量：语言是否专业严谨？是否有空话套话？

重要排除规则（不得作为问题报告）：
- 【待填写】占位符是正常的模板预留位置，不视为不一致或缺失问题
- 法规引用中的机构名称（如"中国国家铁路集团有限公司"出现在"符合国家和中国国家铁路集团有限公司现行技术标准"等句式中）属于法规条款引用，不视为公司名称不一致
- 招标文件中不存在的企业名称（如模板中引用的第三方机构）不纳入一致性检查
- 仅当两个章节中出现了明确不同的实际企业名称时，才报告公司名称不一致

请务必只返回 JSON，不要包含任何其他文字。"""


def _llm_quality_check(
    state: AgentState,
    llm_fn: Callable[[str], str],
) -> dict:
    """Use LLM to perform comprehensive quality check.

    Phase B1 fixes:
      - Per-section truncation raised from 2000 → 8000 chars so longer
        chapters are actually reviewed instead of silently chopped.
      - Non-JSON responses no longer default to PASS — they return FAIL so
        quality problems surface instead of being hidden.

    L4 fix:
      - Per spec §⑧, LLM 质检遵循「三重 JSON 容错 → 重试 1 次 → FAIL」。
        首次调用若异常 / 返回非 JSON / JSON 结构不符预期，在**三重容错
        都失败**之后重试 1 次；重试仍失败才判 FAIL。重试间隔极小 sleep(0.5s)。
    """
    import json
    import re
    import time

    reqs = state.get("requirements", {})
    scoring_items = reqs.get("scoring", [])
    scoring_text = "\n".join(
        f"- {item.get('item_name', '')}: {item.get('score', 0)}分 {item.get('criteria', '')}"
        for item in scoring_items
    )

    sections = state.get("sections", {})
    # Phase B1: raise truncation to 8000 chars so substantial chapters are
    # actually reviewed.  Very long sections are still capped to keep the LLM
    # context manageable, but 2000 was far too aggressive (a 3000-char service
    # plan lost half its content).
    sections_text = "\n\n".join(
        f"【{name}】\n{content[:8000]}"
        for name, content in sections.items()
    )

    prompt = _QUALITY_CHECK_PROMPT.format(
        scoring_context=scoring_text or "无特定评分标准",
        sections_context=sections_text or "无章节内容",
    )

    # ── L4 修复：三重 JSON 容错 → 重试 1 次 → FAIL ──
    # 首次调用若异常 / 非 JSON / JSON 结构不符预期，在**三重容错都失败**
    # 之后重试 1 次；重试仍失败才判 FAIL。重试间隔极小 sleep(0.5s)。
    max_attempts = 2  # 首次 + 重试 1 次
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = llm_fn(prompt)

            # ── 三重 JSON 容错：直接 → ```json``` → 花括号 ──
            response = response.strip()
            code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
            if code_match:
                response = code_match.group(1).strip()
            brace_match = re.search(r"\{[\s\S]*\}", response)
            if brace_match:
                response = brace_match.group(0)

            result = json.loads(response)

            # JSON 结构不符预期（非 dict / 缺 verdict）→ 视为失败，进入重试
            if not isinstance(result, dict) or "verdict" not in result:
                raise ValueError(
                    "LLM 质检返回的 JSON 结构不符合预期（非对象或缺少 verdict 字段）"
                )

            return result

        except Exception as e:  # 异常 / 非 JSON / 结构不符 → 重试
            last_err = e
            logger.warning(
                f"LLM 质检第 {attempt}/{max_attempts} 次调用失败"
                f"（异常/非JSON/结构不符）: {e}"
            )
            if attempt < max_attempts:
                time.sleep(0.5)  # 极小间隔，规避瞬时抖动

    # 重试 1 次后仍失败 → 判 FAIL（不再静默 PASS，承 Phase B1）
    logger.warning(
        "LLM 质检重试 1 次后仍失败 — 判定 FAIL"
        f"（上次错误: {last_err}）"
    )
    return {
        "verdict": "FAIL",
        "score": 0,
        "completeness_issues": ["LLM 质检返回非 JSON 格式，无法解析"],
        "compliance_issues": [],
        "consistency_issues": [],
        "summary": "LLM 响应解析失败",
    }


# ── LangGraph Node ─────────────────────────────────────────────────────


def quality_checker(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """Perform quality check on generated sections.

    Uses local rule-based checks. If llm_fn is provided, also does LLM-aided
    deep check.

    Args:
        state: AgentState with sections and requirements
        llm_fn: Optional LLM function for deep check

    Returns:
        {quality_report: {verdict, completeness, compliance, consistency, ...},
         node_status: {QualityChecker: COMPLETED}}
    """
    requirements = state.get("requirements", {})
    sections = state.get("sections", {})

    if not sections:
        logger.warning("No sections to check")
        return {
            "quality_report": {
                **state.get("quality_report", {}),
                "verdict": "FAIL",
                "compliance": ["无章节内容可检查"],
            },
            "node_status": {
                **state.get("node_status", {}),
                "QualityChecker": NodeStatus.COMPLETED.value,
            },
        }

    # ── Local checks ───────────────────────────────────────────────────
    completeness, completeness_issues = _check_completeness(requirements, sections)
    compliance_issues = _check_compliance(sections)
    consistency_issues = _check_consistency(sections, state)

    # ── Section completeness threshold (spec §⑧: 每章≥8000字) ──────────
    # Configurable via state["min_section_chars"] so tests can lower it.
    # Default 8000 for production; tests typically set 0 to disable.
    min_section_chars = state.get("min_section_chars", 8000)
    if min_section_chars > 0:
        for section_name, content in sections.items():
            stripped = content.strip()
            # Skip placeholder-only sections (e.g. 【生成失败：...】)
            if stripped.startswith("【生成失败"):
                continue
            # Empty/whitespace-only content is a completeness failure for ANY
            # chapter — including format-fixed ones. The exemption below must
            # not silently pass an empty generation.
            if not stripped:
                completeness_issues.append(
                    f"「{section_name}」内容为空，请补充"
                )
                continue
            # N3 fix: 仅格式固定型短章（ch2_authorization, spec ⑦）豁免字数门槛；
            # 仍要求非空（上方已校验）。其余章节按 spec ⑧ 正常判 8000 字。
            if section_name in FORMAT_FIXED_SHORT_CHAPTERS:
                continue
            if len(content) < min_section_chars:
                completeness_issues.append(
                    f"「{section_name}」内容不完整（{len(content)}字，要求≥{min_section_chars}字）"
                )

    all_issues = completeness_issues + compliance_issues + consistency_issues
    total_items = len(requirements.get("scoring", [])) + len(compliance_issues) + len(consistency_issues)
    passed_items = total_items - len(all_issues)
    verdict = "PASS" if not all_issues else "FAIL"

    # ── LLM deep check (optional) ──────────────────────────────────────
    llm_score: int | None = None
    if llm_fn and sections:
        try:
            llm_result = _llm_quality_check(state, llm_fn)
            if llm_result.get("verdict") == "FAIL":
                verdict = "FAIL"
                # Phase B6: filter out issues that are about 【待填写】
                # placeholders — these are expected template positions,
                # not actual quality problems.
                _placeholder_keywords = ["待填写", "占位符", "占位", "未保持统一", "半成品"]
                def _is_placeholder_issue(text: str) -> bool:
                    """Check if an issue is about 【待填写】 placeholders."""
                    return any(kw in text for kw in _placeholder_keywords)

                llm_compliance = [i for i in llm_result.get("compliance_issues", []) if not _is_placeholder_issue(i)]
                llm_consistency = [i for i in llm_result.get("consistency_issues", []) if not _is_placeholder_issue(i)]
                llm_completeness = [i for i in llm_result.get("completeness_issues", []) if not _is_placeholder_issue(i)]
                compliance_issues.extend(llm_compliance)
                consistency_issues.extend(llm_consistency)
                completeness_issues.extend(llm_completeness)
            llm_score = llm_result.get("score")
        except Exception as e:
            logger.error(f"LLM quality check failed: {e}")

    # ── Compute 0-100 score ────────────────────────────────────────────
    # Phase B1: add a numeric score so users can see how weak/strong the draft
    # is, not just a binary PASS/FAIL.  Combines local coverage rate with the
    # LLM score (when available).
    total_scoring = len(requirements.get("scoring", []))
    covered_scoring = sum(1 for v in completeness.values() if v)
    coverage_rate = covered_scoring / total_scoring if total_scoring else 1.0
    local_score = int(coverage_rate * 100)
    # Penalty for compliance/consistency issues (each issue -5, floored at 0)
    local_score = max(0, local_score - 5 * (len(compliance_issues) + len(consistency_issues)))
    final_score = llm_score if llm_score is not None else local_score

    quality_report = {
        "verdict": verdict,
        "score": final_score,
        "completeness": completeness,
        "compliance": compliance_issues,
        "consistency": consistency_issues,
        "total_items": max(1, total_items),
        "passed_items": max(0, passed_items),
        "coverage_rate": round(coverage_rate, 2),
    }

    logger.info(
        f"QualityChecker: verdict={verdict}, "
        f"issues={len(all_issues)} (completeness={len(completeness_issues)}, "
        f"compliance={len(compliance_issues)}, consistency={len(consistency_issues)})"
    )

    return {
        "quality_report": quality_report,
        "node_status": {
            **state.get("node_status", {}),
            "QualityChecker": NodeStatus.COMPLETED.value,
        },
    }
