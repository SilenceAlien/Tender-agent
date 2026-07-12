"""Vector index manager — HNSW index for template matching and RAG retrieval.

Primary: FAISS HNSWFlat (if installed).
Fallback: Pure numpy brute-force (zero deps).
"""

import logging
from pathlib import Path
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_DIM = 1536
DEFAULT_M = 32

# ── Backend detection ──────────────────────────────────────────────────

try:
    import faiss
    _HAS_FAISS = True
except ModuleNotFoundError:
    _HAS_FAISS = False
    logger.warning("FAISS not installed — using numpy brute-force fallback")


# ── Manager ────────────────────────────────────────────────────────────


class VectorIndexManager:
    """Manages a vector index for nearest-neighbor search.

    Uses FAISS HNSW if available, otherwise pure numpy brute-force.

    Usage:
        >>> mgr = VectorIndexManager(dim=1536)
        >>> mgr.add(ids=["t1", "t2"], vectors=np.random.rand(2, 1536).astype("float32"))
        >>> results = mgr.search(np.random.rand(1, 1536).astype("float32"), k=3)
    """

    def __init__(self, dim: int = DEFAULT_DIM, m: int = DEFAULT_M):
        self.dim = dim
        self.m = m
        self._ids: list[str] = []
        self._vectors: np.ndarray | None = None

        if _HAS_FAISS:
            self._faiss_index = self._create_faiss_index()
        else:
            self._faiss_index = None

    def _create_faiss_index(self):
        index = faiss.IndexHNSWFlat(self.dim, self.m)
        index.hnsw.efConstruction = 200
        return index

    @property
    def size(self) -> int:
        return len(self._ids)

    @property
    def ids(self) -> list[str]:
        return list(self._ids)

    # ── CRUD ──────────────────────────────────────────────────────────

    def add(self, ids: Sequence[str], vectors: np.ndarray) -> None:
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)

        if len(ids) != vectors.shape[0]:
            raise ValueError(f"ids length ({len(ids)}) != vectors rows ({vectors.shape[0]})")

        if vectors.shape[1] != self.dim:
            raise ValueError(f"Vector dim {vectors.shape[1]} != index dim {self.dim}")

        if _HAS_FAISS and self._faiss_index is not None:
            self._faiss_index.add(vectors)
            # BUG-04 fix: keep a numpy copy in sync even in FAISS mode so that
            # save() persists real vectors and load() can rebuild the index.
            if self._vectors is None:
                self._vectors = vectors.copy()
            else:
                self._vectors = np.vstack([self._vectors, vectors])
        else:
            if self._vectors is None:
                self._vectors = vectors
            else:
                self._vectors = np.vstack([self._vectors, vectors])

        self._ids.extend(ids)
        logger.info(f"Added {len(ids)} vectors. Total: {self.size}")

    def remove(self, ids_to_remove: Sequence[str]) -> None:
        remove_set = set(ids_to_remove)
        keep_indices = [i for i, vid in enumerate(self._ids) if vid not in remove_set]

        if len(keep_indices) == len(self._ids):
            logger.warning("No matching IDs to remove")
            return

        if not keep_indices:
            self.clear()
            return

        if _HAS_FAISS and self._faiss_index is not None:
            # BUG-11 fix: HNSW index doesn't support reconstruct() without
            # make_direct_map(). Since BUG-04 fix keeps self._vectors in sync,
            # we can use the numpy copy directly instead of reconstruct().
            if self._vectors is not None:
                new_vectors = self._vectors[keep_indices]
            else:
                # Fallback: shouldn't happen after BUG-04 fix, but guard anyway
                new_vectors = np.zeros((len(keep_indices), self.dim), dtype=np.float32)
                for new_idx, old_idx in enumerate(keep_indices):
                    self._faiss_index.reconstruct(old_idx, new_vectors[new_idx])
            self._faiss_index = self._create_faiss_index()
            self._faiss_index.add(new_vectors)
            self._vectors = new_vectors
        else:
            self._vectors = self._vectors[keep_indices]

        self._ids = [self._ids[i] for i in keep_indices]

    def clear(self) -> None:
        if _HAS_FAISS and self._faiss_index is not None:
            self._faiss_index = self._create_faiss_index()
        self._vectors = None
        self._ids = []

    # ── Search ─────────────────────────────────────────────────────────

    def _brute_force_search(self, query: np.ndarray, k: int) -> list[list[tuple[str, float]]]:
        """Pure numpy L2 search."""
        if self._vectors is None or len(self._vectors) == 0:
            return [[] for _ in range(len(query))]

        # Compute pairwise L2 distances
        # dist[i,j] = ||Q[i] - V[j]||^2
        # = Q[i]·Q[i] + V[j]·V[j] - 2Q[i]·V[j]
        q_norms = np.sum(query ** 2, axis=1, keepdims=True)  # (N, 1)
        v_norms = np.sum(self._vectors ** 2, axis=1)          # (M,)
        dots = query @ self._vectors.T                         # (N, M)
        dists = q_norms + v_norms - 2 * dots
        dists = np.maximum(dists, 0.0)  # clip negative due to float imprecision

        # Get top-k smallest distances per query
        effective_k = min(k, len(self._ids))
        top_indices = np.argpartition(dists, effective_k - 1, axis=1)[:, :effective_k]
        top_dists = np.take_along_axis(dists, top_indices, axis=1)

        # Sort within each row
        sort_order = np.argsort(top_dists, axis=1)
        top_indices = np.take_along_axis(top_indices, sort_order, axis=1)
        top_dists = np.take_along_axis(top_dists, sort_order, axis=1)

        results: list[list[tuple[str, float]]] = []
        for i in range(len(query)):
            row: list[tuple[str, float]] = []
            for j in range(effective_k):
                idx = int(top_indices[i, j])
                row.append((self._ids[idx], float(top_dists[i, j])))
            results.append(row)

        return results

    def search(self, query_vectors: np.ndarray, k: int = 3) -> list[list[tuple[str, float]]]:
        query_vectors = np.asarray(query_vectors, dtype=np.float32)
        if query_vectors.ndim == 1:
            query_vectors = query_vectors.reshape(1, -1)

        if not self._ids:
            return [[] for _ in range(len(query_vectors))]

        if _HAS_FAISS and self._faiss_index is not None:
            distances, indices = self._faiss_index.search(query_vectors, k)
            results: list[list[tuple[str, float]]] = []
            for i in range(len(query_vectors)):
                row: list[tuple[str, float]] = []
                for j in range(k):
                    idx = indices[i][j]
                    if idx == -1:
                        break
                    row.append((self._ids[idx], float(distances[i][j])))
                results.append(row)
            return results

        return self._brute_force_search(query_vectors, k)

    # ── Persistence ────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Normalize to .npz extension
        if path.suffix != ".npz":
            path = path.with_suffix(".npz")

        data = {
            "ids": np.array(self._ids, dtype=object),
            "dim": self.dim,
        }
        if self._vectors is not None:
            np.savez_compressed(path, vectors=self._vectors, **data)
        else:
            np.savez_compressed(path, vectors=np.zeros((0, self.dim), dtype=np.float32), **data)

        logger.info(f"Saved index ({self.size} vectors) to {path}")

    @classmethod
    def load(cls, path: str | Path, dim: int | None = None) -> "VectorIndexManager":
        path = Path(path)
        # Handle both .faiss (FAISS convention) and .npz paths
        if path.suffix == ".faiss":
            path = path.with_suffix(".npz")
        elif path.suffix != ".npz":
            path = path.with_suffix(".npz")

        data = np.load(path, allow_pickle=True)
        vectors = data["vectors"]
        stored_dim = int(data["dim"])
        ids = list(data["ids"])

        mgr = cls(dim=dim or stored_dim)
        mgr._ids = ids
        if len(vectors) > 0:
            mgr._vectors = vectors.astype(np.float32)
            # BUG-04 fix: rebuild FAISS index from loaded vectors so that
            # search works after save → load.
            if _HAS_FAISS and mgr._faiss_index is not None:
                mgr._faiss_index.add(mgr._vectors)

        logger.info(f"Loaded index ({len(ids)} vectors) from {path}")
        return mgr


# ── Convenience ────────────────────────────────────────────────────────


def create_index(dim: int = DEFAULT_DIM) -> VectorIndexManager:
    return VectorIndexManager(dim=dim)


def load_index(path: str | Path, dim: int | None = None) -> VectorIndexManager:
    return VectorIndexManager.load(path, dim=dim)
