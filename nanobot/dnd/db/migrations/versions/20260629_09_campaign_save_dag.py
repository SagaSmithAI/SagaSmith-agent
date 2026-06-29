"""Add save DAG lineage and campaign memory revisions.

Revision ID: 20260629_09
Revises: 20260627_08
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20260629_09"
down_revision = "20260627_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    save_columns = {column["name"] for column in inspector.get_columns("campaign_saves")}

    with op.batch_alter_table("campaign_saves") as batch:
        if "parent_save_id" not in save_columns:
            batch.add_column(sa.Column("parent_save_id", sa.String(), nullable=True))
            batch.create_foreign_key(
                "fk_campaign_saves_parent_save_id",
                "campaign_saves",
                ["parent_save_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index(
                "ix_campaign_saves_parent_save_id",
                ["parent_save_id"],
            )
        if "depth" not in save_columns:
            batch.add_column(
                sa.Column("depth", sa.Integer(), nullable=False, server_default="0")
            )

    _reset_legacy_memory_table(bind)
    tables = set(sa.inspect(bind).get_table_names())
    if "campaign_timeline_heads" not in tables:
        op.create_table(
            "campaign_timeline_heads",
            sa.Column(
                "campaign_id",
                sa.String(),
                sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "active_save_id",
                sa.String(),
                sa.ForeignKey("campaign_saves.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_campaign_timeline_heads_active_save_id",
            "campaign_timeline_heads",
            ["active_save_id"],
        )

    if "campaign_save_ancestors" not in tables:
        op.create_table(
            "campaign_save_ancestors",
            sa.Column(
                "descendant_save_id",
                sa.String(),
                sa.ForeignKey("campaign_saves.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "ancestor_save_id",
                sa.String(),
                sa.ForeignKey("campaign_saves.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "campaign_id",
                sa.String(),
                sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("distance", sa.Integer(), nullable=False),
        )
        op.create_index(
            "ix_campaign_save_ancestors_campaign_id",
            "campaign_save_ancestors",
            ["campaign_id"],
        )
        op.create_index(
            "ix_campaign_save_ancestors_ancestor",
            "campaign_save_ancestors",
            ["ancestor_save_id"],
        )

    if "campaign_memory_revisions" not in tables:
        op.create_table(
            "campaign_memory_revisions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "campaign_id",
                sa.String(),
                sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "memory_id",
                sa.String(),
                sa.ForeignKey("campaign_memories.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "save_id",
                sa.String(),
                sa.ForeignKey("campaign_saves.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("operation", sa.String(), nullable=False, server_default="set"),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
            sa.Column("status", sa.String(), nullable=False, server_default="candidate"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "memory_id",
                "save_id",
                name="uq_campaign_memory_revision_save",
            ),
        )
        op.create_index(
            "ix_campaign_memory_revisions_campaign_id",
            "campaign_memory_revisions",
            ["campaign_id"],
        )
        op.create_index(
            "ix_campaign_memory_revisions_memory_id",
            "campaign_memory_revisions",
            ["memory_id"],
        )
        op.create_index(
            "ix_campaign_memory_revisions_save_id",
            "campaign_memory_revisions",
            ["save_id"],
        )
        op.create_index(
            "ix_campaign_memory_revisions_created_at",
            "campaign_memory_revisions",
            ["created_at"],
        )

    _backfill_linear_dag(bind)


def _reset_legacy_memory_table(bind: sa.Connection) -> None:
    """Replace the pre-DAG mutable memory table; old memory data is not retained."""
    inspector = sa.inspect(bind)
    if "campaign_memories" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("campaign_memories")}
    if "text" not in columns:
        return

    op.drop_table("campaign_memories")
    op.create_table(
        "campaign_memories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.String(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("fact_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "campaign_id",
            "entity_type",
            "entity_id",
            "fact_type",
            name="uq_campaign_memory_entity_fact",
        ),
    )
    op.create_index(
        "ix_campaign_memories_campaign_id",
        "campaign_memories",
        ["campaign_id"],
    )


def _backfill_linear_dag(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, campaign_id, slot FROM campaign_saves "
            "ORDER BY campaign_id, slot"
        )
    ).mappings()
    by_campaign: dict[str, list[str]] = {}
    for row in rows:
        by_campaign.setdefault(str(row["campaign_id"]), []).append(str(row["id"]))

    now = datetime.now(UTC).replace(tzinfo=None)
    for campaign_id, save_ids in by_campaign.items():
        ancestors: dict[str, list[str]] = {}
        for depth, save_id in enumerate(save_ids):
            parent_id = save_ids[depth - 1] if depth else None
            bind.execute(
                sa.text(
                    "UPDATE campaign_saves "
                    "SET parent_save_id = :parent_id, depth = :depth WHERE id = :save_id"
                ),
                {"parent_id": parent_id, "depth": depth, "save_id": save_id},
            )
            lineage = [save_id]
            if parent_id:
                lineage.extend(ancestors[parent_id])
            ancestors[save_id] = lineage
            for distance, ancestor_id in enumerate(lineage):
                bind.execute(
                    sa.text(
                        "INSERT INTO campaign_save_ancestors "
                        "(descendant_save_id, ancestor_save_id, campaign_id, distance) "
                        "VALUES (:descendant, :ancestor, :campaign_id, :distance)"
                    ),
                    {
                        "descendant": save_id,
                        "ancestor": ancestor_id,
                        "campaign_id": campaign_id,
                        "distance": distance,
                    },
                )

        bind.execute(
            sa.text(
                "INSERT INTO campaign_timeline_heads "
                "(campaign_id, active_save_id, created_at, updated_at) "
                "VALUES (:campaign_id, :active_save_id, :created_at, :updated_at)"
            ),
            {
                "campaign_id": campaign_id,
                "active_save_id": save_ids[-1],
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    op.drop_table("campaign_memory_revisions")
    op.drop_table("campaign_save_ancestors")
    op.drop_table("campaign_timeline_heads")
    with op.batch_alter_table("campaign_saves") as batch:
        batch.drop_index("ix_campaign_saves_parent_save_id")
        batch.drop_constraint("fk_campaign_saves_parent_save_id", type_="foreignkey")
        batch.drop_column("depth")
        batch.drop_column("parent_save_id")
