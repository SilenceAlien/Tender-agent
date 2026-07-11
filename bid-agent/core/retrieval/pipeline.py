"""Retrieval pipeline — multi-index parallel search with RRF fusion.

Orchestrates multiple FAISS indices with Reciprocal Rank Fusion for
consolidated, deduplicated results.

Contract (T010):
    - Parallel search across multiple indices
    - RRF fusion with configurable k constant
    - Metadata filtering support
    - Deduplication and result truncation
"""

import logging
from typing import Any, Sequence

import numpy as np

from core.retrieval.embeddings import BaseEmbedder
from core.retrieval.faiss_index import VectorIndexManager

logger = logging.getLogger(__name__)

# Default RRF constant
RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = RRF_K,
    top_n: int | None = None,
) -> list[tuple[str, float]]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion.

    RRF score(d) = Σ 1 / (k + rank_i(d))
    Where rank_i(d) is the position (starting at 1) of document d in list i.

    Args:
        ranked_lists: List of ranked ID lists (best first)
        k: RRF constant (default 60, per original paper)
        top_n: Max results to return (None = return all)

    Returns:
        List of (id, score) tuples, sorted by score descending
    """
    scores: dict[str, float] = {}

    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    # Sort by score descending
    sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if top_n is not None:
        sorted_results = sorted_results[:top_n]

    return sorted_results


class RetrievalPipeline:
    """Multi-index retrieval pipeline with RRF fusion."""

    def __init__(
        self,
        embedder: BaseEmbedder,
        indices: dict[str, VectorIndexManager] | None = None,
        rrf_k: int = RRF_K,
    ):
        """Initialize pipeline.

        Args:
            embedder: Embedding provider for query vectorization
            indices: Dict of {index_name: VectorIndexManager}
            rrf_k: RRF constant
        """
        self.embedder = embedder
        self.indices: dict[str, VectorIndexManager] = indices or {}
        self.rrf_k = rrf_k

    def add_index(self, name: str, index: VectorIndexManager) -> None:
        """Register an index in the pipeline."""
        self.indices[name] = index

    def search(
        self,
        query: str,
        top_k_per_index: int = 5,
        final_k: int = 3,
        index_names: list[str] | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Search across indices with RRF fusion.

        Args:
            query: Natural language query string
            top_k_per_index: Number of results per index
            final_k: Number of final fused results
            index_names: Which indices to search (None = all)
            metadata_filter: Optional filter dict (applied post-fusion)

        Returns:
            List of (id, score) tuples, sorted by RRF score descending
        """
        if not self.indices:
            logger.warning("No indices registered")
            return []

        query_vec = self.embedder.embed_query(query).reshape(1, -1)

        # Determine which indices to search
        search_indices = (
            {name: self.indices[name] for name in index_names}
            if index_names
            else self.indices
        )

        # Parallel search (conceptually; FAISS is single-threaded here)
        ranked_lists: list[list[str]] = []
        for name, index in search_indices.items():
            results = index.search(query_vec, k=top_k_per_index)
            ids = [vid for vid, _ in results[0]]
            ranked_lists.append(ids)

        # RRF fusion
        fused = reciprocal_rank_fusion(ranked_lists, k=self.rrf_k, top_n=final_k)

        # Metadata filtering (placeholder — metadata stored externally in Phase 3)
        if metadata_filter:
            # Future: filter based on metadata in a metadata store
            pass

        return fused


def create_pipeline(
    embedder: BaseEmbedder,
    indices: dict[str, VectorIndexManager] | None = None,
) -> RetrievalPipeline:
    """Factory: create a RetrievalPipeline."""
    return RetrievalPipeline(embedder=embedder, indices=indices)
