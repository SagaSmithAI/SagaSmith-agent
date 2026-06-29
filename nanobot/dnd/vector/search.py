"""Shared ChromaDB dense-vector search helper.

This module provides a single function used by both ``RuleSearchService``
and ``ModuleSearchService`` when ChromaDB is enabled.
"""

from __future__ import annotations

import logging
from typing import Any

from nanobot.dnd.rules.embedding import EmbeddingProfile
from nanobot.dnd.vector.client import VectorStore

logger = logging.getLogger(__name__)


def chroma_dense_search(
    collection_name: str,
    query_vector: list[float],
    where: dict[str, Any] | None = None,
    *,
    profile: EmbeddingProfile | None = None,
    limit: int = 50,
) -> list[tuple[str, float]]:
    """Search a ChromaDB collection and return ``(chunk_id, similarity)`` pairs.

    Args:
        collection_name: ``"dnd_rules"`` or ``"dnd_modules"``.
        query_vector: Normalised 1024-dim BGE-M3 embedding.
        where: Optional ChromaDB metadata filter dict (e.g.
               ``{"campaign_id": "abc"}`` or
               ``{"rule_set_id": "x", "publication_id": {"$in": ["a","b"]}}``).
        limit: Maximum number of results.

    Returns:
        List of ``(chunk_id, similarity_score)`` ordered by descending
        similarity.  Similarity is ``1 - cosine_distance`` so that values
        closer to 1 are better matches (consistent with the existing numpy
        dot-product scoring on L2-normalised embeddings).
    """
    store = VectorStore()
    if not store.enabled:
        logger.warning("ChromaDB is not configured; dense search is unavailable")
        return []

    try:
        coll = (
            store.collection_for(collection_name, profile)
            if profile is not None
            else store.collection(collection_name)
        )
    except Exception:
        logger.exception("Failed to access ChromaDB collection %r", collection_name)
        return []

    try:
        results = coll.query(
            query_embeddings=[query_vector],
            n_results=min(limit, coll.count()),
            where=where,
            include=["distances"],
        )
    except Exception:
        logger.exception("ChromaDB query failed on collection %r", collection_name)
        return []

    ids = results.get("ids")
    distances = results.get("distances")
    if not ids or not distances or not ids[0]:
        return []

    chunk_ids: list[str] = ids[0]
    distance_list: list[float] = distances[0]

    # ChromaDB returns cosine *distance* (0 = identical, 2 = opposite).
    # Convert to similarity: 1 - distance, so 1 = identical, -1 = opposite.
    return [
        (chunk_id, 1.0 - float(dist))
        for chunk_id, dist in zip(chunk_ids, distance_list)
    ]
