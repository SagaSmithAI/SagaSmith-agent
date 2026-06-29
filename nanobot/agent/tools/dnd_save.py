"""Native campaign snapshot management — save, list, verify, restore, export, delete."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.dnd.db.campaigns import CampaignNotFoundError
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models.runtime import CampaignSave
from nanobot.dnd.db.snapshots import (
    CampaignSnapshotService,
    InvalidSnapshotError,
    SnapshotError,
    SnapshotNotFoundError,
)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create", "list", "lineage", "verify", "restore", "delete", "export",
                    "regenerate_recap",
                ],
                "description": (
                    "create: save current campaign state with auto recap. "
                    "list: list all saves for a campaign. "
                    "lineage: return the save DAG and active head. "
                    "verify: check snapshot integrity. "
                    "restore: load a snapshot (auto-saves current state first). "
                    "delete: remove a snapshot slot. "
                    "export: dump snapshot to a JSON file. "
                    "regenerate_recap: re-run recap generation for an existing save."
                ),
            },
            "campaign_id": {"type": "string", "description": "Campaign ID."},
            "slot": {
                "type": "integer",
                "description": "Snapshot slot number for verify/restore/delete/export/regenerate_recap.",
            },
            "label": {"type": "string", "description": "Human-readable save label."},
            "output": {"type": "string", "description": "Output path for export action."},
            "auto_save": {
                "type": "boolean",
                "description": "Auto-save current state before restore (default true).",
            },
        },
        "required": ["action", "campaign_id"],
    }
)
class DndSaveTool(Tool):
    """Save and restore campaign snapshots without copying module content."""

    name = "dnd_save"
    description = (
        "Create, list, verify, restore, export, and delete campaign snapshots. "
        "Snapshots contain only mutable game state — module documents and embeddings "
        "are never duplicated. Restore auto-saves current state before loading. "
        "Each create action generates a narrative recap comparing against the "
        "previous save, and triggers long-term memory recording."
    )
    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        recap_generator = None
        if ctx.provider_snapshot_loader:
            try:
                snapshot = ctx.provider_snapshot_loader()
                from nanobot.dnd.db.recap import RecapGenerator
                recap_generator = RecapGenerator(snapshot.provider, snapshot.model)
            except Exception:
                logger.warning("Failed to initialize RecapGenerator; recap will be skipped")
        return cls(Database(), recap_generator=recap_generator)

    def __init__(
        self,
        database: Database,
        recap_generator: Any = None,
    ) -> None:
        self.database = database
        self.recap_generator = recap_generator
        self._service = CampaignSnapshotService(
            database, recap_generator=recap_generator,
        )

    @property
    def read_only(self) -> bool:
        return False

    async def _execute(self, args: dict[str, Any]) -> dict[str, Any]:
        action = args["action"]
        campaign_id = args["campaign_id"]
        ctx = current_request_context()

        if action == "create":
            # Generate recap asynchronously before the sync save
            recap = await self._generate_recap(campaign_id)
            result = self._service.create(
                campaign_id,
                label=args.get("label", ""),
                actor_id=getattr(ctx, "actor_id", None),
                recap=recap,
            )
            self._index_memory_actions(result.memory_actions)
            return asdict(result)

        if action == "list":
            try:
                saves = self._service.list(campaign_id)
            except CampaignNotFoundError as exc:
                return {"error": "campaign_not_found", "detail": str(exc)}
            result = []
            for s in saves:
                d = asdict(s)
                if s.recap:
                    summary = s.recap.get("summary", "")
                    if len(summary) > 100:
                        summary = summary[:100] + "..."
                    d["recap_summary"] = summary
                result.append(d)
            return {"saves": result}

        if action == "lineage":
            try:
                return self._service.lineage(campaign_id)
            except CampaignNotFoundError as exc:
                return {"error": "campaign_not_found", "detail": str(exc)}

        if action == "verify":
            try:
                payload = self._service.get(campaign_id, args["slot"])
            except (SnapshotNotFoundError, InvalidSnapshotError) as exc:
                return {"valid": False, "error": type(exc).__name__, "detail": str(exc)}
            except CampaignNotFoundError as exc:
                return {"valid": False, "error": "campaign_not_found", "detail": str(exc)}
            return {
                "valid": True,
                "campaign_id": payload["campaign_id"],
                "schema_version": payload["schema_version"],
                "captured_at": payload["captured_at"],
                "recap": payload.get("recap"),
            }

        if action == "restore":
            try:
                result = self._service.restore(
                    campaign_id,
                    args["slot"],
                    actor_id=getattr(ctx, "actor_id", None),
                    auto_save=args.get("auto_save", True),
                )
            except (SnapshotNotFoundError, InvalidSnapshotError, CampaignNotFoundError) as exc:
                return {"error": type(exc).__name__, "detail": str(exc)}
            return asdict(result)

        if action == "delete":
            try:
                self._service.delete(campaign_id, args["slot"])
            except (SnapshotError, CampaignNotFoundError) as exc:
                return {"error": type(exc).__name__, "detail": str(exc)}
            return {"deleted": True, "campaign_id": campaign_id, "slot": args["slot"]}

        if action == "export":
            try:
                payload = self._service.export(campaign_id, args["slot"], args["output"])
            except (SnapshotNotFoundError, CampaignNotFoundError) as exc:
                return {"error": type(exc).__name__, "detail": str(exc)}
            return {"exported": True, "output": args["output"], "slot": args["slot"]}

        if action == "regenerate_recap":
            return await self._regenerate_recap(campaign_id, args["slot"])

        return {"error": "unknown_action", "detail": action}

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Run one campaign snapshot operation."""
        return await self._execute(kwargs)

    # -- Recap helpers --------------------------------------------------------

    async def _generate_recap(self, campaign_id: str) -> dict | None:
        """Generate a recap dict by comparing current state to previous save."""
        if self.recap_generator is None:
            return None

        try:
            with self.database.transaction() as session:
                previous_save = self._service._active_save(session, campaign_id)

                current_payload = CampaignSnapshotService.capture_from_session(
                    session, campaign_id,
                )

            recap = await self.recap_generator.generate(
                campaign_id=campaign_id,
                previous_save=previous_save,
                current_payload=current_payload,
            )
            return recap

        except Exception as exc:
            logger.warning("Recap generation failed: {}", exc)
            return {
                "version": 1,
                "baseline": False,
                "from_save_id": None,
                "to_save_id": None,
                "generated_at": datetime.now(UTC).isoformat(),
                "language": "zh-CN",
                "summary": "存档完成，暂无法生成剧情摘要。",
                "source": {"mode": "failed", "error": str(exc)},
            }

    async def _regenerate_recap(
        self, campaign_id: str, slot: int
    ) -> dict[str, Any]:
        """Re-generate recap for an existing save."""
        if self.recap_generator is None:
            return {"error": "no_recap_generator", "detail": "RecapGenerator not available"}

        try:
            with self.database.transaction() as session:
                save = self._service._save(session, campaign_id, slot)
                payload = dict(save.snapshot_json)
                previous_save = (
                    session.get(CampaignSave, save.parent_save_id)
                    if save.parent_save_id
                    else None
                )

            recap = await self.recap_generator.generate(
                campaign_id=campaign_id,
                previous_save=previous_save,
                current_payload=payload,
            )

            result = self._service.regenerate_recap(campaign_id, slot, recap)
            self._index_memory_actions(result.memory_actions)
            return asdict(result)

        except (SnapshotNotFoundError, CampaignNotFoundError) as exc:
            return {"error": type(exc).__name__, "detail": str(exc)}
        except Exception as exc:
            logger.warning("Recap regeneration failed: {}", exc)
            return {"error": "recap_regeneration_failed", "detail": str(exc)}

    def _index_memory_actions(self, actions: list[dict] | None) -> None:
        revision_ids = [
            str(action["revision_id"])
            for action in actions or []
            if action.get("action") == "upsert" and action.get("revision_id")
        ]
        if not revision_ids:
            return
        try:
            from nanobot.dnd.memory_search import CampaignMemorySearchService

            CampaignMemorySearchService(self.database).index_revision_ids(revision_ids)
        except Exception as exc:
            logger.warning("Campaign memory vector indexing failed: {}", exc)
