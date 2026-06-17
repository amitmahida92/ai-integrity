"""Initial Problem Statement 1 foundation.

Revision ID: 202606170001
Revises:
Create Date: 2026-06-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "202606170001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "normalized_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "canonical_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "provider in ('hubspot', 'google_calendar', 'stripe')",
            name=op.f("ck_normalized_records_provider"),
        ),
        sa.UniqueConstraint(
            "provider",
            "entity_type",
            "external_id",
            name=op.f("uq_normalized_records_provider_entity_type_external_id"),
        ),
    )
    op.create_index(
        op.f("ix_normalized_records_provider_entity_type_source_updated_at"),
        "normalized_records",
        ["provider", "entity_type", "source_updated_at"],
    )

    op.create_table(
        "sync_checkpoints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column(
            "checkpoint_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "provider in ('hubspot', 'google_calendar', 'stripe')",
            name=op.f("ck_sync_checkpoints_provider"),
        ),
        sa.UniqueConstraint("provider", name=op.f("uq_sync_checkpoints_provider")),
    )

    op.create_table(
        "sync_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("requested_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "requested_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "requested_mode in ('full', 'incremental')",
            name=op.f("ck_sync_runs_requested_mode"),
        ),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'completed_with_errors', 'failed')",
            name=op.f("ck_sync_runs_status"),
        ),
    )

    op.create_table(
        "sync_source_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("requested_mode", sa.String(length=32), nullable=False),
        sa.Column("effective_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("records_fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records_upserted", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("checkpoint_before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checkpoint_after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "provider in ('hubspot', 'google_calendar', 'stripe')",
            name=op.f("ck_sync_source_results_provider"),
        ),
        sa.CheckConstraint(
            "requested_mode in ('full', 'incremental')",
            name=op.f("ck_sync_source_results_requested_mode"),
        ),
        sa.CheckConstraint(
            "effective_mode in ('full', 'incremental', 'recovery_full')",
            name=op.f("ck_sync_source_results_effective_mode"),
        ),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'failed')",
            name=op.f("ck_sync_source_results_status"),
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["sync_runs.id"],
            name=op.f("fk_sync_source_results_sync_run_id_sync_runs"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_sync_source_results_sync_run_id"),
        "sync_source_results",
        ["sync_run_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sync_source_results_sync_run_id"), table_name="sync_source_results")
    op.drop_table("sync_source_results")
    op.drop_table("sync_runs")
    op.drop_table("sync_checkpoints")
    op.drop_index(
        op.f("ix_normalized_records_provider_entity_type_source_updated_at"),
        table_name="normalized_records",
    )
    op.drop_table("normalized_records")
