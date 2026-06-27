"""Recap generation for campaign snapshots using LLM two-layer extraction."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

from loguru import logger
from sqlalchemy import select

from nanobot.dnd.db.models.runtime import CampaignEvent, CampaignSave
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.dnd.db.database import Database
    from nanobot.providers.base import LLMProvider


def _json_repair(text: str) -> str:
    """Attempt basic JSON repair for common LLM output issues."""
    text = text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


class RecapGenerator:
    """Two-layer recap: structured extraction + NL summary in a single LLM call."""

    def __init__(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model

    async def generate(
        self,
        campaign_id: str,
        previous_save: CampaignSave | None,
        current_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce the full recap dict.

        Args:
            campaign_id: Target campaign ID.
            previous_save: The save immediately before this one, or None for baseline.
            current_payload: The current snapshot payload from capture_from_session().

        Returns:
            Recap dict ready to attach to snapshot_json["recap"].
        """
        baseline = previous_save is None

        # Build input context
        campaign_context = self._build_campaign_context(current_payload, baseline)
        events_text = ""
        state_diff_text = ""

        if not baseline and previous_save is not None:
            prev_payload = previous_save.snapshot_json or {}
            prev_recap = prev_payload.get("recap", {})
            previous_recap_summary = prev_recap.get("summary", "(no previous recap)") if prev_recap else "(none)"
            events_text = self._build_event_delta_text(
                current_payload, prev_payload,
            )
            state_diff_text = self._build_state_diff_text(
                current_payload, prev_payload,
            )
        else:
            previous_recap_summary = ""

        # Render prompt
        prompt = render_template(
            "agent/recap_generation.md",
            strip=True,
            baseline=baseline,
            campaign_context=campaign_context,
            previous_recap_summary=previous_recap_summary,
            events_text=events_text,
            state_diff_text=state_diff_text,
        )

        recap_base = {
            "version": 1,
            "baseline": baseline,
            "from_save_id": previous_save.id if previous_save else None,
            "to_save_id": None,  # filled in by caller after save row exists
            "generated_at": datetime.now(UTC).isoformat(),
            "language": "zh-CN",
            "source": {
                "mode": "baseline" if baseline else "delta_from_previous_snapshot",
                "previous_save_id": previous_save.id if previous_save else None,
            },
        }

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "Generate the recap JSON now."},
                ],
                tools=None,
                tool_choice=None,
            )
        except Exception as exc:
            logger.warning("Recap LLM call failed: {}", exc)
            recap_base.update({
                "summary": "存档完成，暂无法生成剧情摘要。",
                "source": {"mode": "failed", "error": str(exc)},
            })
            return recap_base

        if response.finish_reason == "error":
            logger.warning("Recap LLM returned error finish_reason")
            recap_base.update({
                "summary": response.content or "存档完成，暂无法生成剧情摘要。",
                "source": {"mode": "failed", "error": "LLM finish_reason=error"},
            })
            return recap_base

        return self._parse_response(response.content or "", recap_base)

    def _parse_response(
        self, raw_content: str, recap_base: dict[str, Any]
    ) -> dict[str, Any]:
        """Parse LLM output into structured recap, with fallback."""
        cleaned = _json_repair(raw_content)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Recap LLM output is not valid JSON, using raw text as summary")
            return {
                **recap_base,
                "summary": raw_content[:400] if raw_content else "存档完成，暂无法生成剧情摘要。",
                "source": {**recap_base.get("source", {}), "mode": "json_parse_failed"},
            }

        if not isinstance(parsed, dict):
            return {
                **recap_base,
                "summary": str(parsed)[:400],
                "source": {**recap_base.get("source", {}), "mode": "json_parse_failed"},
            }

        summary = str(parsed.get("summary", ""))[:400]

        return {
            **recap_base,
            "summary": summary,
            "plot_progress": parsed.get("plot_progress", []) or [],
            "new_characters": parsed.get("new_characters", []) or [],
            "new_locations": parsed.get("new_locations", []) or [],
            "triggered_events": parsed.get("triggered_events", []) or [],
            "future_impact": parsed.get("future_impact", []) or [],
            "player_choices": parsed.get("player_choices", []) or [],
            "memory_candidates": parsed.get("memory_candidates", []) or [],
        }

    # -- Input builders -------------------------------------------------------

    @staticmethod
    def _build_campaign_context(
        payload: dict[str, Any], baseline: bool
    ) -> str:
        """Build a human-readable campaign context summary from snapshot payload."""
        campaign = payload.get("campaign", {})
        state = payload.get("state", {})

        world = state.get("world_states", [])
        world_json = world[0].get("state_json", {}) if world else {}

        parties = state.get("parties", [])
        party = parties[0] if parties else {}

        characters = state.get("characters", [])
        char_lines = []
        for c in characters:
            hp = c.get("hp", "?")
            max_hp = c.get("max_hp", "?")
            level = c.get("level", "?")
            name = c.get("name", "?")
            char_lines.append(f"  - {name} Lv{level} HP {hp}/{max_hp}")

        plot = state.get("plot_summaries", [])
        plot_summary = plot[0].get("summary", "") if plot else ""
        open_threads = plot[0].get("open_threads", []) if plot else []

        scene_states = state.get("scene_states", [])
        scene_info = ""
        for sc in scene_states:
            scene_info += (
                f"  - Scene {sc.get('scene_id', '?')}: "
                f"room={sc.get('current_room', '?')}, "
                f"explored={sc.get('explored_percent', 0)}%\n"
            )

        lines = [
            f"Campaign: {campaign.get('name', '?')}",
            f"Module: {campaign.get('module_name', 'none')}",
            f"Chapter: {world_json.get('current_chapter', '?')}",
            f"Scene: {world_json.get('current_scene', '?')}",
            f"Day in game: {world_json.get('day_in_game', '?')}",
            f"Party location: {party.get('location', '?')}",
            f"Party gold: {party.get('shared_gold', 0)}",
        ]

        if char_lines:
            lines.append("Characters:")
            lines.extend(char_lines)

        quests = world_json.get("quest_progress", {})
        if quests:
            active = quests.get("进行中", [])
            completed = quests.get("完成", [])
            if active:
                lines.append(f"Active quests: {', '.join(active)}")
            if completed:
                lines.append(f"Completed quests: {', '.join(completed)}")

        if plot_summary:
            lines.append(f"Plot summary: {plot_summary[:500]}")

        if open_threads:
            thread_names = [
                t.get("title", t) if isinstance(t, dict) else str(t)
                for t in open_threads
            ]
            lines.append(f"Open threads: {', '.join(thread_names)}")

        if scene_info:
            lines.append("Scene states:")
            lines.append(scene_info)

        return "\n".join(lines)

    @staticmethod
    def _build_event_delta_text(
        current_payload: dict[str, Any],
        previous_payload: dict[str, Any],
    ) -> str:
        """Extract and format campaign events that are new since the previous save."""
        prev_events = set()
        for e in previous_payload.get("state", {}).get("campaign_events", []):
            prev_events.add(e.get("id", ""))

        new_events = []
        for e in current_payload.get("state", {}).get("campaign_events", []):
            eid = e.get("id", "")
            if eid and eid not in prev_events:
                new_events.append(e)

        if not new_events:
            return "(no new events since last save)"

        lines = []
        for e in new_events:
            event_type = e.get("event_type", "unknown")
            content = e.get("content", "")[:300]
            actors = e.get("actors", [])
            importance = e.get("importance", 3)
            actor_str = f" [{', '.join(actors)}]" if actors else ""
            lines.append(
                f"[{event_type}](importance={importance}){actor_str}: {content}"
            )

        return "\n".join(lines)

    @staticmethod
    def _build_state_diff_text(
        current_payload: dict[str, Any],
        previous_payload: dict[str, Any],
    ) -> str:
        """Build a human-readable diff between two snapshot payloads."""
        diffs: list[str] = []

        cur_state = current_payload.get("state", {})
        prev_state = previous_payload.get("state", {})

        # World state diff
        cur_world = cur_state.get("world_states", [])
        prev_world = prev_state.get("world_states", [])
        if cur_world and prev_world:
            cw = cur_world[0].get("state_json", {})
            pw = prev_world[0].get("state_json", {})
            if cw.get("current_chapter") != pw.get("current_chapter"):
                diffs.append(
                    f"Chapter changed: {pw.get('current_chapter')} → "
                    f"{cw.get('current_chapter')}"
                )
            if cw.get("current_scene") != pw.get("current_scene"):
                diffs.append(
                    f"Scene changed: {pw.get('current_scene')} → "
                    f"{cw.get('current_scene')}"
                )
            if cw.get("day_in_game") != pw.get("day_in_game"):
                diffs.append(
                    f"Day advanced: {pw.get('day_in_game')} → "
                    f"{cw.get('day_in_game')}"
                )
            # Quest diff
            cq = cw.get("quest_progress", {})
            pq = pw.get("quest_progress", {})
            for bucket in ("完成", "进行中", "待触发"):
                cqs = set(cq.get(bucket, []))
                pqs = set(pq.get(bucket, []))
                added = cqs - pqs
                removed = pqs - cqs
                if added:
                    diffs.append(f"Quests now {bucket}: {', '.join(added)}")
                if removed:
                    diffs.append(
                        f"Quests no longer {bucket}: {', '.join(removed)}"
                    )

        # Party diff
        cur_party = cur_state.get("parties", [])
        prev_party = prev_state.get("parties", [])
        if cur_party and prev_party:
            cp = cur_party[0]
            pp = prev_party[0]
            if cp.get("location") != pp.get("location"):
                diffs.append(
                    f"Party moved: {pp.get('location')} → {cp.get('location')}"
                )
            cur_gold = cp.get("shared_gold", 0)
            prev_gold = pp.get("shared_gold", 0)
            if cur_gold != prev_gold:
                delta = cur_gold - prev_gold
                sign = "+" if delta > 0 else ""
                diffs.append(f"Gold changed: {prev_gold} → {cur_gold} ({sign}{delta})")

        # Character diffs
        cur_chars = {c.get("id"): c for c in cur_state.get("characters", [])}
        prev_chars = {c.get("id"): c for c in prev_state.get("characters", [])}
        for cid, cc in cur_chars.items():
            pc = prev_chars.get(cid)
            if not pc:
                diffs.append(f"New PC: {cc.get('name', '?')} joined")
                continue
            if cc.get("hp") != pc.get("hp"):
                diffs.append(
                    f"  {cc.get('name', '?')} HP: {pc.get('hp')} → {cc.get('hp')}"
                )
            if cc.get("level") != pc.get("level"):
                diffs.append(
                    f"  {cc.get('name', '?')} leveled up: "
                    f"{pc.get('level')} → {cc.get('level')}"
                )

        # Plot summary diff
        cur_plot = cur_state.get("plot_summaries", [])
        prev_plot = prev_state.get("plot_summaries", [])
        if cur_plot and prev_plot:
            cur_threads = {
                (t.get("title") if isinstance(t, dict) else str(t))
                for t in cur_plot[0].get("open_threads", [])
            }
            prev_threads = {
                (t.get("title") if isinstance(t, dict) else str(t))
                for t in prev_plot[0].get("open_threads", [])
            }
            new_threads = cur_threads - prev_threads
            closed = prev_threads - cur_threads
            if new_threads:
                diffs.append(f"New plot threads: {', '.join(new_threads)}")
            if closed:
                diffs.append(f"Closed plot threads: {', '.join(closed)}")

        if not diffs:
            return "(no significant state changes)"

        return "\n".join(diffs)

    # -- Database query helpers -----------------------------------------------

    @staticmethod
    async def collect_delta(
        database: Database,
        campaign_id: str,
        previous_save: CampaignSave | None,
        current_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect delta input for recap generation.

        Use this from the tool layer to gather everything needed by generate().
        Returns a dict with events + state_diff texts, suitable for passing
        to the prompt template.
        """
        baseline = previous_save is None
        prev_payload = previous_save.snapshot_json if previous_save else {}

        events_text = ""
        if not baseline and previous_save is not None:
            events_text = RecapGenerator._build_event_delta_text(
                current_payload, prev_payload,
            )

        state_diff_text = ""
        if not baseline and previous_save is not None:
            state_diff_text = RecapGenerator._build_state_diff_text(
                current_payload, prev_payload,
            )

        return {
            "baseline": baseline,
            "events_text": events_text,
            "state_diff_text": state_diff_text,
            "previous_save_id": previous_save.id if previous_save else None,
        }
