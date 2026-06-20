"""Add Problem Statement 2 financial metric tables.

Revision ID: 202606200001
Revises: 202606180001
Create Date: 2026-06-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "202606200001"
down_revision: str | None = "202606180001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "normalized_financial_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("source_entity_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("raw_status", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("customer_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "raw_payload",
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
        sa.UniqueConstraint(
            "source_name",
            "source_entity_type",
            "external_id",
            name=op.f(
                "uq_normalized_financial_records_source_name_source_entity_type_external_id"
            ),
        ),
    )
    op.create_index(
        op.f("ix_normalized_financial_records_occurred_at"),
        "normalized_financial_records",
        ["occurred_at"],
    )

    op.create_table(
        "revenue_status_allowlist",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("source_entity_type", sa.String(length=64), nullable=False),
        sa.Column("raw_status", sa.String(length=128), nullable=False),
        sa.Column("canonical_status", sa.String(length=128), nullable=False),
        sa.Column("counts_as_collected", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_name",
            "source_entity_type",
            "raw_status",
            name=op.f("uq_revenue_status_allowlist_source_name_source_entity_type_raw_status"),
        ),
    )


def downgrade() -> None:
    op.drop_table("revenue_status_allowlist")
    op.drop_index(
        op.f("ix_normalized_financial_records_occurred_at"),
        table_name="normalized_financial_records",
    )
    op.drop_table("normalized_financial_records")
