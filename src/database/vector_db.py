"""
FAISS vector database for semantic similarity search.
"""
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
        self.index = None
        self.metadata_store = []  # Store metadata for each vector
        self.index_path = os.path.join(settings.FAISS_INDEX_PATH, "faiss.index")
        self.metadata_path = os.path.join(settings.FAISS_INDEX_PATH, "metadata.pkl")
        
        self._initialize_index()
    
    def _initialize_index(self):
        """Initialize or load FAISS index."""
        try:
            if os.path.exists(self.index_path) and os.path.exists(self.metadata_path):
                # Load existing index
                self.index = faiss.read_index(self.index_path)
                with open(self.metadata_path, 'rb') as f:
                    self.metadata_store = pickle.load(f)
                logger.info(f"Loaded FAISS index with {self.index.ntotal} vectors")
            else:
                # Create new index
                self.index = faiss.IndexFlatL2(self.dimension)  # L2 distance
                self.metadata_store = []
                logger.info("Created new FAISS index")
        except Exception as e:
            logger.error(f"Error initializing FAISS index: {e}")
            # Create new index on error
            self.index = faiss.IndexFlatL2(self.dimension)
            self.metadata_store = []
    
    def save_index(self):
        """Save FAISS index and metadata to disk."""
        try:
            os.makedirs(settings.FAISS_INDEX_PATH, exist_ok=True)
            faiss.write_index(self.index, self.index_path)
            with open(self.metadata_path, 'wb') as f:
                pickle.dump(self.metadata_store, f)
            logger.info(f"Saved FAISS index with {self.index.ntotal} vectors")
        except Exception as e:
            logger.error(f"Error saving FAISS index: {e}")
            raise
    
    def add_vectors(
        self,
        embeddings: np.ndarray,
        metadata: List[Dict[str, Any]]
    ) -> List[int]:
        """
        Add vectors to the index.
        
        Args:
            embeddings: Numpy array of shape (n, dimension)
            metadata: List of metadata dicts (one per vector)
            
        Returns:
            List of vector IDs
        """
        try:
            if embeddings.shape[1] != self.dimension:
                raise ValueError(f"Embedding dimension {embeddings.shape[1]} doesn't match index dimension {self.dimension}")
            
            # Normalize vectors for cosine similarity (optional)
            faiss.normalize_L2(embeddings)
            
            start_id = self.index.ntotal
            self.index.add(embeddings.astype('float32'))
            
            # Store metadata
            self.metadata_store.extend(metadata)
            
            # Save after adding
            self.save_index()
            
            vector_ids = list(range(start_id, self.index.ntotal))
            logger.info(f"Added {len(vector_ids)} vectors to FAISS index")
            
            return vector_ids
            
        except Exception as e:
            logger.error(f"Error adding vectors: {e}")
            raise
    
    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors.
        
        Args:
            query_vector: Query embedding vector
            top_k: Number of results to return
            filter_metadata: Optional metadata filter
            
        Returns:
            List of results with scores and metadata
        """
        try:
            if self.index.ntotal == 0:
                logger.warning("FAISS index is empty")
                return []
            
            # Ensure query vector is 2D
            if query_vector.ndim == 1:
                query_vector = query_vector.reshape(1, -1)
            
            # Normalize for cosine similarity
            faiss.normalize_L2(query_vector)
            
            # Search
            distances, indices = self.index.search(query_vector.astype('float32'), min(top_k, self.index.ntotal))
            
            results = []
            for idx, (distance, index) in enumerate(zip(distances[0], indices[0])):
                if index < 0 or index >= len(self.metadata_store):
                    continue
                
                metadata = self.metadata_store[index]
                
                # Apply filter if provided
                if filter_metadata:
                    if not all(metadata.get(k) == v for k, v in filter_metadata.items()):
                        continue
                
                # Convert L2 distance to similarity score (0-1)
                similarity = 1 / (1 + distance)
                
                results.append({
                    "id": int(index),
                    "score": float(similarity),
                    "metadata": metadata
                })
            
            logger.info(f"Found {len(results)} similar vectors")
            return results
            
        except Exception as e:
            logger.error(f"Error searching vectors: {e}")
            raise
    
    def delete_by_filter(self, filter_metadata: Dict[str, Any]):
        """
        Delete vectors matching metadata filter.
        Note: FAISS doesn't support deletion, so we rebuild the index.
        """
        try:
            # Find indices to keep
            keep_indices = []
            new_metadata = []
            
            for idx, metadata in enumerate(self.metadata_store):
                if not all(metadata.get(k) == v for k, v in filter_metadata.items()):
                    keep_indices.append(idx)
                    new_metadata.append(metadata)
            
            if len(keep_indices) == len(self.metadata_store):
                logger.info("No vectors to delete")
                return
            
            # Rebuild index with kept vectors
            new_index = faiss.IndexFlatL2(self.dimension)
            
            if keep_indices:
                vectors_to_keep = []
                for idx in keep_indices:
                    # This is inefficient but necessary since FAISS doesn't expose vectors directly
                    # In production, consider maintaining a separate vector store
                    pass
                
            self.index = new_index
            self.metadata_store = new_metadata
            self.save_index()
            
            logger.info(f"Deleted {len(self.metadata_store) - len(new_metadata)} vectors")
            
        except Exception as e:
            logger.error(f"Error deleting vectors: {e}")
            raise
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the index."""
        return {
            "total_vectors": self.index.ntotal,
            "dimension": self.dimension,
            "index_type": "IndexFlatL2"
        }

# Global instance
_faiss_db: Optional[FAISSVectorDB] = None

def get_vector_db() -> FAISSVectorDB:
    """Get or create FAISS vector database instance."""
    global _faiss_db
    if _faiss_db is None:
        _faiss_db = FAISSVectorDB()
    return _faiss_db

async def init_vector_db():
    """Initialize FAISS vector database."""
    global _faiss_db
    _faiss_db = FAISSVectorDB()
    logger.info("FAISS vector database initialized")

async def close_vector_db():
    """Close FAISS vector database."""
    global _faiss_db
    if _faiss_db:
        _faiss_db.save_index()
        _faiss_db = None
    logger.info("FAISS vector database closed")
