"""Failure-mode tests: crash recovery, reconciliation, audit immutability, races, queueing."""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.db import utcnow
from app.orm import AuditEvent, Deployment, Job
from app.worker import DeploymentWorker
from tests.conftest import (
    RELEASE,
    VIEWER,
    Env,
    approve_version,
    deploy,
    make_model,
    make_version,
    run_to_completion,
)  # fmt: skip


def seed(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702


def expire_leases(env: Env) -> None:
    with env.container.session_factory() as s:
        s.execute(
            update(Job)
            .where(Job.status == "RUNNING")
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )
        s.commit()


def test_external_success_then_db_failure_is_reconciled_without_second_side_effect(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime deploys, then our final commit 'fails'. Recovery must converge, not redeploy."""
    seed(env)
    dep_id = deploy(env, "1.0.0", "staging").json()["id"]

    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("simulated: DB down after external success")

    monkeypatch.setattr(DeploymentWorker, "_succeed", boom)
    env.worker.run_once()
    assert env.runtime.side_effects == 1  # the external world has changed...
    with env.container.session_factory() as s:
        assert s.get(Deployment, dep_id).status == "DEPLOYING"  # ...but we never recorded it

    monkeypatch.undo()
    expire_leases(env)
    env.worker.run_once()

    with env.container.session_factory() as s:
        dep = s.get(Deployment, dep_id)
        assert dep.status == "SUCCEEDED"
        assert s.scalar(select(Job.status).where(Job.deployment_id == dep_id)) == "DONE"
    assert env.runtime.side_effects == 1 and env.runtime.calls == 1  # no second deploy attempt
    events = env.client.get(f"/deployments/{dep_id}/events", headers=VIEWER).json()["items"]
    assert events[-1]["event"] == "reconciled_success"


def test_crash_before_runtime_call_requeues_and_completes(env: Env) -> None:
    seed(env)
    dep_id = deploy(env, "1.0.0", "staging").json()["id"]
    job_id = env.worker._claim_next()  # worker "dies" right after claiming
    assert job_id is not None
    assert env.worker.run_once() == 0  # lease still valid: nobody steals it
    expire_leases(env)
    run_to_completion(env)
    assert env.client.get(f"/deployments/{dep_id}", headers=VIEWER).json()["status"] == "SUCCEEDED"
    assert env.runtime.side_effects == 1


def test_two_workers_never_run_the_same_job(env: Env) -> None:
    seed(env)
    deploy(env, "1.0.0", "staging")
    w2 = DeploymentWorker(env.container.session_factory, env.container.settings, env.container.runtimes, "w2")
    results: list[str | None] = []
    barrier = threading.Barrier(2)

    def claim(w: DeploymentWorker) -> None:
        barrier.wait()
        results.append(w._claim_next())

    threads = [threading.Thread(target=claim, args=(w,)) for w in (env.worker, w2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r is not None for r in results) == [False, True]


def test_concurrent_identical_requests_create_one_deployment(env: Env) -> None:
    seed(env)
    codes: list[int] = []
    barrier = threading.Barrier(6)

    def go() -> None:
        barrier.wait()
        codes.append(deploy(env, "1.0.0", "staging", key="same-key-1234").status_code)

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(codes) <= {200, 202} and codes.count(202) == 1
    with env.container.session_factory() as s:
        assert len(s.scalars(select(Deployment)).all()) == 1


def test_concurrent_conflicting_promotions_one_winner(env: Env) -> None:
    seed(env)
    make_version(env, version="2.0.0")
    approve_version(env, version="2.0.0")  # noqa: E702
    codes: list[int] = []
    barrier = threading.Barrier(4)

    def go(version: str) -> None:
        barrier.wait()
        codes.append(deploy(env, version, "staging").status_code)

    threads = [threading.Thread(target=go, args=(v,)) for v in ("1.0.0", "2.0.0", "1.0.0", "2.0.0")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes.count(202) == 1 and codes.count(409) == 3


def test_audit_log_is_append_only(env: Env) -> None:
    make_model(env)
    with env.container.session_factory() as s:
        row = s.scalars(select(AuditEvent)).first()
        row.actor = "tampered"
        with pytest.raises(RuntimeError, match="append-only"):
            s.flush()
        s.rollback()
        row = s.scalars(select(AuditEvent)).first()
        s.delete(row)
        with pytest.raises(RuntimeError, match="append-only"):
            s.flush()


def test_revoked_approval_between_request_and_execution_fails_as_policy(env: Env) -> None:
    seed(env)
    dep_id = deploy(env, "1.0.0", "staging").json()["id"]
    # version archived after queued but before the worker runs
    from app.orm import ModelVersion

    with env.container.session_factory() as s:
        mv = s.scalar(select(ModelVersion))
        mv.stage = "ARCHIVED"
        s.commit()
    run_to_completion(env)
    dep = env.client.get(f"/deployments/{dep_id}", headers=VIEWER).json()
    assert dep["status"] == "FAILED" and dep["failure_class"] == "POLICY"
    assert env.runtime.calls == 0
    _ = RELEASE


def test_backoff_delays_retry(env: Env) -> None:
    seed(env)
    env.container.settings.retry_backoff_base_s = 3600.0
    dep_id = deploy(env, "1.0.0", "staging", simulate_failure="TRANSIENT_ALWAYS").json()["id"]
    env.worker.run_once()
    dep = env.client.get(f"/deployments/{dep_id}", headers=VIEWER).json()
    assert dep["status"] == "REQUESTED" and dep["attempt_count"] == 1  # waiting out the backoff
    assert env.worker.run_once() == 0
