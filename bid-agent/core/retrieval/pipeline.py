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
from typing import Any


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
        metadata_store: dict[str, dict] | None = None,
    ):
        """Initialize pipeline.

        Args:
            embedder: Embedding provider for query vectorization
            indices: Dict of {index_name: VectorIndexManager}
            rrf_k: RRF constant
            metadata_store: Optional {doc_id: metadata_dict} used for the
                metadata-filtering step (§4.2 step 5). If omitted, results
                whose metadata is unavailable are passed through untouched.
        """
        self.embedder = embedder
        self.indices: dict[str, VectorIndexManager] = indices or {}
        self.rrf_k = rrf_k
        self.metadata_store: dict[str, dict] = metadata_store or {}

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

        # Metadata filtering (§4.2 step 5) — filter fused results by doc metadata.
        # Each key in metadata_filter must be satisfied by the doc's stored
        # metadata: str→case-insensitive substring, otherwise exact equality.
        # Docs with no available metadata are passed through (cannot be judged).
        if metadata_filter:
            filtered: list[tuple[str, float]] = []
            for doc_id, score in fused:
                meta = self.metadata_store.get(doc_id)
                if meta is None or not _matches_metadata(meta, metadata_filter):
                    # No metadata available → keep (safe default); otherwise drop
                    # only when it explicitly fails the match.
                    if meta is None:
                        filtered.append((doc_id, score))
                    continue
                filtered.append((doc_id, score))
            logger.info(
                f"MetadataFilter: {len(filtered)}/{len(fused)} docs passed "
                f"filter {metadata_filter}"
            )
            fused = filtered

        return fused


def _matches_metadata(meta: dict, metadata_filter: dict[str, Any]) -> bool:
    """Return True iff every key in metadata_filter is satisfied by meta.

    String expected values match case-insensitively by substring containment;
    non-string expected values must equal the actual value exactly.
    """
    for key, expected in metadata_filter.items():
        actual = meta.get(key)
        if actual is None:
            return False
        if isinstance(expected, str) and isinstance(actual, str):
            if expected.lower() not in actual.lower():
                return False
        elif expected != actual:
            return False
    return True


def create_pipeline(
    embedder: BaseEmbedder,
    indices: dict[str, VectorIndexManager] | None = None,
) -> RetrievalPipeline:
    """Factory: create a RetrievalPipeline."""
    return RetrievalPipeline(embedder=embedder, indices=indices)
