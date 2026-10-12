"""Persistence model. Every table is tenant-scoped; concurrency is guarded by optimistic
version columns and by a partial unique index that admits one in-flight deployment per slot.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow

_ACTIVE_SQL = text("status IN ('REQUESTED','VALIDATING','DEPLOYING')")
# SQLite cannot autoincrement BIGINT primary keys; this keeps one definition for both dialects.
_BigPk = BigInteger().with_variant(Integer(), "sqlite")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def _default_id() -> str:
    # SQLAlchemy passes a context arg to defaults that accept one; keep this zero-arg.
    return new_id()


class Model(Base):
    __tablename__ = "models"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "model_id"], ["models.tenant_id", "models.model_id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("tenant_id", "model_id", "version", name="uq_version_per_model"),
        Index("ix_versions_model", "tenant_id", "model_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_default_id)
    tenant_id: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    framework: Mapped[str] = mapped_column(String(64))
    algorithm: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runtime: Mapped[str] = mapped_column(String(64))
    artifact_uri: Mapped[str] = mapped_column(String(1024))
    training_data_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(16), default="DRAFT")
    approval_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_by: Mapped[str] = mapped_column(String(128))
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": row_version}


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "model_id"], ["models.tenant_id", "models.model_id"], ondelete="RESTRICT"
        ),
        # Promotion-conflict guard: at most one in-flight deployment per (tenant, model, env).
        Index(
            "uq_active_deployment_per_slot",
            "tenant_id",
            "model_id",
            "environment",
            unique=True,
            sqlite_where=_ACTIVE_SQL,
            postgresql_where=_ACTIVE_SQL,
        ),
        # Idempotency guard: a client key maps to exactly one deployment per tenant.
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_idempotency_key"),
        Index("ix_deployments_list", "tenant_id", "model_id", "created_at"),
        Index("ix_deployments_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str] = mapped_column(String(128))
    version_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16), default="DEPLOY")
    status: Mapped[str] = mapped_column(String(16), default="REQUESTED")
    requested_by: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    rollback_of_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulate_failure: Mapped[str] = mapped_column(String(24), default="NONE")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    external_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": row_version}


class EnvironmentSlot(Base):
    """What is serving right now in (tenant, model, environment). The promotion pointer."""

    __tablename__ = "environment_slots"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    current_deployment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": row_version}


class Job(Base):
    """Durable work queue row (transactional outbox: created in the same commit as the
    deployment it drives)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_poll", "status", "run_after"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_default_id)
    tenant_id: Mapped[str] = mapped_column(String(64))
    deployment_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class DeploymentEvent(Base):
    __tablename__ = "deployment_events"
    __table_args__ = (
        Index("ix_dep_events_dep", "deployment_id", "id"),
        Index("ix_dep_events_model", "tenant_id", "model_id", "id"),
    )

    id: Mapped[int] = mapped_column(_BigPk, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    deployment_id: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(16))
    event: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditEvent(Base):
    """Append-only. ORM-level guards below; in Postgres also REVOKE UPDATE/DELETE (see docs)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_lookup", "tenant_id", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(_BigPk, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    actor_role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(160))
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def _audit_is_immutable(*_: object) -> None:
    raise RuntimeError("audit_events is append-only")


class MetricPoint(Base):
    """Raw monitoring samples. Partition by month on Postgres at scale (see docs/architecture)."""

    __tablename__ = "metric_points"
    __table_args__ = (
        Index("ix_metrics_series", "tenant_id", "model_id", "environment", "ts"),
        UniqueConstraint("tenant_id", "model_id", "version", "environment", "ts", name="uq_metric_sample"),
    )

    id: Mapped[int] = mapped_column(_BigPk, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    latency_ms: Mapped[float]
    throughput_rpm: Mapped[float]
    error_rate: Mapped[float]
    quality_score: Mapped[float]
    drift_score: Mapped[float]
    availability: Mapped[float]
