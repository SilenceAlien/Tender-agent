"""TemplateMatcher node — matches extracted requirements against template library.

Contract (from node_interfaces.md):
    def template_matcher(state: AgentState) -> dict
    Input:  state.requirements
    Output: {matched_templates: [template_id, ...], node_status: {...}}
    Constraints:
    - Only queries template index (HNSWFlat)
    - Returns top-3 template IDs, sorted by cosine similarity desc
    - If no hit (similarity < 0.3) → empty list but mark COMPLETED
"""

import logging
from typing import Callable

import numpy as np

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# Similarity threshold: below this, consider no match
MIN_SIMILARITY = 0.3

# N08 fix: aligned with knowledge_base directory names (all include "类")
# and upload_panel options.  Previously lacked "类" suffix and missing "劳务管理服务类".
TEMPLATE_TYPES = ["服务类", "货物类", "工程类", "集成类", "运维类", "劳务外包类", "劳务管理服务类"]


def _build_query_text(requirements: dict) -> str:
    """Build a search query from extracted requirements."""
    parts: list[str] = []

    scoring = requirements.get("scoring", [])
    if scoring:
        scoring_text = " ".join(
            f"{item.get('item_name', '')} {item.get('criteria', '')}"
            for item in scoring
        )
        parts.append(f"评分项: {scoring_text}")

    qualifications = requirements.get("qualifications", [])
    if qualifications:
        parts.append(f"资质要求: {' '.join(qualifications)}")

    tech_specs = requirements.get("tech_specs", [])
    if tech_specs:
        tech_text = " ".join(
            spec.get("spec_name", "") for spec in tech_specs
        )
        parts.append(f"技术规格: {tech_text}")

    return " ".join(parts)


def build_default_search_fn(
    embedder=None,
    indices: dict | None = None,
) -> Callable[[str, int], list[tuple[str, float]]] | None:
    """Build a default search_fn from the template library.

    BUG-05 fix: Previously, ``build_graph()`` never injected a ``search_fn``
    into ``template_matcher``, so template matching always returned empty
    results. This factory creates a ready-to-use search function.

    Args:
        embedder: A BaseEmbedder instance. If None, uses MockEmbedder(dim=64)
                  matching the template library's dimension.
        indices: A dict of {bid_type: VectorIndexManager}. If None, builds
                 from the template library.

    Returns:
        A search function ``(query: str, k: int) -> [(id, distance), ...]``,
        or None if no indices could be built.
    """
    from core.retrieval.embeddings import MockEmbedder
    from core.retrieval.template_library import build_template_index

    if embedder is None:
        embedder = MockEmbedder(dim=64)
    if indices is None:
        try:
            indices = build_template_index()
        except Exception as e:
            logger.warning(f"Failed to build template index: {e}")
            return None

    if not indices:
        return None

    # Combine all type-specific indices into one flat list for searching.
    # Each VectorIndexManager.search returns list[list[tuple[str, float]]]
    # (one list per query vector). We pass a single query vector.
    def search_fn(query: str, k: int = 5) -> list[tuple[str, float]]:
        query_vec = embedder.embed_query(query).reshape(1, -1).astype(np.float32)
        all_results: list[tuple[str, float]] = []
        for mgr in indices.values():
            if mgr.size == 0:
                continue
            results = mgr.search(query_vec, k=k)
            if results and results[0]:
                all_results.extend(results[0])
        # Sort by distance ascending (smaller = more similar) and take top-k
        all_results.sort(key=lambda x: x[1])
        return all_results[:k]

    return search_fn


def template_matcher(
    state: AgentState,
    search_fn: Callable[[str, int], list[tuple[str, float]]] | None = None,
) -> dict:
    """Match requirements against template library.

    Args:
        state: AgentState with populated requirements
        search_fn: Optional search function (query, k) -> [(id, score)].
                   If None, returns empty match list.

    Returns:
        Dict with matched_templates and updated node_status
    """
    requirements = state.get("requirements", {})
    query = _build_query_text(requirements)

    if not query or search_fn is None:
        logger.warning("No query text or search function — returning empty matches")
        return {
            "matched_templates": [],
            "node_status": {
                **state.get("node_status", {}),
                "TemplateMatcher": NodeStatus.COMPLETED.value,
            },
        }

    # Search template index
    results = search_fn(query, k=5)

    # Filter by minimum similarity threshold
    # FAISS returns L2 distance — convert to cosine similarity approximation
    # For normalized vectors, cosine_sim ≈ 1 - L2²/2
    matched: list[str] = []
    for template_id, distance in results:
        # Convert L2 to cosine for normalized vectors
        similarity = max(0.0, 1.0 - (distance ** 2) / 2.0)
        if similarity >= MIN_SIMILARITY:
            matched.append(template_id)

    # Top-3 by similarity (already sorted by results order)
    matched = matched[:3]

    # Phase A3 fix: write selected_template_id so SectionGenerator can consume it.
    # Previously this field was defined in AgentState but never written, so the
    # entire template-matching step was dead computation (workflow-review §漏洞3).
    selected_id = matched[0] if matched else ""

    logger.info(
        f"TemplateMatcher: matched {len(matched)} templates, "
        f"selected={selected_id or 'none'} "
        f"(query: {query[:80]}...)"
    )

    return {
        "matched_templates": matched,
        "selected_template_id": selected_id,
        "node_status": {
            **state.get("node_status", {}),
            "TemplateMatcher": NodeStatus.COMPLETED.value,
        },
    }
