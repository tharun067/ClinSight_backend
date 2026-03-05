from neo4j import AsyncGraphDatabase, AsyncDriver
from typing import Optional, List, Dict, Any
import logging

from src.config import settings

logger = logging.getLogger(__name__)

_driver: Optional[AsyncDriver] = None


async def init_neo4j_driver():
    global _driver
    try:
        logger.info(f"Initializing Neo4j driver at {settings.NEO4J_URI}")
        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_pool_size=50,
            connection_acquisition_timeout=60.0,
        )
        await _driver.verify_connectivity()
        logger.info("Neo4j driver initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Neo4j driver: {e}")
        raise


async def close_neo4j_driver():
    global _driver
    if _driver:
        try:
            await _driver.close()
            logger.info("Neo4j driver closed.")
        except Exception as e:
            logger.error(f"Error closing Neo4j driver: {e}")


async def get_neo4j_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        raise RuntimeError("Neo4j driver is not initialized.")
    return _driver


class Neo4jService:
    def __init__(self, driver: AsyncDriver):
        self.driver = driver

    async def query_concepts(
        self,
        concept_ids: List[str],
        relationship_types: Optional[List[str]] = None,
        max_depth: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        Query SNOMED CT concepts and their relationships.
        max_depth is validated as a plain integer before being embedded
        in the Cypher query string — it is never a user-supplied raw string.
        """
        # Clamp depth to a safe range so it cannot be misused even if
        # the value somehow arrives from an untrusted caller.
        max_depth = max(1, min(int(max_depth), 5))

        async with self.driver.session() as session:
            query = f"""
            MATCH (c:Concept)
            WHERE c.conceptId IN $concept_ids
            OPTIONAL MATCH path = (c)-[r*1..{max_depth}]-(related:Concept)
            WHERE $relationship_types IS NULL
               OR ALL(rel IN r WHERE type(rel) IN $relationship_types)
            RETURN c,
                   collect(DISTINCT related) AS related_concepts,
                   collect(DISTINCT [rel IN r | type(rel)]) AS relationship_paths
            """
            result = await session.run(
                query,
                concept_ids=concept_ids,
                relationship_types=relationship_types,
            )
            records = []
            async for record in result:
                records.append(
                    {
                        "concept": dict(record["c"]),
                        "related_concepts": [dict(r) for r in record["related_concepts"]],
                        "relationship_paths": record["relationship_paths"],
                    }
                )
            return records

    async def search_by_term(
        self, search_term: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Full-text search for SNOMED CT concepts."""
        limit = max(1, min(int(limit), 100))
        async with self.driver.session() as session:
            query = """
            CALL db.index.fulltext.queryNodes('conceptSearch', $search_term)
            YIELD node, score
            RETURN node, score
            ORDER BY score DESC
            LIMIT $limit
            """
            result = await session.run(query, search_term=search_term, limit=limit)
            records = []
            async for record in result:
                records.append(
                    {"concept": dict(record["node"]), "score": record["score"]}
                )
            return records

    async def get_concept_hierarchy(
        self, concept_id: str, direction: str = "both"
    ) -> Dict[str, Any]:
        """Get hierarchical (IS-A) relationships for a concept."""
        async with self.driver.session() as session:
            if direction == "up":
                rel_pattern = "<-[:IS_A*]-"
            elif direction == "down":
                rel_pattern = "-[:IS_A*]->"
            else:
                rel_pattern = "-[:IS_A*]-"

            query = f"""
            MATCH (c:Concept {{conceptId: $concept_id}})
            OPTIONAL MATCH path = (c){rel_pattern}(related:Concept)
            RETURN c,
                   collect(DISTINCT related) AS hierarchy,
                   [rel IN relationships(path) | type(rel)] AS relationship_types
            """
            result = await session.run(query, concept_id=concept_id)
            record = await result.single()
            if record:
                return {
                    "concept": dict(record["c"]),
                    "hierarchy": [dict(h) for h in record["hierarchy"]],
                    "relationship_types": record["relationship_types"],
                }
            return {}