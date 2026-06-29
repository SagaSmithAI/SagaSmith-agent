"""Agent-facing campaign-memory tool tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.tools.dnd_memory import DndMemoryTool
from nanobot.dnd.db.database import Database, sqlite_database_url
from nanobot.dnd.db.memory import CampaignMemoryService
from nanobot.dnd.db.models import Campaign
from nanobot.dnd.db.snapshots import CampaignSnapshotService
from nanobot.dnd.memory_search import CampaignMemorySearchService


class _DisabledVectorStore:
    enabled = False

    def collection_stats(self, name: str) -> dict[str, Any]:
        return {"name": name, "count": 0}


async def test_dnd_memory_tool_uses_slot_and_active_branch(tmp_path: Path) -> None:
    database = Database(sqlite_database_url(tmp_path / "memory-tool.db"))
    database.upgrade_schema()
    try:
        with database.transaction() as session:
            session.add(Campaign(id="campaign", name="Tool Test"))

        snapshots = CampaignSnapshotService(database)
        memories = CampaignMemoryService(database)
        root = snapshots.create("campaign", label="root")
        memories.record(
            "campaign",
            root.id,
            kind="npc_relation",
            text="Mira is neutral.",
            priority="medium",
            status="candidate",
            entity_type="npc",
            entity_id="npc_mira",
            fact_type="relationship",
        )

        left = snapshots.create("campaign", label="left")
        memories.record(
            "campaign",
            left.id,
            kind="npc_relation",
            text="Mira is hostile.",
            priority="high",
            status="permanent",
            entity_type="npc",
            entity_id="npc_mira",
            fact_type="relationship",
        )

        snapshots.restore("campaign", root.slot, auto_save=False)
        right = snapshots.create("campaign", label="right")
        memories.record(
            "campaign",
            right.id,
            kind="npc_relation",
            text="Mira is an ally.",
            priority="high",
            status="permanent",
            entity_type="npc",
            entity_id="npc_mira",
            fact_type="relationship",
        )

        tool = DndMemoryTool(database, migrate=False)
        tool.search_service = CampaignMemorySearchService(
            database,
            vector_store=_DisabledVectorStore(),  # type: ignore[arg-type]
        )

        active_scope = await tool.execute(action="scope", campaign_id="campaign")
        assert [row["slot"] for row in active_scope["included_saves"]] == [
            root.slot,
            right.slot,
        ]

        historical = await tool.execute(
            action="search",
            campaign_id="campaign",
            slot=left.slot,
            query="Mira hostile",
        )
        assert historical["retrieval"] == "lexical"
        assert historical["hits"][0]["text"] == "Mira is hostile."

        active = await tool.execute(
            action="get",
            campaign_id="campaign",
        )
        assert [row["text"] for row in active["memories"]] == ["Mira is an ally."]

        missing = await tool.execute(
            action="scope",
            campaign_id="campaign",
            slot=999,
        )
        assert missing["error"] == "save_not_found"
    finally:
        database.dispose()
