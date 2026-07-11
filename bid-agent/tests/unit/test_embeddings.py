"""Unit tests for Embedding interface."""

import numpy as np
import pytest

from core.retrieval.embeddings import (
    MockEmbedder,
    OpenAIEmbedder,
    get_embedder,
)


# ── Mock Embedder Tests ────────────────────────────────────────────────


class TestMockEmbedder:
    def test_dimension(self):
        embedder = MockEmbedder(dim=1536)
        assert embedder.dim() == 1536

    def test_custom_dimension(self):
        embedder = MockEmbedder(dim=768)
        assert embedder.dim() == 768

    def test_embed_single_text(self):
        embedder = MockEmbedder(dim=128)
        result = embedder.embed(["hello"])
        assert result.shape == (1, 128)
        assert result.dtype == np.float32

    def test_embed_multiple_texts(self):
        embedder = MockEmbedder(dim=128)
        result = embedder.embed(["a", "b", "c"])
        assert result.shape == (3, 128)

    def test_embed_unit_vectors(self):
        """Mock embedder returns unit vectors."""
        embedder = MockEmbedder(dim=128)
        result = embedder.embed(["test", "vectors"])
        norms = np.linalg.norm(result, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-5)

    def test_deterministic(self):
        """Same seed should produce same vectors."""
        e1 = MockEmbedder(dim=64, seed=42)
        e2 = MockEmbedder(dim=64, seed=42)
        v1 = e1.embed(["text"])
        v2 = e2.embed(["text"])
        assert np.array_equal(v1, v2)

    def test_embed_query(self):
        embedder = MockEmbedder(dim=256)
        vec = embedder.embed_query("query text")
        assert vec.shape == (256,)
        assert vec.dtype == np.float32


# ── Factory Tests ──────────────────────────────────────────────────────


class TestGetEmbedder:
    def test_get_mock(self):
        embedder = get_embedder(provider="mock", dim=768)
        assert isinstance(embedder, MockEmbedder)
        assert embedder.dim() == 768

    def test_get_mock_default_dim(self):
        embedder = get_embedder(provider="mock")
        assert embedder.dim() == 1536

    def test_get_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown"):
            get_embedder(provider="nonexistent")


# ── OpenAI Embedder Tests ──────────────────────────────────────────────


class TestOpenAIEmbedder:
    def test_dimension_from_model(self):
        e = OpenAIEmbedder(model="text-embedding-3-small")
        assert e.dim() == 1536

    def test_dimension_large_model(self):
        e = OpenAIEmbedder(model="text-embedding-3-large")
        assert e.dim() == 3072

    def test_dimension_ada_002(self):
        e = OpenAIEmbedder(model="text-embedding-ada-002")
        assert e.dim() == 1536

    def test_dimension_unknown_model(self):
        e = OpenAIEmbedder(model="unknown-model")
        assert e.dim() == 1536  # default fallback

    def test_custom_base_url(self):
        e = OpenAIEmbedder(base_url="https://custom.api.com/v1")
        assert e._base_url == "https://custom.api.com/v1"
