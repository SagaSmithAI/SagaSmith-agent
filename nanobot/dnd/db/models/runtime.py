"""Combat, save, summary, and event state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from nanobot.dnd.db.database import Base
from nanobot.dnd.db.models.common import TimestampMixin, utc_now


class Combat(TimestampMixin, Base):
    __tablename__ = "combats"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String, default="")
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    current_turn: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    result: Mapped[str | None] = mapped_column(String)
    environment_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    state_version: Mapped[int] = mapped_column(Integer, default=1)


class CampaignSave(TimestampMixin, Base):
    __tablename__ = "campaign_saves"
    __table_args__ = (
        UniqueConstraint("campaign_id", "slot", name="uq_campaign_save_slot"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    slot: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String, default="")
    chapter: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    snapshot_format: Mapped[str] = mapped_column(
        String, default="dnd-campaign-snapshot"
    )
    snapshot_hash: Mapped[str] = mapped_column(String, default="")
    created_by: Mapped[str | None] = mapped_column(String, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    parent_save_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_saves.id", ondelete="SET NULL"), index=True
    )
    depth: Mapped[int] = mapped_column(Integer, default=0)


class CampaignTimelineHead(TimestampMixin, Base):
    """Current save lineage used by the mutable campaign runtime."""

    __tablename__ = "campaign_timeline_heads"

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    active_save_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaign_saves.id", ondelete="SET NULL"), index=True
    )


class CampaignSaveAncestor(Base):
    """Transitive closure for fast save-lineage queries."""

    __tablename__ = "campaign_save_ancestors"
    __table_args__ = (
        Index("ix_campaign_save_ancestors_ancestor", "ancestor_save_id"),
    )

    descendant_save_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_saves.id", ondelete="CASCADE"), primary_key=True
    )
    ancestor_save_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_saves.id", ondelete="CASCADE"), primary_key=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    distance: Mapped[int] = mapped_column(Integer, nullable=False)


class PlotSummary(TimestampMixin, Base):
    __tablename__ = "plot_summaries"
    __table_args__ = (
        UniqueConstraint("campaign_id", "scope", "scope_id", name="uq_plot_summary_scope"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String, default="campaign")
    scope_id: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    open_threads: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


class CampaignEvent(Base):
    __tablename__ = "campaign_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    content: Mapped[str] = mapped_column(Text)
    actors: Mapped[list[str]] = mapped_column(JSON, default=list)
    visibility: Mapped[str] = mapped_column(String, default="party", index=True)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class CampaignMemory(TimestampMixin, Base):
    """Stable identity of one campaign-scoped narrative fact."""

    __tablename__ = "campaign_memories"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "entity_type", "entity_id", "fact_type",
            name="uq_campaign_memory_entity_fact",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    fact_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class CampaignMemoryRevision(Base):
    """Immutable-per-save value of a logical campaign memory fact."""

    __tablename__ = "campaign_memory_revisions"
    __table_args__ = (
        UniqueConstraint("memory_id", "save_id", name="uq_campaign_memory_revision_save"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_memories.id", ondelete="CASCADE"), index=True
    )
    save_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_saves.id", ondelete="CASCADE"), index=True
    )
    operation: Mapped[str] = mapped_column(String, default="set")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String, default="medium")
    status: Mapped[str] = mapped_column(String, default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
