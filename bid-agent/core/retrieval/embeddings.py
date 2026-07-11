"""Embedding interface — unified API for text-to-vector conversion.

Supports: OpenAI API + local model placeholder.
"""

import logging
from abc import ABC, abstractmethod
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)


class BaseEmbedder(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def dim(self) -> int:
        """Return the vector dimension."""
        ...

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Convert a list of texts to a (N, dim) numpy array.

        Args:
            texts: List of text strings

        Returns:
            numpy array of shape (len(texts), dim), dtype float32
        """
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query text. Returns (dim,) array."""
        return self.embed([text])[0]


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI embedding provider (text-embedding-ada-002, text-embedding-3-small/large)."""

    # Known models and their dimensions
    MODEL_DIMS = {
        "text-embedding-ada-002": 1536,
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
    ):
        """Initialize OpenAI embedder.

        Args:
            api_key: OpenAI API key. If None, reads from OPENAI_API_KEY env var.
            model: Model name
            base_url: Optional alternative API base URL (for proxies / compatible APIs)
        """
        self.model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url
        self._client = None

    def _get_client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
        return self._client

    def dim(self) -> int:
        return self.MODEL_DIMS.get(self.model, 1536)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Call OpenAI embeddings API."""
        client = self._get_client()
        response = client.embeddings.create(model=self.model, input=list(texts))
        vectors = [item.embedding for item in response.data]
        return np.array(vectors, dtype=np.float32)


class MockEmbedder(BaseEmbedder):
    """Mock embedder for testing — returns random unit vectors."""

    def __init__(self, dim: int = 1536, seed: int = 42):
        self._dim = dim
        self._rng = np.random.RandomState(seed)

    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        n = len(texts)
        vectors = self._rng.randn(n, self._dim).astype(np.float32)
        # Normalize to unit vectors
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / norms


# ── Factory ────────────────────────────────────────────────────────────

import os


def get_embedder(
    provider: str = "openai",
    model: str = "text-embedding-3-small",
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs,
) -> BaseEmbedder:
    """Create an embedder instance.

    Args:
        provider: "openai" or "mock"
        model: Model name (openai)
        api_key: API key
        base_url: API base URL
        **kwargs: Provider-specific options (e.g. dim for mock)

    Returns:
        A BaseEmbedder instance
    """
    if provider == "mock":
        return MockEmbedder(dim=kwargs.get("dim", 1536))
    elif provider == "openai":
        return OpenAIEmbedder(model=model, api_key=api_key, base_url=base_url)
    else:
        raise ValueError(f"Unknown embedder provider: {provider}")
