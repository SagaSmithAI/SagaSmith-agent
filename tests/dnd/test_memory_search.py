"""Campaign-memory vector search is constrained by the active save DAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.dnd.db.database import Database, sqlite_database_url
from nanobot.dnd.db.memory import CampaignMemoryService
from nanobot.dnd.db.models import Campaign
from nanobot.dnd.db.snapshots import CampaignSnapshotService
from nanobot.dnd.memory_search import CampaignMemorySearchService


class _FakeEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "hostile" in text.casefold() else [0.0, 1.0] for text in texts]


class _FakeCollection:
    def __init__(self) -> None:
        self.rows: dict[str, list[float]] = {}
        self.metadatas: dict[str, dict[str, str]] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str]],
    ) -> None:
        del documents
        self.rows.update(zip(ids, embeddings, strict=True))
        self.metadatas.update(zip(ids, metadatas, strict=True))

    def get(self, *, ids: list[str], include: list[str]) -> dict[str, Any]:
        del include
        found = [value for value in ids if value in self.rows]
        return {
            "ids": found,
            "embeddings": [self.rows[value] for value in found],
        }

    def count(self) -> int:
        return len(self.rows)

    def delete(self, *, where: dict[str, str]) -> None:
        deleted = [
            row_id
            for row_id, metadata in self.metadatas.items()
            if all(metadata.get(key) == value for key, value in where.items())
        ]
        for row_id in deleted:
            self.rows.pop(row_id, None)
            self.metadatas.pop(row_id, None)


class _FakeVectorStore:
    enabled = True

    def __init__(self) -> None:
        self.memory = _FakeCollection()

    def collection(self, name: str) -> _FakeCollection:
        assert name == "dnd_campaign_memories"
        return self.memory

    def collection_stats(self, name: str) -> dict[str, Any]:
        return {"name": name, "count": self.memory.count()}

    def drop_collection(self, name: str) -> None:
        assert name == "dnd_campaign_memories"
        self.memory = _FakeCollection()


def test_chroma_receives_only_effective_revision_ids(tmp_path: Path) -> None:
    database = Database(sqlite_database_url(tmp_path / "memory-search.db"))
    database.upgrade_schema()
    try:
        with database.transaction() as session:
            session.add(Campaign(id="campaign", name="Branch Test"))

        snapshots = CampaignSnapshotService(database)
        memories = CampaignMemoryService(database)
        root = snapshots.create("campaign", label="root")
        memories.record(
            "campaign", root.id,
            kind="npc_relation", text="Mira is neutral.",
            priority="medium", status="candidate",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )

        left = snapshots.create("campaign", label="left")
        _, left_revision = memories.record(
            "campaign", left.id,
            kind="npc_relation", text="Mira is hostile.",
            priority="high", status="permanent",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )

        snapshots.restore("campaign", root.slot, auto_save=False)
        right = snapshots.create("campaign", label="right")
        _, right_revision = memories.record(
            "campaign", right.id,
            kind="npc_relation", text="Mira is an ally.",
            priority="high", status="permanent",
            entity_type="npc", entity_id="npc_mira", fact_type="relationship",
        )

        store = _FakeVectorStore()
        search = CampaignMemorySearchService(
            database,
            embedder=_FakeEmbedder(),
            vector_store=store,  # type: ignore[arg-type]
        )
        result = search.search("campaign", "Mira relationship", save_id=right.id)

        assert result["effective_revision_ids"] == [right_revision]
        assert set(store.memory.rows) == {right_revision}
        assert left_revision not in store.memory.rows
        assert {row["id"] for row in result["included_saves"]} == {root.id, right.id}
        assert result["hits"][0]["text"] == "Mira is an ally."

        store.memory.rows["stale"] = [1.0, 0.0]
        store.memory.metadatas["stale"] = {"campaign_id": "campaign"}
        assert search.reindex("campaign") == 3
        assert "stale" not in store.memory.rows
        assert {left_revision, right_revision}.issubset(store.memory.rows)
    finally:
        database.dispose()
