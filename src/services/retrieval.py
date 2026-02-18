"""
Hybrid retrieval service.
Fixed:
  - _build_context() separator string bug (was prepended once, not used as join separator)
  - retrieve_for_patient() no-query branch now implemented (returns all patient vectors)
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
    Hybrid retrieval: FAISS vector search + Neo4j SNOMED knowledge graph
    combined with Reciprocal Rank Fusion.
    """

    def __init__(self):
        self.vector_db = get_vector_db()
        self.embedding_service = get_embedding_service()
        try:
            neo4j_driver = get_neo4j_driver()
            self.neo4j_service = Neo4jService(neo4j_driver) if neo4j_driver else None
        except Exception as e:
            logger.warning(f"Neo4j unavailable: {e}")
            self.neo4j_service = None

    async def retrieve(
        self,
        query: str,
        patient_id: Optional[str] = None,
        modalities: List[str] = None,
        top_k_vector: Optional[int] = None,
        top_k_graph: Optional[int] = None,
        include_graph: bool = True,
        image_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Perform hybrid multi-modal retrieval with RRF."""
        modalities = modalities or ["text"]
        top_k_vector = top_k_vector or settings.TOP_K_VECTOR
        top_k_graph = top_k_graph or settings.TOP_K_GRAPH

        results: Dict[str, Any] = {
            "vector_results": [],
            "graph_results": [],
            "combined_results": [],
            "context": "",
            "total_sources": 0,
        }

        if "text" in modalities or "image" in modalities:
            results["vector_results"] = await self._vector_search(
                query=query, patient_id=patient_id, top_k=top_k_vector, image_paths=image_paths
            )

        if include_graph and self.neo4j_service:
            results["graph_results"] = await self._graph_search(query=query, top_k=top_k_graph)

        combined = self._reciprocal_rank_fusion(results["vector_results"], results["graph_results"])
        results["combined_results"] = combined
        results["context"] = self._build_context(combined)
        results["total_sources"] = len(combined)

        logger.info(
            f"Hybrid retrieval: {len(results['vector_results'])} vector + "
            f"{len(results['graph_results'])} graph = {len(combined)} combined"
        )
        return results

    async def _vector_search(
        self,
        query: str,
        patient_id: Optional[str],
        top_k: int,
        image_paths: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            if image_paths:
                raw = self.embedding_service.embed_multimodal(texts=[query], image_paths=image_paths)
                query_vector = raw[0]
            else:
                query_vector = self.embedding_service.embed_text(query)[0]

            filter_meta = {"patient_id": patient_id} if patient_id else None
            search_results = self.vector_db.search(query_vector=query_vector, top_k=top_k, filter_metadata=filter_meta)

            return [
                {
                    "source_id": f"vector_{r['id']}",
                    "source_type": "vector_search",
                    "content": r["metadata"].get("text", ""),
                    "score": r["score"],
                    "patient_id": r["metadata"].get("patient_id"),
                    "document_type": r["metadata"].get("document_type"),
                    "file_id": r["metadata"].get("file_id"),
                    "chunk_index": r["metadata"].get("chunk_index"),
                }
                for r in search_results
            ]
        except Exception as e:
            logger.error(f"Vector search failed: {e}", exc_info=True)
            return []

    async def _graph_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        if not self.neo4j_service:
            return []
        try:
            concepts = await self.neo4j_service.search_by_term(search_term=query, limit=top_k)
            results = []
            for item in concepts:
                concept = item.get("concept", {})
                concept_id = concept.get("conceptId", "")
                if not concept_id:
                    continue
                hierarchy = await self.neo4j_service.get_concept_hierarchy(concept_id=concept_id, direction="both")
                description = concept.get("term", "Unknown concept")
                parents = [h.get("term", "") for h in hierarchy.get("hierarchy", [])[:3]]
                if parents:
                    description += f"\n\nRelated: {', '.join(filter(None, parents))}"
                fsn = concept.get("fsn", "")
                if fsn and fsn != concept.get("term"):
                    description += f"\n\nFully specified: {fsn}"
                results.append({
                    "source_id": f"snomed_{concept_id}",
                    "source_type": "knowledge_graph",
                    "content": description,
                    "score": item.get("score", 0),
                    "concept_id": concept_id,
                    "concept_term": concept.get("term", ""),
                    "concept_fsn": fsn,
                    "hierarchy_depth": len(hierarchy.get("hierarchy", [])),
                })
            return results
        except Exception as e:
            logger.error(f"Graph search failed: {e}", exc_info=True)
            return []

    def _reciprocal_rank_fusion(
        self,
        vector_results: List[Dict[str, Any]],
        graph_results: List[Dict[str, Any]],
        k: int = 60,
    ) -> List[Dict[str, Any]]:
        """Combine result lists using Reciprocal Rank Fusion."""
        rrf_scores: Dict[str, float] = {}
        all_results: Dict[str, Dict[str, Any]] = {}

        for rank, result in enumerate(vector_results, 1):
            sid = result["source_id"]
            rrf_scores[sid] = rrf_scores.get(sid, 0.0) + 1.0 / (k + rank)
            all_results[sid] = result

        for rank, result in enumerate(graph_results, 1):
            sid = result["source_id"]
            rrf_scores[sid] = rrf_scores.get(sid, 0.0) + 1.0 / (k + rank)
            all_results[sid] = result

        for sid, result in all_results.items():
            result["rrf_score"] = rrf_scores[sid]
            result["original_score"] = result.get("score", 0)

        return sorted(all_results.values(), key=lambda x: x["rrf_score"], reverse=True)

    def _build_context(self, results: List[Dict[str, Any]], max_results: int = 10) -> str:
        """
        Build a context string for LLM consumption.
        Fixed: separator is used correctly as a join delimiter (not prepended once).
        """
        parts = []
        separator = "\n" + "=" * 80 + "\n"

        for i, result in enumerate(results[:max_results], 1):
            source_type = result["source_type"]
            content = result["content"]
            score = result.get("rrf_score", result.get("score", 0))

            if source_type == "vector_search":
                header = f"[Source {i} — Patient Document — Relevance: {score:.3f}]"
            elif source_type == "knowledge_graph":
                cid = result.get("concept_id", "?")
                header = f"[Source {i} — SNOMED CT:{cid} — Relevance: {score:.3f}]"
            else:
                header = f"[Source {i} — {source_type} — Relevance: {score:.3f}]"

            parts.append(f"{header}\n{content}")

        # ← Fixed: join with separator (was concatenating incorrectly)
        return separator.join(parts)

    async def retrieve_for_patient(
        self,
        patient_id: str,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve context for a patient. If no query, fetches all patient vectors.
        Fixed: no-query branch was previously a bare `pass`.
        """
        if query:
            return await self.retrieve(query=query, patient_id=patient_id, include_graph=True)

        # No query — return all vectors stored for this patient
        all_patient_docs = self.vector_db.search(
            query_vector=np.zeros(settings.VECTOR_DIMENSION, dtype="float32"),
            top_k=50,
            filter_metadata={"patient_id": patient_id},
        )
        combined = [
            {
                "source_id": f"vector_{r['id']}",
                "source_type": "vector_search",
                "content": r["metadata"].get("text", ""),
                "score": r["score"],
                "rrf_score": r["score"],
                "patient_id": r["metadata"].get("patient_id"),
            }
            for r in all_patient_docs
        ]
        return {
            "vector_results": combined,
            "graph_results": [],
            "combined_results": combined,
            "context": self._build_context(combined),
            "total_sources": len(combined),
        }

    async def cross_modal_validation(
        self,
        text_findings: str,
        image_findings: str,
    ) -> Dict[str, Any]:
        """Validate consistency between text and image findings via cosine similarity."""
        text_emb = self.embedding_service.embed_text(text_findings)[0]
        img_text_emb = self.embedding_service.embed_text(image_findings)[0]
        norm_a = np.linalg.norm(text_emb)
        norm_b = np.linalg.norm(img_text_emb)
        similarity = float(np.dot(text_emb, img_text_emb) / (norm_a * norm_b)) if norm_a and norm_b else 0.0
        if similarity > 0.8:
            alignment = "High — Findings are consistent"
        elif similarity > 0.6:
            alignment = "Moderate — Some consistency"
        else:
            alignment = "Low — Potential contradiction"
        return {
            "similarity_score": similarity,
            "alignment": alignment,
            "recommendation": "Review for discrepancies" if similarity < 0.6 else "Findings support each other",
        }


_retrieval_service: Optional[HybridRetrievalService] = None


def get_retrieval_service() -> HybridRetrievalService:
    global _retrieval_service
    if _retrieval_service is None:
        _retrieval_service = HybridRetrievalService()
    return _retrieval_service