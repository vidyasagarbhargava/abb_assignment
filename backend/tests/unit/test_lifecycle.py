from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import (
    ApprovalStatus,
    DeploymentKind,
    DeploymentStatus,
    Environment,
    Stage,
)
from app.domain.errors import ConflictError, PolicyViolationError
from app.domain.lifecycle import (
    DeploymentFacts,
    VersionFacts,
    assert_deployment_transition,
    assert_promotion_allowed,
    assert_version_transition,
    plan_rollback,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def vf(stage: Stage, approval: ApprovalStatus = ApprovalStatus.APPROVED, v: str = "1.0.0") -> VersionFacts:
    return VersionFacts(v, stage, approval)


def df(id_: str, version: str, hours: int, status=DeploymentStatus.SUCCEEDED) -> DeploymentFacts:  # type: ignore[no-untyped-def]
    return DeploymentFacts(
        id_, f"vid-{version}", version, status, DeploymentKind.DEPLOY, T0 + timedelta(hours=hours)
    )


@pytest.mark.parametrize(
    ("src", "dst"),
    [(Stage.DRAFT, Stage.VALIDATED), (Stage.VALIDATED, Stage.APPROVED), (Stage.PRODUCTION, Stage.APPROVED)],
)
def test_valid_version_transitions(src: Stage, dst: Stage) -> None:
    assert_version_transition(src, dst)


@pytest.mark.parametrize(
    ("src", "dst"),
    [(Stage.DRAFT, Stage.APPROVED), (Stage.DRAFT, Stage.PRODUCTION), (Stage.ARCHIVED, Stage.APPROVED)],
)
def test_invalid_version_transitions(src: Stage, dst: Stage) -> None:
    with pytest.raises(ConflictError) as e:
        assert_version_transition(src, dst)
    assert e.value.code == "INVALID_STAGE_TRANSITION"


def test_deployment_transition_matrix_rejects_skips() -> None:
    assert_deployment_transition(DeploymentStatus.REQUESTED, DeploymentStatus.VALIDATING)
    with pytest.raises(ConflictError):
        assert_deployment_transition(DeploymentStatus.REQUESTED, DeploymentStatus.SUCCEEDED)
    with pytest.raises(ConflictError):
        assert_deployment_transition(DeploymentStatus.ROLLED_BACK, DeploymentStatus.REQUESTED)


@pytest.mark.parametrize("stage", [Stage.DRAFT, Stage.VALIDATED])
def test_unapproved_cannot_deploy_anywhere(stage: Stage) -> None:
    with pytest.raises(PolicyViolationError) as e:
        assert_promotion_allowed(
            vf(stage, ApprovalStatus.PENDING), Environment.STAGING,
            has_successful_staging=False, require_staging=True,
        )  # fmt: skip
    assert e.value.code == "APPROVAL_REQUIRED"


def test_production_requires_staging_when_configured() -> None:
    with pytest.raises(PolicyViolationError) as e:
        assert_promotion_allowed(
            vf(Stage.APPROVED), Environment.PRODUCTION, has_successful_staging=False, require_staging=True
        )
    assert e.value.code == "STAGING_REQUIRED"
    assert_promotion_allowed(
        vf(Stage.APPROVED), Environment.PRODUCTION, has_successful_staging=False, require_staging=False
    )
    assert_promotion_allowed(
        vf(Stage.APPROVED), Environment.PRODUCTION, has_successful_staging=True, require_staging=True
    )


def test_archived_is_never_deployable() -> None:
    with pytest.raises(PolicyViolationError) as e:
        assert_promotion_allowed(
            vf(Stage.ARCHIVED), Environment.STAGING, has_successful_staging=True, require_staging=False
        )
    assert e.value.code == "VERSION_ARCHIVED"


def test_rollback_picks_most_recent_earlier_different_version() -> None:
    history = [df("d1", "1.0.0", 1), df("d2", "2.0.0", 2), df("d3", "3.0.0", 3)]
    target = plan_rollback(
        history[2], history, {f"vid-{v}": vf(Stage.APPROVED, v=v) for v in ("1.0.0", "2.0.0", "3.0.0")}
    )
    assert target.id == "d2"


def test_rollback_refuses_without_target() -> None:
    only = df("d1", "1.0.0", 1)
    with pytest.raises(PolicyViolationError) as e:
        plan_rollback(only, [only], {"vid-1.0.0": vf(Stage.APPROVED)})
    assert e.value.code == "ROLLBACK_NO_TARGET"


def test_rollback_refuses_archived_target() -> None:
    history = [df("d1", "1.0.0", 1), df("d2", "2.0.0", 2)]
    with pytest.raises(PolicyViolationError) as e:
        plan_rollback(history[1], history, {"vid-1.0.0": vf(Stage.ARCHIVED, v="1.0.0")})
    assert e.value.code == "ROLLBACK_TARGET_UNSAFE"


def test_rollback_ignores_rolled_back_and_failed_history() -> None:
    history = [
        df("d1", "1.0.0", 1),
        df("d2", "2.0.0", 2, DeploymentStatus.ROLLED_BACK),
        df("d3", "3.0.0", 3),
    ]
    target = plan_rollback(history[2], history, {"vid-1.0.0": vf(Stage.APPROVED, v="1.0.0")})
    assert target.id == "d1"


def test_rollback_only_from_succeeded_head() -> None:
    failed = df("d2", "2.0.0", 2, DeploymentStatus.FAILED)
    with pytest.raises(PolicyViolationError) as e:
        plan_rollback(failed, [failed], {})
    assert e.value.code == "ROLLBACK_NOT_SUCCEEDED"
