import faiss
import numpy as np
from typing import List, Dict, Any, Optional
import logging
import pickle
import os

from src.config import settings

logger = logging.getLogger(__name__)


class FAISSVectorDB:
    """Service class for FAISS vector database operations."""

    def __init__(self):
        self.dimension = settings.VECTOR_DIMENSION
        self.index: Optional[faiss.Index] = None
        self.metadata_store: List[Dict[str, Any]] = []
        # Shadow store keeps raw float32 vectors so deletion/rebuild works
        self.vector_store: List[np.ndarray] = []

        self.index_path = os.path.join(settings.FAISS_INDEX_PATH, "faiss.index")
        self.metadata_path = os.path.join(settings.FAISS_INDEX_PATH, "metadata.pkl")
        self.vectors_path = os.path.join(settings.FAISS_INDEX_PATH, "vectors.pkl")

        self._initialize_index()

    def _initialize_index(self):
        """Initialize or load FAISS index."""
        try:
            if (
                os.path.exists(self.index_path)
                and os.path.exists(self.metadata_path)
                and os.path.exists(self.vectors_path)
            ):
                self.index = faiss.read_index(self.index_path)
                with open(self.metadata_path, "rb") as f:
                    self.metadata_store = pickle.load(f)
                with open(self.vectors_path, "rb") as f:
                    self.vector_store = pickle.load(f)
                logger.info(f"Loaded FAISS index with {self.index.ntotal} vectors")
            else:
                self._create_fresh_index()
        except Exception as e:
            logger.error(f"Error initializing FAISS index: {e}. Creating fresh index.")
            self._create_fresh_index()

    def _create_fresh_index(self):
        self.index = faiss.IndexFlatL2(self.dimension)
        self.metadata_store = []
        self.vector_store = []
        logger.info("Created new FAISS index")

    def save_index(self):
        """Persist index, metadata, and shadow vector store to disk."""
        try:
            os.makedirs(settings.FAISS_INDEX_PATH, exist_ok=True)
            faiss.write_index(self.index, self.index_path)
            with open(self.metadata_path, "wb") as f:
                pickle.dump(self.metadata_store, f)
            with open(self.vectors_path, "wb") as f:
                pickle.dump(self.vector_store, f)
            logger.info(f"Saved FAISS index with {self.index.ntotal} vectors")
        except Exception as e:
            logger.error(f"Error saving FAISS index: {e}")
            raise

    def add_vectors(
        self,
        embeddings: np.ndarray,
        metadata: List[Dict[str, Any]],
    ) -> List[int]:
        """
        Add vectors to the index.

        Args:
            embeddings: Numpy array of shape (n, dimension)
            metadata:   One metadata dict per vector

        Returns:
            List of assigned vector IDs
        """
        if embeddings.ndim == 1:
            embeddings = embeddings.reshape(1, -1)

        if embeddings.shape[1] != self.dimension:
            raise ValueError(
                f"Embedding dimension {embeddings.shape[1]} "
                f"doesn't match index dimension {self.dimension}"
            )

        embeddings = embeddings.astype("float32")
        faiss.normalize_L2(embeddings)

        start_id = self.index.ntotal
        self.index.add(embeddings)

        # Store metadata and raw vectors for future deletion/rebuild
        self.metadata_store.extend(metadata)
        for vec in embeddings:
            self.vector_store.append(vec.copy())

        self.save_index()

        vector_ids = list(range(start_id, self.index.ntotal))
        logger.info(f"Added {len(vector_ids)} vectors to FAISS index")
        return vector_ids

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors.

        Args:
            query_vector:     Query embedding vector
            top_k:            Number of results to return
            filter_metadata:  Optional metadata filter (AND logic)

        Returns:
            List of results with scores and metadata
        """
        if self.index.ntotal == 0:
            logger.warning("FAISS index is empty")
            return []

        query_vector = np.array(query_vector, dtype="float32")
        if query_vector.ndim == 1:
            query_vector = query_vector.reshape(1, -1)

        faiss.normalize_L2(query_vector)

        search_k = min(top_k * 3, self.index.ntotal) if filter_metadata else min(top_k, self.index.ntotal)
        distances, indices = self.index.search(query_vector, search_k)

        results = []
        for distance, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata_store):
                continue

            meta = self.metadata_store[idx]

            if filter_metadata:
                if not all(meta.get(k) == v for k, v in filter_metadata.items()):
                    continue

            # Convert L2 distance to a 0-1 similarity score
            similarity = float(1.0 / (1.0 + distance))
            results.append({"id": int(idx), "score": similarity, "metadata": meta})

            if len(results) >= top_k:
                break

        logger.info(f"FAISS search returned {len(results)} results")
        return results

    def delete_by_filter(self, filter_metadata: Dict[str, Any]):
        """
        Delete all vectors matching the metadata filter and rebuild the index.

        FAISS does not support in-place deletion; we rebuild from the shadow store.
        """
        keep_indices = [
            i
            for i, meta in enumerate(self.metadata_store)
            if not all(meta.get(k) == v for k, v in filter_metadata.items())
        ]

        deleted = len(self.metadata_store) - len(keep_indices)
        if deleted == 0:
            logger.info("delete_by_filter: no matching vectors found")
            return

        new_index = faiss.IndexFlatL2(self.dimension)
        if keep_indices:
            kept_vecs = np.vstack([self.vector_store[i] for i in keep_indices]).astype("float32")
            new_index.add(kept_vecs)

        self.index = new_index
        self.metadata_store = [self.metadata_store[i] for i in keep_indices]
        self.vector_store = [self.vector_store[i] for i in keep_indices]
        self.save_index()

        logger.info(f"Deleted {deleted} vectors from FAISS index; {len(keep_indices)} remain")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_vectors": self.index.ntotal,
            "dimension": self.dimension,
            "index_type": "IndexFlatL2",
        }


# ── Global singleton ────────────────────────────────────────────────────────

_faiss_db: Optional[FAISSVectorDB] = None


def get_vector_db() -> FAISSVectorDB:
    global _faiss_db
    if _faiss_db is None:
        _faiss_db = FAISSVectorDB()
    return _faiss_db


async def init_vector_db():
    global _faiss_db
    _faiss_db = FAISSVectorDB()
    logger.info("FAISS vector database initialized")


async def close_vector_db():
    global _faiss_db
    if _faiss_db:
        _faiss_db.save_index()
        _faiss_db = None
    logger.info("FAISS vector database closed")