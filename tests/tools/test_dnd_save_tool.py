"""Agent-facing campaign save DAG tests."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.tools.dnd_save import DndSaveTool
from nanobot.dnd.db.database import Database, sqlite_database_url
from nanobot.dnd.db.models import Campaign


async def test_dnd_save_tool_creates_branch_and_reports_lineage(tmp_path: Path) -> None:
    database = Database(sqlite_database_url(tmp_path / "save-tool.db"))
    database.upgrade_schema()
    try:
        with database.transaction() as session:
            session.add(Campaign(id="campaign", name="Save Tool Test"))

        tool = DndSaveTool(database)
        root = await tool.execute(
            action="create",
            campaign_id="campaign",
            label="root",
        )
        left = await tool.execute(
            action="create",
            campaign_id="campaign",
            label="left",
        )
        restored = await tool.execute(
            action="restore",
            campaign_id="campaign",
            slot=root["slot"],
            auto_save=False,
        )
        assert restored["slot"] == root["slot"]

        right = await tool.execute(
            action="create",
            campaign_id="campaign",
            label="right",
        )
        lineage = await tool.execute(action="lineage", campaign_id="campaign")
        nodes = {node["id"]: node for node in lineage["nodes"]}

        assert nodes[left["id"]]["parent_save_id"] == root["id"]
        assert nodes[right["id"]]["parent_save_id"] == root["id"]
        assert lineage["active_save_id"] == right["id"]
        assert nodes[right["id"]]["active"] is True
    finally:
        database.dispose()
