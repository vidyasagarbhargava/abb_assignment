from datetime import UTC, datetime

import pytest

from app.domain.enums import Environment, MonitoringStatus, Role
from app.domain.errors import ForbiddenError
from app.domain.monitoring import Point, classify, percentile, summarize
from app.domain.rbac import Permission, Principal, deploy_permission


def principal(role: Role) -> Principal:
    return Principal("u", role, "t")


def test_developer_cannot_approve_or_deploy_production() -> None:
    dev = principal(Role.DEVELOPER)
    with pytest.raises(ForbiddenError):
        dev.require(Permission.VERSION_APPROVE)
    with pytest.raises(ForbiddenError):
        dev.require(deploy_permission(Environment.PRODUCTION))
    dev.require(deploy_permission(Environment.STAGING))


def test_approver_cannot_deploy() -> None:
    assert not principal(Role.APPROVER).can(Permission.DEPLOY_STAGING)


def test_admin_has_every_permission() -> None:
    assert all(principal(Role.ADMIN).can(p) for p in Permission)


def pt(**kw: float) -> Point:
    base = dict(latency_ms=90, throughput_rpm=1000, error_rate=0.01, quality_score=0.9,
                drift_score=0.1, availability=99.8)  # fmt: skip
    base.update(kw)
    return Point(datetime(2026, 1, 1, tzinfo=UTC), **base)  # type: ignore[arg-type]


def test_percentile_nearest_rank() -> None:
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10
    assert percentile([5], 0.5) == 5


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, MonitoringStatus.HEALTHY),
        ({"drift_score": 0.35}, MonitoringStatus.DEGRADED),
        ({"quality_score": 0.7}, MonitoringStatus.DEGRADED),
        ({"error_rate": 0.06}, MonitoringStatus.CRITICAL),
        ({"availability": 98.0}, MonitoringStatus.CRITICAL),
    ],
)
def test_classification(overrides: dict[str, float], expected: MonitoringStatus) -> None:
    assert classify(summarize([pt(**overrides)])) == expected


def test_no_data() -> None:
    assert classify(summarize([])) == MonitoringStatus.NO_DATA


def test_last_successful_inference_skips_dead_samples() -> None:
    live = Point(datetime(2026, 1, 1, tzinfo=UTC), 90, 100, 0.0, 0.9, 0.1, 99.9)
    dead = Point(datetime(2026, 1, 2, tzinfo=UTC), 0, 0, 1.0, 0.0, 0.0, 0.0)
    assert summarize([live, dead]).last_successful_inference == live.timestamp  # type: ignore[union-attr]
