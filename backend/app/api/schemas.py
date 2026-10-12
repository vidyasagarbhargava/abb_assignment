"""Public API contract (the only types the Angular client generates from)."""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    ApprovalStatus,
    DeploymentKind,
    DeploymentStatus,
    Environment,
    FailureClass,
    MonitoringStatus,
    SimulatedFailure,
    Stage,
)

T = TypeVar("T")
SLUG = r"^[a-z0-9][a-z0-9._-]{1,126}$"
SEMVER = r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$"


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    correlation_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---- registry -----------------------------------------------------------------------------
class ModelCreate(Request):
    model_id: str = Field(pattern=SLUG, examples=["pump-failure-predictor"])
    name: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelOut(Response):
    model_id: str
    name: str
    owner: str
    description: str | None
    tags: list[str]
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_by: str
    created_at: datetime
    updated_at: datetime


class VersionCreate(Request):
    version: str = Field(pattern=SEMVER, examples=["1.0.0"])
    framework: str = Field(min_length=1, max_length=64)
    algorithm: str | None = Field(default=None, max_length=128)
    runtime: str | None = Field(default=None, description="Serving runtime adapter name")
    artifact_uri: str = Field(min_length=3, max_length=1024, examples=["s3://models/pump/1.0.0"])
    training_data_ref: str | None = Field(default=None, max_length=1024)
    tags: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VersionOut(Response):
    model_id: str
    version: str
    framework: str
    algorithm: str | None
    runtime: str
    artifact_uri: str
    training_data_ref: str | None
    tags: list[str]
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    stage: Stage
    approval_status: ApprovalStatus
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime


class DecisionBody(Request):
    reason: str | None = Field(default=None, max_length=2000)


class RejectBody(Request):
    reason: str = Field(min_length=3, max_length=2000)


# ---- deployments --------------------------------------------------------------------------
class DeploymentCreate(Request):
    model_id: str
    version: str
    environment: Environment
    reason: str | None = Field(default=None, max_length=2000)
    simulate_failure: SimulatedFailure = SimulatedFailure.NONE
    expected_current_deployment_id: str | None = Field(
        default=None,
        description="Optimistic promotion guard. Send the deployment id you believe is "
        "serving (null = nothing serving). Omit the field to skip the check.",
    )


class RollbackBody(Request):
    reason: str | None = Field(default=None, max_length=2000)


class DeploymentOut(Response):
    id: str
    model_id: str
    version: str
    environment: Environment
    kind: DeploymentKind
    status: DeploymentStatus
    requested_by: str
    rollback_of_id: str | None
    reason: str | None
    attempt_count: int
    external_ref: str | None
    failure_class: FailureClass | None
    failure_message: str | None
    correlation_id: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class DeploymentEventOut(Response):
    id: int
    deployment_id: str
    model_id: str
    version: str
    environment: Environment
    event: str
    from_status: DeploymentStatus | None
    to_status: DeploymentStatus | None
    failure_class: FailureClass | None
    detail: dict[str, Any]
    correlation_id: str | None
    created_at: datetime


class AuditOut(Response):
    id: int
    actor: str
    actor_role: str
    action: str
    entity_type: str
    entity_id: str
    model_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    correlation_id: str | None
    created_at: datetime


# ---- monitoring ---------------------------------------------------------------------------
class MetricPointIn(Request):
    version: str = Field(pattern=SEMVER)
    environment: Environment
    ts: datetime
    latency_ms: float = Field(ge=0)
    throughput_rpm: float = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    drift_score: float = Field(ge=0)
    availability: float = Field(ge=0, le=100)


class MetricsIngest(Request):
    points: list[MetricPointIn] = Field(min_length=1)


class MetricsIngestResult(BaseModel):
    submitted: int
    inserted: int


class SummaryOut(Response):
    count: int
    latency_avg_ms: float
    latency_p95_ms: float
    throughput_avg_rpm: float
    error_rate_avg: float
    quality_avg: float
    drift_avg: float
    availability_avg: float
    last_successful_inference: datetime | None


class SeriesBucketOut(Response):
    start: datetime
    summary: SummaryOut


class MetricsOut(Response):
    model_id: str
    window_start: datetime | None
    window_end: datetime | None
    status: MonitoringStatus
    summary: SummaryOut | None
    series: list[SeriesBucketOut]
    truncated: bool


class CompareSide(BaseModel):
    version: str
    status: MonitoringStatus
    summary: SummaryOut | None


class CompareOut(BaseModel):
    base: CompareSide
    candidate: CompareSide
    delta: dict[str, float] | None


class HealthOut(BaseModel):
    status: str
    checks: dict[str, Any] = Field(default_factory=dict)
