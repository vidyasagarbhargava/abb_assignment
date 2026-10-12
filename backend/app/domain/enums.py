from enum import StrEnum


class Stage(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"
    ARCHIVED = "ARCHIVED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DeploymentStatus(StrEnum):
    REQUESTED = "REQUESTED"
    VALIDATING = "VALIDATING"
    DEPLOYING = "DEPLOYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


ACTIVE_DEPLOYMENT_STATUSES = (
    DeploymentStatus.REQUESTED,
    DeploymentStatus.VALIDATING,
    DeploymentStatus.DEPLOYING,
)


class DeploymentKind(StrEnum):
    DEPLOY = "DEPLOY"
    ROLLBACK = "ROLLBACK"


class Environment(StrEnum):
    STAGING = "staging"
    PRODUCTION = "production"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    DEAD = "DEAD"


class FailureClass(StrEnum):
    """Operator-facing failure taxonomy; also a metrics label (bounded cardinality)."""

    POLICY = "POLICY"  # governance rule no longer satisfied; retry will not help
    RUNTIME_TRANSIENT = "RUNTIME_TRANSIENT"  # runtime 5xx / connectivity; retried automatically
    RUNTIME_PERMANENT = "RUNTIME_PERMANENT"  # runtime rejected the artifact; needs a human
    TIMEOUT = "TIMEOUT"  # runtime did not answer in time; retried automatically
    INTERNAL = "INTERNAL"  # our own bug or infrastructure failure


class SimulatedFailure(StrEnum):
    NONE = "NONE"
    TRANSIENT_ONCE = "TRANSIENT_ONCE"
    TRANSIENT_ALWAYS = "TRANSIENT_ALWAYS"
    PERMANENT = "PERMANENT"
    TIMEOUT = "TIMEOUT"


class MonitoringStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    NO_DATA = "NO_DATA"


class Role(StrEnum):
    VIEWER = "viewer"
    DEVELOPER = "developer"
    APPROVER = "approver"
    RELEASE_MANAGER = "release_manager"
    SERVICE = "service"
    ADMIN = "admin"
