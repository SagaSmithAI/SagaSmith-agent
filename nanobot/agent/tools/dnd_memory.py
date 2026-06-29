"""Natural-language access to branch-effective campaign memories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.memory import CampaignMemoryService
from nanobot.dnd.db.models import CampaignSave
from nanobot.dnd.memory_search import CampaignMemorySearchService


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "scope", "get", "status", "reindex"],
                "description": (
                    "search: answer a natural-language campaign-memory question. "
                    "scope: show which save slots define the current retrieval range. "
                    "get: list effective memories at the selected save. "
                    "status: inspect the Chroma memory index. "
                    "reindex: rebuild memory vectors for a campaign."
                ),
            },
            "campaign_id": {"type": "string"},
            "save_id": {"type": "string"},
            "slot": {
                "type": "integer",
                "minimum": 1,
                "description": "Human-facing save slot. Takes precedence over the active head.",
            },
            "query": {"type": "string"},
            "memory_id": {"type": "string"},
            "statuses": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["candidate", "stable", "permanent"],
                },
            },
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["action"],
    }
)
class DndMemoryTool(Tool):
    """Search campaign memory through the active save DAG."""

    name = "dnd_memory"
    description = (
        "Search long-term campaign facts using natural language. Results are restricted to "
        "the active save's ancestor path, so memories from sibling save branches never leak "
        "into the answer. Use action=scope to explain which slots are included."
    )
    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(Database())

    def __init__(self, database: Database, *, migrate: bool = True) -> None:
        self.database = database
        self._migrate = migrate
        self._ready = False
        self.memory = CampaignMemoryService(database)
        self.search_service = CampaignMemorySearchService(database)

    @property
    def read_only(self) -> bool:
        return False

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        if self._migrate:
            self.database.upgrade_schema()
        self._ready = True

    @staticmethod
    def _context_campaign_id() -> str | None:
        context = current_request_context()
        if context is None:
            return None
        value = context.metadata.get("campaign_id")
        return str(value) if value else None

    async def _execute(self, args: dict[str, Any]) -> dict[str, Any]:
        self._ensure_ready()
        action = str(args["action"])
        campaign_id = args.get("campaign_id") or self._context_campaign_id()

        if action == "status":
            return self.search_service.status()
        if not campaign_id:
            return {
                "error": "campaign_id_required",
                "detail": "Select a campaign or provide campaign_id.",
            }

        try:
            save_id = self._resolve_save_id(
                campaign_id,
                save_id=args.get("save_id"),
                slot=args.get("slot"),
            )
        except ValueError as exc:
            return {"error": "save_not_found", "detail": str(exc)}
        if action == "scope":
            return self.memory.scope(campaign_id, save_id=save_id)
        if action == "get":
            rows = self.memory.get_effective(
                campaign_id,
                save_id=save_id,
                statuses=args.get("statuses"),
            )
            memory_id = args.get("memory_id")
            if memory_id:
                rows = [row for row in rows if row["id"] == memory_id]
            return {
                **self.memory.scope(campaign_id, save_id=save_id),
                "memories": rows,
            }
        if action == "search":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"error": "query_required", "detail": "query is required for search"}
            return self.search_service.search(
                campaign_id,
                query,
                save_id=save_id,
                statuses=args.get("statuses"),
                top_k=int(args.get("top_k", 8)),
            )
        if action == "reindex":
            indexed = self.search_service.reindex(campaign_id)
            return {
                "campaign_id": campaign_id,
                "indexed": indexed,
                **self.search_service.status(),
            }
        return {"error": "unknown_action", "detail": action}

    def _resolve_save_id(
        self,
        campaign_id: str,
        *,
        save_id: str | None,
        slot: int | None,
    ) -> str | None:
        if slot is None:
            return save_id
        with self.database.transaction() as session:
            resolved = session.scalar(
                select(CampaignSave.id).where(
                    CampaignSave.campaign_id == campaign_id,
                    CampaignSave.slot == slot,
                )
            )
        if resolved is None:
            raise ValueError(f"save slot {slot} does not exist in campaign {campaign_id}")
        return resolved

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Run one branch-aware campaign-memory operation."""
        return await self._execute(kwargs)
