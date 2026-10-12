"""Pure governance rules: state machines, promotion policy and rollback planning.

No I/O here. Services load facts from the database and ask these functions for a verdict,
which keeps the rules unit-testable and prevents them being re-implemented in handlers.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import (
    ApprovalStatus,
    DeploymentKind,
    DeploymentStatus,
    Environment,
    Stage,
)
from app.domain.errors import ConflictError, PolicyViolationError

VERSION_TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    Stage.DRAFT: frozenset({Stage.VALIDATED, Stage.ARCHIVED}),
    Stage.VALIDATED: frozenset({Stage.APPROVED, Stage.DRAFT, Stage.ARCHIVED}),
    Stage.APPROVED: frozenset({Stage.STAGING, Stage.PRODUCTION, Stage.ARCHIVED}),
    # A serving version can step back to APPROVED when superseded or rolled back.
    Stage.STAGING: frozenset({Stage.PRODUCTION, Stage.APPROVED}),
    Stage.PRODUCTION: frozenset({Stage.APPROVED}),
    Stage.ARCHIVED: frozenset(),
}

DEPLOYMENT_TRANSITIONS: dict[DeploymentStatus, frozenset[DeploymentStatus]] = {
    DeploymentStatus.REQUESTED: frozenset({DeploymentStatus.VALIDATING, DeploymentStatus.FAILED}),
    DeploymentStatus.VALIDATING: frozenset({DeploymentStatus.DEPLOYING, DeploymentStatus.FAILED}),
    DeploymentStatus.DEPLOYING: frozenset(
        {
            DeploymentStatus.SUCCEEDED,
            DeploymentStatus.FAILED,
            DeploymentStatus.REQUESTED,  # automatic retry after a transient failure
        }
    ),
    DeploymentStatus.SUCCEEDED: frozenset({DeploymentStatus.ROLLED_BACK}),
    DeploymentStatus.FAILED: frozenset({DeploymentStatus.REQUESTED}),  # manual retry
    DeploymentStatus.ROLLED_BACK: frozenset(),
}


def assert_version_transition(current: Stage, target: Stage) -> None:
    if target not in VERSION_TRANSITIONS[current]:
        raise ConflictError(
            f"Version cannot move from {current} to {target}.",
            code="INVALID_STAGE_TRANSITION",
            details={"from": str(current), "to": str(target)},
        )


def assert_deployment_transition(current: DeploymentStatus, target: DeploymentStatus) -> None:
    if target not in DEPLOYMENT_TRANSITIONS[current]:
        raise ConflictError(
            f"Deployment cannot move from {current} to {target}.",
            code="INVALID_DEPLOYMENT_TRANSITION",
            details={"from": str(current), "to": str(target)},
        )


@dataclass(frozen=True)
class VersionFacts:
    version: str
    stage: Stage
    approval_status: ApprovalStatus


def assert_deployable(facts: VersionFacts) -> None:
    """A version must be approved and not archived to be deployed to any environment."""
    if facts.stage == Stage.ARCHIVED:
        raise PolicyViolationError(
            f"Version {facts.version} is archived and cannot be deployed.", code="VERSION_ARCHIVED"
        )
    if facts.approval_status != ApprovalStatus.APPROVED or facts.stage in (
        Stage.DRAFT,
        Stage.VALIDATED,
    ):
        raise PolicyViolationError(
            f"Version {facts.version} is not approved (stage={facts.stage}, "
            f"approval={facts.approval_status}).",
            code="APPROVAL_REQUIRED",
        )


def assert_promotion_allowed(
    facts: VersionFacts,
    environment: Environment,
    *,
    has_successful_staging: bool,
    require_staging: bool,
) -> None:
    assert_deployable(facts)
    if environment == Environment.PRODUCTION and require_staging and not has_successful_staging:
        raise PolicyViolationError(
            f"Version {facts.version} has no successful staging deployment; promote through staging first.",
            code="STAGING_REQUIRED",
        )


@dataclass(frozen=True)
class DeploymentFacts:
    id: str
    version_id: str
    version: str
    status: DeploymentStatus
    kind: DeploymentKind
    completed_at: datetime | None


def plan_rollback(
    current_head: DeploymentFacts,
    environment_history: Sequence[DeploymentFacts],
    target_version_facts: dict[str, VersionFacts],
) -> DeploymentFacts:
    """Choose the rollback target or refuse.

    Safe target = the most recent earlier SUCCEEDED deployment in the same environment whose
    version differs from the current one. ROLLED_BACK deployments are never targets (they
    were judged bad), and the target version must still be approved and not archived.
    """
    if current_head.status != DeploymentStatus.SUCCEEDED:
        raise PolicyViolationError(
            "Only a SUCCEEDED deployment can be rolled back.", code="ROLLBACK_NOT_SUCCEEDED"
        )
    candidates = [
        d
        for d in environment_history
        if d.id != current_head.id
        and d.status == DeploymentStatus.SUCCEEDED
        and d.version != current_head.version
        and d.completed_at is not None
        and current_head.completed_at is not None
        and d.completed_at <= current_head.completed_at
    ]
    if not candidates:
        raise PolicyViolationError(
            "No earlier successful deployment exists to roll back to.",
            code="ROLLBACK_NO_TARGET",
        )
    target = max(candidates, key=lambda d: d.completed_at or datetime.min)
    facts = target_version_facts[target.version_id]
    try:
        assert_deployable(facts)
    except PolicyViolationError as exc:
        raise PolicyViolationError(
            f"Rollback target {facts.version} is no longer deployable: {exc.message}",
            code="ROLLBACK_TARGET_UNSAFE",
        ) from exc
    return target
