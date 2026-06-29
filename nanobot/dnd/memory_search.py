"""Natural-language search over branch-effective campaign memory revisions."""

from __future__ import annotations

import math
import re
from typing import Any, Protocol

from sqlalchemy import select

from nanobot.dnd.db.database import Database
from nanobot.dnd.db.memory import CampaignMemoryService
from nanobot.dnd.db.models.runtime import CampaignMemory, CampaignMemoryRevision
from nanobot.dnd.rules.embedding import BgeM3Embedder
from nanobot.dnd.vector.client import VectorStore

COLLECTION_NAME = "dnd_campaign_memories"


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class CampaignMemorySearchService:
    """Index memory revisions and search only the revisions effective at one save."""

    def __init__(
        self,
        database: Database,
        *,
        embedder: Embedder | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.database = database
        self.memory = CampaignMemoryService(database)
        self.embedder = embedder
        self.vector_store = vector_store or VectorStore()

    def search(
        self,
        campaign_id: str,
        query: str,
        *,
        save_id: str | None = None,
        statuses: list[str] | None = None,
        top_k: int = 8,
    ) -> dict[str, Any]:
        scope = self.memory.scope(campaign_id, save_id=save_id)
        memories = self.memory.get_effective(
            campaign_id,
            save_id=scope["save_id"],
            statuses=statuses,
        )
        if not memories:
            return {
                **scope,
                "query": query,
                "retrieval": "empty",
                "hits": [],
            }

        if self.vector_store.enabled:
            self.index_rows(memories)
            scored = self._dense_scores(query, memories)
            retrieval = "chroma"
        else:
            scored = self._lexical_scores(query, memories)
            retrieval = "lexical"

        hits = []
        for score, row in scored[: max(1, min(top_k, 20))]:
            hit = dict(row)
            hit["score"] = round(float(score), 6)
            hits.append(hit)
        return {
            **scope,
            "query": query,
            "retrieval": retrieval,
            "hits": hits,
        }

    def index_revision_ids(self, revision_ids: list[str]) -> int:
        ids = [value for value in revision_ids if value]
        if not ids or not self.vector_store.enabled:
            return 0
        with self.database.transaction() as session:
            rows = session.execute(
                select(CampaignMemoryRevision, CampaignMemory)
                .join(CampaignMemory, CampaignMemory.id == CampaignMemoryRevision.memory_id)
                .where(CampaignMemoryRevision.id.in_(ids))
            ).all()
            payload = [
                _joined_revision_row(revision, memory)
                for revision, memory in rows
            ]
        return self.index_rows(payload)

    def reindex(self, campaign_id: str | None = None) -> int:
        if not self.vector_store.enabled:
            return 0
        if campaign_id:
            self.vector_store.collection(COLLECTION_NAME).delete(
                where={"campaign_id": campaign_id}
            )
        else:
            self.vector_store.drop_collection(COLLECTION_NAME)
        with self.database.transaction() as session:
            statement = (
                select(CampaignMemoryRevision, CampaignMemory)
                .join(CampaignMemory, CampaignMemory.id == CampaignMemoryRevision.memory_id)
                .order_by(CampaignMemoryRevision.created_at)
            )
            if campaign_id:
                statement = statement.where(
                    CampaignMemoryRevision.campaign_id == campaign_id
                )
            rows = session.execute(statement).all()
            payload = [
                _joined_revision_row(revision, memory)
                for revision, memory in rows
            ]
        return self.index_rows(payload)

    def index_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows or not self.vector_store.enabled:
            return 0
        texts = [_embedding_text(row) for row in rows]
        vectors = self._embedder().encode(texts)
        collection = self.vector_store.collection(COLLECTION_NAME)
        collection.upsert(
            ids=[str(row["revision_id"]) for row in rows],
            embeddings=vectors,
            documents=texts,
            metadatas=[_metadata(row) for row in rows],
        )
        return len(rows)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.vector_store.enabled,
            "collection": self.vector_store.collection_stats(COLLECTION_NAME)
            if self.vector_store.enabled
            else None,
        }

    def _dense_scores(
        self,
        query: str,
        rows: list[dict[str, Any]],
    ) -> list[tuple[float, dict[str, Any]]]:
        collection = self.vector_store.collection(COLLECTION_NAME)
        ids = [str(row["revision_id"]) for row in rows]
        result = collection.get(ids=ids, include=["embeddings"])
        returned_ids = [str(value) for value in result.get("ids") or []]
        embeddings = result.get("embeddings")
        if embeddings is None:
            return self._lexical_scores(query, rows)
        query_vector = self._embedder().encode([query])[0]
        row_by_id = {str(row["revision_id"]): row for row in rows}
        scored = [
            (_cosine(query_vector, vector), row_by_id[revision_id])
            for revision_id, vector in zip(returned_ids, embeddings, strict=True)
            if revision_id in row_by_id
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    @staticmethod
    def _lexical_scores(
        query: str,
        rows: list[dict[str, Any]],
    ) -> list[tuple[float, dict[str, Any]]]:
        query_terms = _search_terms(query)
        scored = []
        for row in rows:
            haystack = " ".join([
                str(row.get("text") or ""),
                str(row.get("kind") or ""),
                str(row.get("entity_id") or ""),
                str(row.get("fact_type") or ""),
            ])
            terms = _search_terms(haystack)
            overlap = len(query_terms & terms)
            score = overlap / max(1, len(query_terms))
            if query.casefold() in haystack.casefold():
                score += 1.0
            scored.append((score, row))
        scored.sort(
            key=lambda item: (
                item[0],
                _priority_rank(str(item[1].get("priority") or "")),
                -int(item[1].get("distance") or 0),
            ),
            reverse=True,
        )
        return scored

    def _embedder(self) -> Embedder:
        if self.embedder is None:
            self.embedder = BgeM3Embedder()
        return self.embedder


def _joined_revision_row(
    revision: CampaignMemoryRevision,
    memory: CampaignMemory,
) -> dict[str, Any]:
    return {
        "id": memory.id,
        "revision_id": revision.id,
        "campaign_id": memory.campaign_id,
        "kind": memory.kind,
        "entity_type": memory.entity_type,
        "entity_id": memory.entity_id,
        "fact_type": memory.fact_type,
        "text": revision.text,
        "priority": revision.priority,
        "status": revision.status,
        "operation": revision.operation,
        "source_save_id": revision.save_id,
        "distance": 0,
        "created_at": revision.created_at.isoformat(),
    }


def _embedding_text(row: dict[str, Any]) -> str:
    return " | ".join([
        str(row.get("kind") or ""),
        str(row.get("entity_type") or ""),
        str(row.get("entity_id") or ""),
        str(row.get("fact_type") or ""),
        str(row.get("text") or ""),
    ])


def _metadata(row: dict[str, Any]) -> dict[str, str]:
    return {
        "campaign_id": str(row["campaign_id"]),
        "memory_id": str(row["id"]),
        "save_id": str(row["source_save_id"]),
        "kind": str(row["kind"]),
        "entity_type": str(row["entity_type"]),
        "entity_id": str(row["entity_id"]),
        "fact_type": str(row["fact_type"]),
        "priority": str(row["priority"]),
        "status": str(row["status"]),
    }


def _cosine(left: list[float], right: Any) -> float:
    right_values = [float(value) for value in right]
    if len(left) != len(right_values):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right_values, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _search_terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    compact = re.sub(r"\s+", "", normalized)
    grams = {
        compact[index:index + 2]
        for index in range(max(0, len(compact) - 1))
    }
    return words | grams


def _priority_rank(priority: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(priority, 0)
