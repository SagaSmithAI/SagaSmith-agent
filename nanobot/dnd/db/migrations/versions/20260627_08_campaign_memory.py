"""Add campaign_memories table for narrative long-term memory.

Revision ID: 20260627_08
Revises: 20260624_07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260627_08"
down_revision = "20260624_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "campaign_memories" not in tables:
        op.create_table(
            "campaign_memories",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "campaign_id",
                sa.String(),
                sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
            sa.Column("status", sa.String(), nullable=False, server_default="candidate"),
            sa.Column("entity_type", sa.String(), nullable=True),
            sa.Column("entity_id", sa.String(), nullable=True),
            sa.Column("fact_type", sa.String(), nullable=True),
            sa.Column("supersedes", sa.String(), nullable=True),
            sa.Column("source_save_id", sa.String(), nullable=True),
            sa.Column("score", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "campaign_id", "entity_type", "entity_id", "fact_type",
                name="uq_campaign_memory_entity_fact",
            ),
        )
        op.create_index(
            "ix_campaign_memories_campaign_status",
            "campaign_memories",
            ["campaign_id", "status"],
        )


def downgrade() -> None:
    op.drop_table("campaign_memories")
