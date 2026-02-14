"""
Hybrid retrieval service combining vector search (FAISS) and knowledge graphs (Neo4j).
Implements Reciprocal Rank Fusion for optimal multi-source retrieval.
"""
import numpy as np
from typing import List, Dict, Any, Optional
import logging

from src.database.vector_db import get_vector_db
from src.database.neo4j_db import Neo4jService, get_neo4j_driver
from src.services.embedding import get_embedding_service
from src.config import settings

logger = logging.getLogger(__name__)

class HybridRetrievalService:
    """
    Advanced hybrid retrieval system combining:
    1. Vector search (FAISS) - Semantic similarity
    2. Knowledge graphs (Neo4j) - Structured medical knowledge (SNOMED CT)
    3. Reciprocal Rank Fusion - Optimal result combination
    """
    
    def __init__(self):
        self.vector_db = get_vector_db()
        self.embedding_service = get_embedding_service()
        
        # Initialize Neo4j if available
        try:
            neo4j_driver = get_neo4j_driver()
            self.neo4j_service = Neo4jService(neo4j_driver) if neo4j_driver else None
        except Exception as e:
            logger.warning(f"Neo4j not available: {e}")
            self.neo4j_service = None
    
    async def retrieve(
        self,
        query: str,
        patient_id: Optional[str] = None,
        modalities: List[str] = ["text"],  # ["text", "image"]
        top_k_vector: int = None,
        top_k_graph: int = None,
        include_graph: bool = True,
        image_paths: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Perform hybrid multi-modal retrieval.
        
        Args:
            query: Search query (clinical question)
            patient_id: Filter by specific patient
            modalities: Which modalities to search ["text", "image"]
            top_k_vector: Number of vector results
            top_k_graph: Number of graph results
            include_graph: Whether to include knowledge graph search
            image_paths: Optional images for multi-modal search
            
        Returns:
            Combined retrieval results with ranked sources
        """
        top_k_vector = top_k_vector or settings.TOP_K_VECTOR
        top_k_graph = top_k_graph or settings.TOP_K_GRAPH
        
        results = {
            "vector_results": [],
            "graph_results": [],
            "combined_results": [],
            "context": "",
            "total_sources": 0
        }
        
        # 1. Vector search
        if "text" in modalities or "image" in modalities:
            vector_results = await self._vector_search(
                query=query,
                patient_id=patient_id,
                top_k=top_k_vector,
                image_paths=image_paths
            )
            results["vector_results"] = vector_results
        
        # 2. Knowledge graph search (SNOMED CT)
        if include_graph and self.neo4j_service:
            graph_results = await self._graph_search(
                query=query,
                top_k=top_k_graph
            )
            results["graph_results"] = graph_results
        
        # 3. Combine results using Reciprocal Rank Fusion
        combined = self._reciprocal_rank_fusion(
            vector_results=results["vector_results"],
            graph_results=results["graph_results"]
        )
        results["combined_results"] = combined
        
        # 4. Build context string
        results["context"] = self._build_context(combined)
        results["total_sources"] = len(combined)
        
        logger.info(f"Hybrid retrieval: {len(results['vector_results'])} vector + "
                   f"{len(results['graph_results'])} graph = {len(combined)} combined")
        
        return results
    
    async def _vector_search(
        self,
        query: str,
        patient_id: Optional[str],
        top_k: int,
        image_paths: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Semantic vector search using FAISS.
        Supports multi-modal (text + image) queries.
        """
        try:
            # Generate query embedding
            if image_paths:
                # Multi-modal embedding (text + images)
                query_embedding = self.embedding_service.embed_multimodal(
                    texts=[query],
                    image_paths=image_paths
                )
                query_vector = query_embedding[0]
            else:
                # Text-only embedding
                query_vector = self.embedding_service.embed_text(query)[0]
            
            # Build filter for patient-specific search
            filter_metadata = {"patient_id": patient_id} if patient_id else None
            
            # Search FAISS
            search_results = self.vector_db.search(
                query_vector=query_vector,
                top_k=top_k,
                filter_metadata=filter_metadata
            )
            
            # Enrich results with metadata
            enriched_results = []
            for result in search_results:
                metadata = result.get("metadata", {})
                enriched_results.append({
                    "source_id": f"vector_{result['id']}",
                    "source_type": "vector_search",
                    "content": metadata.get("text", ""),
                    "score": result["score"],
                    "patient_id": metadata.get("patient_id"),
                    "document_type": metadata.get("document_type"),
                    "file_id": metadata.get("file_id"),
                    "chunk_index": metadata.get("chunk_index")
                })
            
            logger.info(f"Vector search returned {len(enriched_results)} results")
            return enriched_results
            
        except Exception as e:
            logger.error(f"Vector search failed: {e}", exc_info=True)
            return []
    
    async def _graph_search(
        self,
        query: str,
        top_k: int
    ) -> List[Dict[str, Any]]:
        """
        Knowledge graph search using Neo4j (SNOMED CT).
        Retrieves structured medical knowledge.
        """
        if not self.neo4j_service:
            logger.warning("Neo4j service not available")
            return []
        
        try:
            # Search SNOMED CT concepts
            concepts = await self.neo4j_service.search_by_term(
                search_term=query,
                limit=top_k
            )
            
            results = []
            for concept_data in concepts:
                concept = concept_data.get('concept', {})
                score = concept_data.get('score', 0)
                
                # Get concept hierarchy for additional context
                concept_id = concept.get('conceptId', '')
                if concept_id:
                    hierarchy = await self.neo4j_service.get_concept_hierarchy(
                        concept_id=concept_id,
                        direction='both'
                    )
                    
                    # Build rich description
                    description = f"{concept.get('term', 'Unknown concept')}"
                    
                    # Add parent concepts for context
                    hierarchy_concepts = hierarchy.get('hierarchy', [])
                    if hierarchy_concepts:
                        parents = [h.get('term', '') for h in hierarchy_concepts[:3]]
                        if parents:
                            description += f"\n\nRelated concepts: {', '.join(filter(None, parents))}"
                    
                    # Add FSN (Fully Specified Name) if available
                    fsn = concept.get('fsn', '')
                    if fsn and fsn != concept.get('term'):
                        description += f"\n\nFully specified: {fsn}"
                    
                    results.append({
                        "source_id": f"snomed_{concept_id}",
                        "source_type": "knowledge_graph",
                        "content": description,
                        "score": score,
                        "concept_id": concept_id,
                        "concept_term": concept.get('term', ''),
                        "concept_fsn": fsn,
                        "hierarchy_depth": len(hierarchy_concepts)
                    })
            
            logger.info(f"Graph search returned {len(results)} SNOMED concepts")
            return results
            
        except Exception as e:
            logger.error(f"Graph search failed: {e}", exc_info=True)
            return []
    
    def _reciprocal_rank_fusion(
        self,
        vector_results: List[Dict[str, Any]],
        graph_results: List[Dict[str, Any]],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Combine results from multiple sources using Reciprocal Rank Fusion.
        
        RRF formula: score = Σ(1 / (k + rank))
        
        Args:
            vector_results: Results from vector search
            graph_results: Results from graph search
            k: RRF constant (default: 60)
            
        Returns:
            Combined and re-ranked results
        """
        rrf_scores = {}
        all_results = {}
        
        # Score vector results
        for rank, result in enumerate(vector_results, start=1):
            source_id = result['source_id']
            rrf_scores[source_id] = rrf_scores.get(source_id, 0) + (1 / (k + rank))
            all_results[source_id] = result
        
        # Score graph results
        for rank, result in enumerate(graph_results, start=1):
            source_id = result['source_id']
            rrf_scores[source_id] = rrf_scores.get(source_id, 0) + (1 / (k + rank))
            all_results[source_id] = result
        
        # Add RRF scores to results
        for source_id, result in all_results.items():
            result['rrf_score'] = rrf_scores[source_id]
            result['original_score'] = result.get('score', 0)
        
        # Sort by RRF score
        combined_results = sorted(
            all_results.values(),
            key=lambda x: x['rrf_score'],
            reverse=True
        )
        
        logger.info(f"RRF combined {len(vector_results)} vector + {len(graph_results)} graph "
                   f"= {len(combined_results)} total results")
        
        return combined_results
    
    def _build_context(
        self,
        results: List[Dict[str, Any]],
        max_results: int = 10
    ) -> str:
        """
        Build context string from retrieval results for LLM consumption.
        
        Args:
            results: Ranked retrieval results
            max_results: Maximum number of results to include
            
        Returns:
            Formatted context string
        """
        context_parts = []
        
        for i, result in enumerate(results[:max_results], 1):
            source_type = result['source_type']
            content = result['content']
            score = result.get('rrf_score', result.get('score', 0))
            
            # Format source header
            if source_type == 'vector_search':
                header = f"[Source {i} - Patient Document - Relevance: {score:.3f}]"
            elif source_type == 'knowledge_graph':
                concept_id = result.get('concept_id', 'Unknown')
                header = f"[Source {i} - Medical Knowledge (SNOMED:{concept_id}) - Relevance: {score:.3f}]"
            else:
                header = f"[Source {i} - {source_type} - Relevance: {score:.3f}]"
            
            # Add to context
            context_parts.append(f"{header}\n{content}\n")
        
        # Join with separator
        context = "\n" + "="*80 + "\n".join(context_parts)
        
        return context
    
    async def retrieve_for_patient(
        self,
        patient_id: str,
        query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieve all relevant information for a specific patient.
        
        Args:
            patient_id: Patient UUID
            query: Optional specific query (if None, returns all patient data)
            
        Returns:
            Patient-specific context
        """
        if query:
            # Query-specific retrieval
            return await self.retrieve(
                query=query,
                patient_id=patient_id,
                include_graph=True
            )
        else:
            # Retrieve all patient documents
            # This would query the database directly for all patient data
            # Implementation depends on your database structure
            pass
    
    async def cross_modal_validation(
        self,
        text_findings: str,
        image_findings: str
    ) -> Dict[str, Any]:
        """
        Validate consistency between text and image findings.
        
        Args:
            text_findings: Findings from clinical notes
            image_findings: Findings from imaging analysis
            
        Returns:
            Validation result with alignment score
        """
        # Embed both modalities
        text_embedding = self.embedding_service.embed_text(text_findings)[0]
        
        # For image findings (text description of image)
        image_text_embedding = self.embedding_service.embed_text(image_findings)[0]
        
        # Compute cosine similarity
        similarity = np.dot(text_embedding, image_text_embedding) / (
            np.linalg.norm(text_embedding) * np.linalg.norm(image_text_embedding)
        )
        
        # Interpret alignment
        if similarity > 0.8:
            alignment = "High - Findings are consistent"
        elif similarity > 0.6:
            alignment = "Moderate - Some consistency"
        else:
            alignment = "Low - Potential contradiction"
        
        return {
            "similarity_score": float(similarity),
            "alignment": alignment,
            "recommendation": "Review for discrepancies" if similarity < 0.6 else "Findings support each other"
        }

# Global instance
_retrieval_service: Optional[HybridRetrievalService] = None

def get_retrieval_service() -> HybridRetrievalService:
    """Get or create hybrid retrieval service instance."""
    global _retrieval_service
    if _retrieval_service is None:
        _retrieval_service = HybridRetrievalService()
    return _retrieval_service
