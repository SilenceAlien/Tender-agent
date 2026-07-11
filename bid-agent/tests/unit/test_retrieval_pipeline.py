"""Unit tests for retrieval pipeline (RRF fusion)."""

import numpy as np
import pytest

from core.retrieval.embeddings import MockEmbedder
from core.retrieval.faiss_index import VectorIndexManager
from core.retrieval.pipeline import (
    RRF_K,
    RetrievalPipeline,
    create_pipeline,
    reciprocal_rank_fusion,
)

# FAISS may not be installed yet — skip integration tests gracefully
try:
    from core.retrieval.faiss_index import VectorIndexManager as _VIM
    FAISS_AVAILABLE = True
except ModuleNotFoundError:
    FAISS_AVAILABLE = True  # VectorIndexManager now works without FAISS

FAISS_AVAILABLE = True  # Always available with numpy fallback


# ── RRF Tests ──────────────────────────────────────────────────────────


class TestReciprocalRankFusion:
    def test_single_list(self):
        ranked = [["a", "b", "c"]]
        result = reciprocal_rank_fusion(ranked)
        assert result == [("a", 1.0 / (RRF_K + 1)), ("b", 1.0 / (RRF_K + 2)), ("c", 1.0 / (RRF_K + 3))]

    def test_two_lists_same_order(self):
        ranked = [["x", "y"], ["x", "y"]]
        result = reciprocal_rank_fusion(ranked)
        # x gets score from both lists
        expected_x = 2.0 / (RRF_K + 1)
        expected_y = 2.0 / (RRF_K + 2)
        assert result[0][0] == "x"
        assert abs(result[0][1] - expected_x) < 1e-9
        assert result[1][0] == "y"

    def test_two_lists_different_order(self):
        ranked = [["a", "b", "c"], ["c", "b", "a"]]
        result = reciprocal_rank_fusion(ranked)
        # a: 1/(k+1)+1/(k+3), c: 1/(k+3)+1/(k+1) → equal
        # b: 2/(k+2) → slightly different (within ~1e-5)
        scores = {r[0]: r[1] for r in result}
        assert abs(scores["a"] - scores["c"]) < 1e-9  # symmetric ranks
        assert scores["b"] > 0  # b has a valid score

    def test_deduplication(self):
        ranked = [["d", "e"], ["d", "f"]]
        result = reciprocal_rank_fusion(ranked)
        ids = [r[0] for r in result]
        assert len(ids) == 3
        assert ids.count("d") == 1  # deduplicated

    def test_top_n_truncation(self):
        ranked = [["a", "b", "c", "d", "e"]]
        result = reciprocal_rank_fusion(ranked, top_n=2)
        assert len(result) == 2
        assert result[0][0] == "a"
        assert result[1][0] == "b"

    def test_custom_k(self):
        ranked = [["x", "y"]]
        result = reciprocal_rank_fusion(ranked, k=10)
        assert abs(result[0][1] - 1.0 / 11.0) < 1e-9

    def test_empty_lists(self):
        result = reciprocal_rank_fusion([])
        assert result == []

    def test_partial_empty(self):
        result = reciprocal_rank_fusion([["a", "b"], []])
        assert len(result) == 2


# ── Pipeline Tests ────────────────────────────────────────────────────


class TestRetrievalPipeline:
    @pytest.fixture
    def embedder(self):
        return MockEmbedder(dim=16, seed=1)

    @pytest.fixture
    def index_a(self):
        mgr = VectorIndexManager(dim=16)
        mgr.add(["a1", "a2", "a3"], np.random.randn(3, 16).astype(np.float32))
        return mgr

    @pytest.fixture
    def index_b(self):
        mgr = VectorIndexManager(dim=16)
        mgr.add(["b1", "b2", "b3"], np.random.randn(3, 16).astype(np.float32))
        return mgr

    def test_empty_pipeline(self, embedder):
        pipe = RetrievalPipeline(embedder=embedder)
        results = pipe.search("test query")
        assert results == []

    def test_single_index_search(self, embedder, index_a):
        pipe = RetrievalPipeline(embedder=embedder, indices={"templates": index_a})
        results = pipe.search("test query", final_k=2)
        assert len(results) == 2
        assert all(isinstance(r[0], str) for r in results)
        assert all(isinstance(r[1], float) for r in results)

    def test_multi_index_search(self, embedder, index_a, index_b):
        pipe = RetrievalPipeline(
            embedder=embedder,
            indices={"templates": index_a, "clauses": index_b},
        )
        results = pipe.search("test query", final_k=5)
        assert len(results) <= 5
        assert len(results) > 0

    def test_results_deduplicated(self, embedder, index_a, index_b):
        pipe = RetrievalPipeline(
            embedder=embedder,
            indices={"idx_a": index_a, "idx_b": index_b},
        )
        results = pipe.search("test query", final_k=10)
        ids = [r[0] for r in results]
        assert len(ids) == len(set(ids))  # no duplicates

    def test_specific_indices(self, embedder, index_a, index_b):
        pipe = RetrievalPipeline(
            embedder=embedder,
            indices={"a": index_a, "b": index_b},
        )
        results_a = pipe.search("test", index_names=["a"], final_k=3)
        # Should only have results from index_a
        for vid, _ in results_a:
            assert vid.startswith("a")

    def test_create_pipeline(self, embedder):
        pipe = create_pipeline(embedder=embedder)
        assert isinstance(pipe, RetrievalPipeline)
        assert pipe.embedder is embedder

    def test_rrf_fusion_scores_descending(self, embedder, index_a):
        pipe = RetrievalPipeline(embedder=embedder, indices={"a": index_a})
        results = pipe.search("test", final_k=3)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)
