"""ScoreSimulator node — predicts evaluation scores for generated sections.

Phase B4 (spec.md §1.4): inserted between QualityChecker (PASS) and
DocumentAssembler so the user gets a predicted score breakdown before the
final document is assembled.  Highlights weak scoring items so the user
knows where to focus revisions.

Contract:
    def score_simulator(state: AgentState) -> dict
    Input:  state.sections, state.requirements.scoring
    Output: {score_simulation: {scores, total, rank_estimate}, node_status}
"""

import json
import logging
import re
from typing import Callable

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Prompt ─────────────────────────────────────────────────────────────

_SCORE_SIM_PROMPT = """你是一位资深的招投标评审专家。请模拟评审以下标书章节内容，按评分标准逐项打分。

评分标准：
{scoring_context}

标书章节内容：
{sections_context}

请以严格的 JSON 格式返回（不要包含其他文字）：
{{
  "scores": [
    {{
      "item": "评分项名称",
      "max_score": 数字,
      "predicted": 预测得分,
      "gap": max_score - predicted,
      "suggestion": "薄弱环节改进建议（gap=0 时为 null）"
    }}
  ],
  "total": {{
    "predicted": 预测总分,
    "max": 满分,
    "rank_estimate": "排名预估（如：前3、中等、偏后）"
  }}
}}

打分原则：
- 内容完整且专业 → 满分或接近满分
- 内容覆盖但有瑕疵 → 扣 1-3 分
- 内容缺失或跑题 → 扣 5 分以上
- gap=0 的项 suggestion 为 null

请务必只返回 JSON。"""


# ── Heuristic scorer (no LLM) ──────────────────────────────────────────


def _tokenize_cn(text: str) -> list[str]:
    """N09 fix: Tokenize text for keyword overlap, with Chinese support.

    For Chinese text (no spaces), generates 2-character n-grams.
    For mixed text, also splits on spaces/punctuation for ASCII tokens.
    """
    import re

    tokens: list[str] = []

    # Split by spaces and punctuation for ASCII tokens
    parts = re.split(r"[\s,，;；:：、/\\()（）\[\]【】]+", text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) >= 2:
            # Check if it's mostly CJK
            cjk_count = sum(1 for c in part if '\u4e00' <= c <= '\u9fff')
            if cjk_count >= len(part) * 0.5:
                # Chinese text: generate 2-char n-grams
                for i in range(len(part) - 1):
                    tokens.append(part[i:i + 2])
            else:
                tokens.append(part)
        elif len(part) == 1 and '\u4e00' <= part <= '\u9fff':
            # Single CJK character — skip (too short for meaningful matching)
            pass

    return tokens


def _heuristic_score(state: AgentState) -> dict:
    """Estimate scores without an LLM using content-coverage heuristics.

    Each scoring item gets a predicted score proportional to how thoroughly
    its content appears in the generated sections.  This is a rough estimate
    used when no LLM is configured.
    """
    reqs = state.get("requirements", {})
    scoring_items = reqs.get("scoring", [])
    sections = state.get("sections", {})

    if not scoring_items:
        return {
            "scores": [],
            "total": {"predicted": 0, "max": 0, "rank_estimate": "无法评估"},
            "method": "heuristic_no_scoring",
        }

    corpus = " ".join(sections.values()).lower()
    scores = []
    total_pred = 0
    total_max = 0

    for item in scoring_items:
        name = item.get("item_name", "")
        max_score = item.get("score", 0)
        name_lower = name.lower()

        # Coverage: explicit mention + content length near the mention
        mentioned = name_lower in corpus
        if mentioned:
            # Find the section that mentions it and check its length
            best_len = 0
            for content in sections.values():
                if name_lower in content.lower():
                    best_len = max(best_len, len(content))
            # 500+ chars of relevant content → near full marks
            if best_len >= 500:
                ratio = 0.95
            elif best_len >= 200:
                ratio = 0.80
            else:
                ratio = 0.60
        else:
            # Keyword overlap fallback
            # N09 fix: use character n-gram tokenization for Chinese text.
            # Previously, split() only worked for space-delimited text (English),
            # leaving Chinese names as single tokens that rarely matched.
            tokens = _tokenize_cn(name_lower)
            if tokens:
                hits = sum(1 for t in tokens if t in corpus)
                ratio = hits / len(tokens) * 0.5  # partial credit only
            else:
                ratio = 0.0

        predicted = round(max_score * ratio)
        predicted = min(predicted, max_score)
        gap = max_score - predicted

        scores.append({
            "item": name,
            "max_score": max_score,
            "predicted": predicted,
            "gap": gap,
            "suggestion": f"建议加强「{name}」的论述深度" if gap > 0 else None,
        })
        total_pred += predicted
        total_max += max_score

    # Rank estimate based on percentage
    pct = total_pred / total_max if total_max else 0
    if pct >= 0.90:
        rank = "前3"
    elif pct >= 0.75:
        rank = "中上"
    elif pct >= 0.60:
        rank = "中等"
    else:
        rank = "偏后"

    return {
        "scores": scores,
        "total": {"predicted": total_pred, "max": total_max, "rank_estimate": rank},
        "method": "heuristic",
    }


