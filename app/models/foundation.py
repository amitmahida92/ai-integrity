import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

PROVIDER_CHECK = "provider in ('hubspot', 'google_calendar', 'stripe')"
REQUESTED_MODE_CHECK = "requested_mode in ('full', 'incremental')"


class NormalizedRecord(Base):
    __tablename__ = "normalized_records"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "entity_type",
            "external_id",
            name="uq_normalized_records_provider_entity_type_external_id",
        ),
        CheckConstraint(PROVIDER_CHECK, name="provider"),
        Index(
            "ix_normalized_records_provider_entity_type_source_updated_at",
            "provider",
            "entity_type",
            "source_updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canonical_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"
    __table_args__ = (
        UniqueConstraint("provider", name="uq_sync_checkpoints_provider"),
        CheckConstraint(PROVIDER_CHECK, name="provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(REQUESTED_MODE_CHECK, name="requested_mode"),
        CheckConstraint(
            "status in ('running', 'succeeded', 'completed_with_errors', 'failed')",
            name="status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    requested_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_sources: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source_results: Mapped[list["SyncSourceResult"]] = relationship(
        back_populates="sync_run",
        cascade="all, delete-orphan",
    )


class SyncSourceResult(Base):
    __tablename__ = "sync_source_results"
    __table_args__ = (
        CheckConstraint(PROVIDER_CHECK, name="provider"),
        CheckConstraint(REQUESTED_MODE_CHECK, name="requested_mode"),
        CheckConstraint(
            "effective_mode in ('full', 'incremental', 'recovery_full')",
            name="effective_mode",
        ),
        CheckConstraint("status in ('running', 'succeeded', 'failed')", name="status"),
        Index("ix_sync_source_results_sync_run_id", "sync_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    records_fetched: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    records_upserted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    records_rejected: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    pages_fetched: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    checkpoint_before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checkpoint_after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sync_run: Mapped[SyncRun] = relationship(back_populates="source_results")


class NormalizedFinancialRecord(Base):
    __tablename__ = "normalized_financial_records"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "source_entity_type",
            "external_id",
            name="uq_normalized_financial_records_source_name_source_entity_type_external_id",
        ),
        Index("ix_normalized_financial_records_occurred_at", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    raw_status: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    customer_reference: Mapped[str | None] = mapped_column(String(255))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RevenueStatusAllowlist(Base):
    __tablename__ = "revenue_status_allowlist"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "source_entity_type",
            "raw_status",
            name="uq_revenue_status_allowlist_source_name_source_entity_type_raw_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_status: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_status: Mapped[str] = mapped_column(String(128), nullable=False)
    counts_as_collected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
