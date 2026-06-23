"""World state management — faction relations, NPC attitudes, quests, locations.

Ports the deprecated dnd-engine state/world.py logic to the SQL database.
"""

from __future__ import annotations

from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models import WorldState


class WorldService:
    """Read and update the mutable world-state JSON for a campaign."""

    def __init__(self, database: Database) -> None:
        self.database = database

    # ── Internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _attitude_label(score: int) -> str:
        if score >= 3:
            return "allied"
        elif score >= 1:
            return "friendly"
        elif score >= -1:
            return "neutral"
        elif score >= -3:
            return "hostile"
        else:
            return "vengeful"

    _LABEL_ZH: dict[int, str] = {
        5: "盟友", 4: "盟友", 3: "盟友",
        2: "友好", 1: "友好",
        0: "中立", -1: "中立",
        -2: "敌对", -3: "敌对",
        -4: "死仇", -5: "死仇",
    }

    def _load(self, session, campaign_id: str) -> dict:
        row = session.get(WorldState, f"world_{campaign_id}")
        if row is None:
            # Fallback: search by campaign_id
            from sqlalchemy import select as _select
            row = session.scalar(
                _select(WorldState).where(WorldState.campaign_id == campaign_id)
            )
        if row is None:
            raise ValueError(f"world state not found for campaign {campaign_id}")
        state = dict(row.state_json or {})
        state.setdefault("faction_relations", {})
        state.setdefault("key_npc_status", {})
        state.setdefault("discovered_locations", [])
        state.setdefault("quest_progress", {"完成": [], "进行中": [], "待触发": [], "已失败": []})
        state.setdefault("current_chapter", 0)
        state.setdefault("current_scene", "")
        state.setdefault("day_in_game", 1)
        return state

    def _save(self, session, campaign_id: str, state: dict) -> None:
        from sqlalchemy import select as _select
        row = session.scalar(
            _select(WorldState).where(WorldState.campaign_id == campaign_id)
        )
        if row is None:
            row = session.get(WorldState, f"world_{campaign_id}")
        if row is not None:
            row.state_json = state
            row.state_version = (row.state_version or 0) + 1

    # ── Faction relations ──────────────────────────────────────────────

    def update_faction(
        self,
        campaign_id: str,
        faction_name: str,
        delta: int,
        *,
        note: str = "",
    ) -> dict:
        """Adjust a faction's relationship score and return the result."""
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
            current = state["faction_relations"].get(faction_name, 0)
            current += delta
            state["faction_relations"][faction_name] = current
            self._save(session, campaign_id, state)
        return {
            "faction_name": faction_name,
            "score": current,
            "attitude": self._attitude_label(current),
            "delta": delta,
            "note": note,
        }

    def get_factions(self, campaign_id: str) -> dict[str, dict]:
        """Return all faction relations with attitude labels."""
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
        return {
            name: {"score": score, "attitude": self._attitude_label(score)}
            for name, score in state["faction_relations"].items()
        }

    # ── NPC status / attitude ──────────────────────────────────────────

    def update_npc_attitude(
        self,
        campaign_id: str,
        npc_name: str,
        delta: int,
        *,
        note: str = "",
    ) -> dict:
        """Adjust an NPC's attitude score.  Uses the same scale as factions."""
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
            npc_data = state["key_npc_status"].get(npc_name, {})
            if isinstance(npc_data, str):
                # Legacy free-text status → migrate to structured dict.
                npc_data = {"status": npc_data, "attitude": 0}
            current = npc_data.get("attitude", 0)
            current += delta
            npc_data["attitude"] = current
            if note:
                npc_data.setdefault("history", []).append(
                    f"{delta:+d} ({self._attitude_label(current)}): {note}"
                )
            state["key_npc_status"][npc_name] = npc_data
            self._save(session, campaign_id, state)
        return {
            "npc_name": npc_name,
            "attitude_score": current,
            "attitude": self._attitude_label(current),
            "delta": delta,
            "note": note,
        }

    def set_npc_status(
        self,
        campaign_id: str,
        npc_name: str,
        *,
        status: str | None = None,
        attitude: int | None = None,
        trust: int | None = None,
        fear: int | None = None,
        note: str | None = None,
        location: str | None = None,
    ) -> dict:
        """Set one or more fields on a key NPC's status record."""
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
            npc_data = state["key_npc_status"].get(npc_name, {})
            if isinstance(npc_data, str):
                npc_data = {"status": npc_data}
            if status is not None:
                npc_data["status"] = status
            if attitude is not None:
                npc_data["attitude"] = attitude
            if trust is not None:
                npc_data["trust"] = trust
            if fear is not None:
                npc_data["fear"] = fear
            if note is not None:
                npc_data.setdefault("history", []).append(note)
            if location is not None:
                npc_data["location"] = location
            state["key_npc_status"][npc_name] = npc_data
            self._save(session, campaign_id, state)
        return {"npc_name": npc_name, **npc_data}

    def get_npc_status(self, campaign_id: str, npc_name: str) -> dict | None:
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
        return state["key_npc_status"].get(npc_name)

    def list_npc_statuses(self, campaign_id: str) -> dict[str, dict]:
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
        return dict(state["key_npc_status"])

    # ── Quests ─────────────────────────────────────────────────────────

    def update_quest(self, campaign_id: str, quest_name: str, new_status: str) -> dict:
        statuses = {"完成", "进行中", "待触发", "已失败"}
        if new_status not in statuses:
            raise ValueError(f"status must be one of {statuses}")
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
            old_status = None
            for key in statuses:
                if quest_name in state["quest_progress"].get(key, []):
                    old_status = key
                    state["quest_progress"][key].remove(quest_name)
                    break
            state["quest_progress"].setdefault(new_status, []).append(quest_name)
            self._save(session, campaign_id, state)
        return {"quest_name": quest_name, "old_status": old_status, "new_status": new_status}

    def get_quests(self, campaign_id: str) -> dict:
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
        return dict(state["quest_progress"])

    # ── Locations ──────────────────────────────────────────────────────

    def discover_location(self, campaign_id: str, location_name: str) -> dict:
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
            if location_name not in state["discovered_locations"]:
                state["discovered_locations"].append(location_name)
            self._save(session, campaign_id, state)
        return {"location_name": location_name, "discovered": True}

    # ── Time ───────────────────────────────────────────────────────────

    def advance_day(self, campaign_id: str) -> int:
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
            state["day_in_game"] = state.get("day_in_game", 1) + 1
            self._save(session, campaign_id, state)
        return state["day_in_game"]

    # ── Summary ────────────────────────────────────────────────────────

    def get_summary(self, campaign_id: str) -> str:
        with self.database.transaction() as session:
            state = self._load(session, campaign_id)
        lines = []
        lines.append(
            f"Ch.{state.get('current_chapter', '?')} "
            f"{state.get('current_scene', '?')} "
            f"Day #{state.get('day_in_game', 1)}"
        )
        factions = state.get("faction_relations", {})
        if factions:
            parts = []
            for name, val in sorted(factions.items(), key=lambda x: -x[1]):
                label = self._LABEL_ZH.get(val, str(val))
                parts.append(f"{name}({label})")
            lines.append(f"Factions: {' '.join(parts)}")
        quests = state.get("quest_progress", {})
        active = quests.get("进行中", [])
        if active:
            lines.append(f"Active: {' / '.join(active)}")
        npcs = state.get("key_npc_status", {})
        important = {}
        for name, data in npcs.items():
            if isinstance(data, dict):
                att = data.get("attitude", 0)
                status = data.get("status", "")
                if att <= -1 or "待" in status or "未" in status:
                    important[name] = self._attitude_label(att)
            elif isinstance(data, str) and ("待" in data or "未" in data or "敌对" in data):
                important[name] = data
        if important:
            parts = [f"{n}({s})" for n, s in important.items()]
            lines.append(f"Key NPCs: {' '.join(parts)}")
        return " | ".join(lines)

    def get_full_state(self, campaign_id: str) -> dict:
        with self.database.transaction() as session:
            return dict(self._load(session, campaign_id))