# ── LLM-aided scorer ──────────────────────────────────────────────────


def _llm_score(state: AgentState, llm_fn: Callable[[str], str]) -> dict:
    """Use LLM to predict evaluation scores."""
    reqs = state.get("requirements", {})
    scoring_items = reqs.get("scoring", [])
    sections = state.get("sections", {})

    scoring_text = "\n".join(
        f"- {item.get('item_name', '')}: {item.get('score', 0)}分 — {item.get('criteria', '')}"
        for item in scoring_items
    )
    sections_text = "\n\n".join(
        f"【{name}】\n{content[:6000]}"
        for name, content in sections.items()
    )

    prompt = _SCORE_SIM_PROMPT.format(
        scoring_context=scoring_text or "无评分标准",
        sections_context=sections_text or "无章节内容",
    )

    response = llm_fn(prompt)
    response = response.strip()

    # Extract JSON
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
    if code_match:
        response = code_match.group(1).strip()
    brace_match = re.search(r"\{[\s\S]*\}", response)
    if brace_match:
        response = brace_match.group(0)

    try:
        result = json.loads(response)
        result["method"] = "llm"
        return result
    except json.JSONDecodeError:
        logger.warning("ScoreSimulator LLM returned non-JSON — falling back to heuristic")
        return _heuristic_score(state)


# ── LangGraph Node ─────────────────────────────────────────────────────


def score_simulator(
    state: AgentState,
    llm_fn: Callable[[str], str] | None = None,
) -> dict:
    """Predict evaluation scores for the generated bid sections.

    Args:
        state: AgentState with sections and requirements.scoring
        llm_fn: Optional LLM for precise scoring; falls back to heuristic.

    Returns:
        {score_simulation: {scores, total, rank_estimate}, node_status}
    """
    sections = state.get("sections", {})

    if not sections:
        logger.warning("ScoreSimulator: no sections to score")
        return {
            "score_simulation": {
                "scores": [],
                "total": {"predicted": 0, "max": 0, "rank_estimate": "无内容"},
                "method": "empty",
            },
            "node_status": {
                **state.get("node_status", {}),
                "ScoreSimulator": NodeStatus.COMPLETED.value,
            },
        }

    # Use LLM if available, otherwise heuristic
    if llm_fn:
        try:
            simulation = _llm_score(state, llm_fn)
        except Exception as e:
            logger.error(f"ScoreSimulator LLM failed: {e} — falling back to heuristic")
            simulation = _heuristic_score(state)
    else:
        simulation = _heuristic_score(state)

    total = simulation.get("total", {})
    logger.info(
        f"ScoreSimulator: predicted {total.get('predicted', 0)}/"
        f"{total.get('max', 0)} ({total.get('rank_estimate', '?')}), "
        f"method={simulation.get('method')}"
    )

    return {
        "score_simulation": simulation,
        "node_status": {
            **state.get("node_status", {}),
            "ScoreSimulator": NodeStatus.COMPLETED.value,
        },
    }
