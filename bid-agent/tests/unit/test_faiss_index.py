"""Unit tests for FAISS index manager."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

from core.retrieval.faiss_index import (
    DEFAULT_DIM,
    VectorIndexManager,
    create_index,
    load_index,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def empty_index():
    """Create an empty vector index."""
    return VectorIndexManager(dim=16)


@pytest.fixture
def index_with_data():
    """Create an index with 50 vectors."""
    mgr = VectorIndexManager(dim=16)
    n = 50
    vectors = np.random.randn(n, 16).astype(np.float32)
    ids = [f"doc_{i:03d}" for i in range(n)]
    mgr.add(ids, vectors)
    return mgr


# ── Initialization Tests ───────────────────────────────────────────────


class TestInitialization:
    def test_create_with_default_dim(self):
        mgr = VectorIndexManager()
        assert mgr.dim == DEFAULT_DIM
        assert mgr.size == 0

    def test_create_with_custom_dim(self):
        mgr = VectorIndexManager(dim=128)
        assert mgr.dim == 128

    def test_create_with_custom_m(self):
        mgr = VectorIndexManager(m=64)
        assert mgr.m == 64

    def test_empty_index_size_zero(self, empty_index):
        assert empty_index.size == 0
        assert empty_index.ids == []


# ── Add Tests ──────────────────────────────────────────────────────────


class TestAdd:
    def test_add_single_vector(self, empty_index):
        vec = np.random.randn(16).astype(np.float32)
        empty_index.add(["v1"], vec)
        assert empty_index.size == 1
        assert empty_index.ids == ["v1"]

    def test_add_multiple_vectors(self, empty_index):
        vecs = np.random.randn(5, 16).astype(np.float32)
        ids = ["a", "b", "c", "d", "e"]
        empty_index.add(ids, vecs)
        assert empty_index.size == 5
        assert empty_index.ids == ids

    def test_add_1d_vector_reshaped(self, empty_index):
        vec = np.random.randn(16).astype(np.float32)
        empty_index.add(["x"], vec)
        assert empty_index.size == 1

    def test_dimension_mismatch_raises(self, empty_index):
        vec = np.random.randn(1, 32).astype(np.float32)
        with pytest.raises(ValueError, match="dim"):
            empty_index.add(["bad"], vec)

    def test_id_length_mismatch_raises(self, empty_index):
        vecs = np.random.randn(5, 16).astype(np.float32)
        with pytest.raises(ValueError, match="length"):
            empty_index.add(["a", "b"], vecs)

    def test_add_50_vectors(self, empty_index):
        n = 50
        vecs = np.random.randn(n, 16).astype(np.float32)
        ids = [f"t_{i}" for i in range(n)]
        empty_index.add(ids, vecs)
        assert empty_index.size == 50


# ── Search Tests ───────────────────────────────────────────────────────


class TestSearch:
    def test_search_empty_index(self, empty_index):
        query = np.random.randn(1, 16).astype(np.float32)
        results = empty_index.search(query, k=3)
        assert len(results) == 1
        assert results[0] == []

    def test_search_returns_top_k(self, index_with_data):
        query = np.random.randn(1, 16).astype(np.float32)
        results = index_with_data.search(query, k=3)
        assert len(results) == 1
        assert len(results[0]) == 3
        # Each result is (id, distance)
        for vid, dist in results[0]:
            assert vid.startswith("doc_")
            assert isinstance(dist, float)

    def test_search_results_sorted_by_similarity(self, index_with_data):
        query = np.random.randn(1, 16).astype(np.float32)
        results = index_with_data.search(query, k=5)
        distances = [d for _, d in results[0]]
        # FAISS returns L2 distance (ascending = more similar)
        assert distances == sorted(distances)

    def test_search_batch_queries(self, index_with_data):
        queries = np.random.randn(3, 16).astype(np.float32)
        results = index_with_data.search(queries, k=2)
        assert len(results) == 3
        for r in results:
            assert len(r) == 2

    def test_search_self_returns_zero_distance(self, index_with_data):
        """Searching with the first stored vector should return itself first."""
        # Reconstruct via numpy (works regardless of backend)
        known = np.zeros(16, dtype=np.float32)
        if index_with_data._vectors is not None:
            known[:] = index_with_data._vectors[0]

        results = index_with_data.search(known.reshape(1, -1), k=1)
        assert results[0][0][0] == "doc_000"
        assert abs(results[0][0][1]) < 1e-4


# ── Remove Tests ───────────────────────────────────────────────────────


class TestRemove:
    def test_remove_single(self, index_with_data):
        index_with_data.remove(["doc_000"])
        assert index_with_data.size == 49
        assert "doc_000" not in index_with_data.ids

    def test_remove_multiple(self, index_with_data):
        index_with_data.remove(["doc_000", "doc_001", "doc_002"])
        assert index_with_data.size == 47

    def test_remove_all(self, index_with_data):
        all_ids = list(index_with_data.ids)
        index_with_data.remove(all_ids)
        assert index_with_data.size == 0

    def test_remove_nonexistent(self, index_with_data):
        index_with_data.remove(["nonexistent"])
        assert index_with_data.size == 50  # unchanged

    def test_clear(self, index_with_data):
        index_with_data.clear()
        assert index_with_data.size == 0
        assert index_with_data.ids == []


# ── Persistence Tests ──────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load(self, index_with_data):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.faiss"
            index_with_data.save(path)

            # np.savez appends .npz
            saved_path = path.with_suffix(".npz")
            assert saved_path.exists()

            loaded = VectorIndexManager.load(path)
            assert loaded.size == index_with_data.size
            assert loaded.ids == index_with_data.ids

    def test_load_nonexistent(self):
        with pytest.raises(Exception):
            VectorIndexManager.load("/nonexistent/path.faiss")

    def test_ids_included_in_save(self):
        """IDs are embedded in the .npz file, so no separate .ids file needed."""
        index = VectorIndexManager(dim=16)
        index.add(["a", "b"], np.random.randn(2, 16).astype(np.float32))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.faiss"
            index.save(path)
            loaded = VectorIndexManager.load(path)
            assert loaded.ids == ["a", "b"]

    def test_roundtrip_preserves_search(self):
        """Save, load, then verify search results match."""
        n = 10
        mgr = VectorIndexManager(dim=8)
        vecs = np.random.randn(n, 8).astype(np.float32)
        ids = [f"item_{i}" for i in range(n)]
        mgr.add(ids, vecs)

        query = np.random.randn(1, 8).astype(np.float32)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "roundtrip.faiss"
            mgr.save(path)
            loaded = VectorIndexManager.load(path)

        original = mgr.search(query, k=3)[0]
        restored = loaded.search(query, k=3)[0]
        assert original == restored


# ── Convenience Tests ──────────────────────────────────────────────────


class TestConvenience:
    def test_create_index(self):
        mgr = create_index(dim=1024)
        assert isinstance(mgr, VectorIndexManager)
        assert mgr.dim == 1024

    def test_load_index_roundtrip(self):
        mgr = create_index(dim=16)
        mgr.add(["x"], np.random.randn(1, 16).astype(np.float32))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "conv.faiss"
            mgr.save(path)
            loaded = load_index(path)
            assert loaded.size == 1
            assert loaded.ids == ["x"]


# ── T027: Template Library Index Test ──────────────────────────────────


class TestAllTypesIndexed:
    def test_all_types_have_min_3_templates(self):
        """T027: 7 types, each with at least 3 templates."""
        from core.retrieval.template_library import (
            TEMPLATES,
            check_all_types_indexed,
            get_template_count,
            get_template_types,
        )

        types = get_template_types()
        assert len(types) == 7, f"Expected 7 types, got {len(types)}"

        for bid_type in types:
            count = get_template_count(bid_type)
            assert count >= 3, f"{bid_type} has only {count} templates"

        assert check_all_types_indexed() is True

    def test_template_indices_buildable(self):
        """All 7 indices can be built and searched."""
        from core.retrieval.template_library import build_template_index

        indices = build_template_index()
        assert len(indices) == 7

        for bid_type, mgr in indices.items():
            assert mgr.size >= 3, f"{bid_type} index has only {mgr.size} vectors"
