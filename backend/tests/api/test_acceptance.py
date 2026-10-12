"""The brief's ten acceptance scenarios, end to end through HTTP + the async worker."""

from tests.conftest import (
    ADMIN,
    APPROVER,
    DEV,
    RELEASE,
    VIEWER,
    Env,
    approve_version,
    deploy,
    make_model,
    make_version,
    run_to_completion,
)  # fmt: skip


def promote_to_prod(env: Env, version: str) -> str:
    assert deploy(env, version, "staging").status_code == 202
    run_to_completion(env)
    r = deploy(env, version, "production")
    assert r.status_code == 202, r.text
    run_to_completion(env)
    return str(r.json()["id"])


def test_1_to_4_register_approve_block_and_deploy(env: Env) -> None:
    make_model(env)
    make_version(env, version="1.0.0")
    make_version(env, version="2.0.0")  # scenario 1
    approve_version(env, version="1.0.0")  # scenario 2

    blocked = deploy(env, "2.0.0", "production")  # scenario 3
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "APPROVAL_REQUIRED"

    skip_staging = deploy(env, "1.0.0", "production")
    assert skip_staging.json()["error"]["code"] == "STAGING_REQUIRED"

    dep_id = promote_to_prod(env, "1.0.0")  # scenario 4
    dep = env.client.get(f"/deployments/{dep_id}", headers=VIEWER).json()
    assert dep["status"] == "SUCCEEDED"
    v = env.client.get("/models/pump-model/versions/1.0.0", headers=VIEWER).json()
    assert v["stage"] == "PRODUCTION"


def test_post_deployment_returns_before_execution(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    r = deploy(env, "1.0.0", "staging")
    assert r.status_code == 202
    assert r.json()["status"] == "REQUESTED"  # async: nothing ran yet
    assert env.runtime.side_effects == 0
    run_to_completion(env)
    assert env.client.get(f"/deployments/{r.json()['id']}", headers=VIEWER).json()["status"] == "SUCCEEDED"


def test_5_monitoring_data_is_served(env: Env) -> None:
    make_model(env)
    pts = [
        {"version": "1.0.0", "environment": "production", "ts": f"2026-07-0{d}T00:00:00Z",
         "latency_ms": 90, "throughput_rpm": 1000, "error_rate": 0.01, "quality_score": 0.9,
         "drift_score": 0.1, "availability": 99.9}
        for d in range(1, 6)
    ]  # fmt: skip
    r = env.client.post("/models/pump-model/metrics", headers=DEV, json={"points": pts})
    assert r.status_code == 202 and r.json()["inserted"] == 5
    again = env.client.post("/models/pump-model/metrics", headers=DEV, json={"points": pts})
    assert again.json()["inserted"] == 0  # idempotent re-send
    m = env.client.get("/models/pump-model/metrics?environment=production", headers=VIEWER).json()
    assert m["status"] == "HEALTHY"
    assert m["summary"]["count"] == 5
    assert m["summary"]["last_successful_inference"].startswith("2026-07-05")
    assert len(m["series"]) == 5


def test_6_retry_failed_deployment(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    r = deploy(env, "1.0.0", "staging", simulate_failure="PERMANENT")
    run_to_completion(env)
    failed = env.client.get(f"/deployments/{r.json()['id']}", headers=VIEWER).json()
    assert failed["status"] == "FAILED"
    assert failed["failure_class"] == "RUNTIME_PERMANENT"

    # retry re-runs the same request (still PERMANENT) -> fails again, but the path works
    retry = env.client.post(f"/deployments/{failed['id']}/retry", headers=RELEASE)
    assert retry.status_code == 202 and retry.json()["status"] == "REQUESTED"
    run_to_completion(env)
    assert env.client.get(f"/deployments/{failed['id']}", headers=VIEWER).json()["status"] == "FAILED"

    not_failed = env.client.post(f"/deployments/{failed['id']}/retry", headers=RELEASE)
    assert not_failed.status_code == 202  # FAILED again, so retry is allowed again


def test_6b_transient_failure_self_heals_and_timeline_explains_it(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    r = deploy(env, "1.0.0", "staging", simulate_failure="TRANSIENT_ONCE")
    run_to_completion(env)
    dep = env.client.get(f"/deployments/{r.json()['id']}", headers=VIEWER).json()
    assert dep["status"] == "SUCCEEDED" and dep["attempt_count"] == 2
    events = env.client.get(f"/deployments/{dep['id']}/events", headers=VIEWER).json()["items"]
    names = [e["event"] for e in events]
    assert "retry_scheduled" in names and names[-1] == "deploy_succeeded"
    assert any(e["failure_class"] == "RUNTIME_TRANSIENT" for e in events)


def test_exhausted_transient_retries_become_failed(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    r = deploy(env, "1.0.0", "staging", simulate_failure="TRANSIENT_ALWAYS")
    run_to_completion(env)
    dep = env.client.get(f"/deployments/{r.json()['id']}", headers=VIEWER).json()
    assert dep["status"] == "FAILED" and dep["failure_class"] == "RUNTIME_TRANSIENT"
    assert dep["attempt_count"] == 3


def test_timeout_is_classified(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    r = deploy(env, "1.0.0", "staging", simulate_failure="TIMEOUT")
    run_to_completion(env)
    dep = env.client.get(f"/deployments/{r.json()['id']}", headers=VIEWER).json()
    assert dep["failure_class"] == "TIMEOUT"


def test_7_rollback_production(env: Env) -> None:
    make_model(env)
    for v in ("1.0.0", "2.0.0"):
        make_version(env, version=v)
        approve_version(env, version=v)  # noqa: E702
    promote_to_prod(env, "1.0.0")
    v2_dep = promote_to_prod(env, "2.0.0")
    assert env.client.get("/models/pump-model/versions/1.0.0", headers=VIEWER).json()["stage"] == "APPROVED"

    rb = env.client.post(f"/deployments/{v2_dep}/rollback", headers=RELEASE, json={"reason": "drift"})
    assert rb.status_code == 202, rb.text
    assert rb.json()["kind"] == "ROLLBACK" and rb.json()["version"] == "1.0.0"
    run_to_completion(env)

    assert env.client.get(f"/deployments/{v2_dep}", headers=VIEWER).json()["status"] == "ROLLED_BACK"
    assert env.client.get(f"/deployments/{rb.json()['id']}", headers=VIEWER).json()["status"] == "SUCCEEDED"
    stage = lambda v: env.client.get(f"/models/pump-model/versions/{v}", headers=VIEWER).json()["stage"]  # noqa: E731
    # 2.0.0 is rolled off production but still serves staging, so its stage reflects that.
    assert stage("1.0.0") == "PRODUCTION" and stage("2.0.0") == "STAGING"


def test_unsafe_rollbacks_are_refused(env: Env) -> None:
    make_model(env)
    for v in ("1.0.0", "2.0.0"):
        make_version(env, version=v)
        approve_version(env, version=v)  # noqa: E702
    only = promote_to_prod(env, "1.0.0")
    no_target = env.client.post(f"/deployments/{only}/rollback", headers=RELEASE)
    assert no_target.json()["error"]["code"] == "ROLLBACK_NO_TARGET"

    newest = promote_to_prod(env, "2.0.0")
    stale = env.client.post(f"/deployments/{only}/rollback", headers=RELEASE)  # not the head
    assert stale.json()["error"]["code"] == "ROLLBACK_NOT_CURRENT"

    # target archived after the fact -> unsafe. Archive is blocked while serving, so
    # take 1.0.0 out of rotation by rolling the head forward first is not possible; instead
    # approve-revoke is modelled by archiving once it no longer serves (it is not serving).
    assert env.client.post("/models/pump-model/versions/1.0.0/archive", headers=RELEASE).status_code == 200
    unsafe = env.client.post(f"/deployments/{newest}/rollback", headers=RELEASE)
    assert unsafe.json()["error"]["code"] == "ROLLBACK_TARGET_UNSAFE"


def test_8_duplicate_requests_are_safe(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    first = deploy(env, "1.0.0", "staging", key="client-key-0001")
    second = deploy(env, "1.0.0", "staging", key="client-key-0001")
    assert first.status_code == 202 and second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert first.json()["id"] == second.json()["id"]

    reused = deploy(env, "1.0.0", "production", key="client-key-0001")
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # Without a key, an in-flight duplicate is still rejected rather than double-run.
    dup = deploy(env, "1.0.0", "staging")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DEPLOYMENT_IN_PROGRESS"
    run_to_completion(env)
    assert env.runtime.side_effects == 1

    again = deploy(env, "1.0.0", "staging")
    assert again.json()["error"]["code"] == "VERSION_ALREADY_ACTIVE"


def test_9_errors_use_one_envelope_with_correlation_id(env: Env) -> None:
    r = env.client.get("/models/nope", headers=VIEWER, params={})
    body = r.json()["error"]
    assert r.status_code == 404 and body["code"] == "MODEL_NOT_FOUND"
    assert body["correlation_id"] == r.headers["X-Correlation-ID"]

    bad = env.client.post("/models", headers=DEV, json={"model_id": "Bad Id", "name": "x", "owner": "y"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    assert bad.json()["error"]["details"][0]["field"].endswith("model_id")

    assert env.client.get("/models").json()["error"]["code"] == "UNAUTHENTICATED"
    echoed = env.client.get("/models", headers={**VIEWER, "X-Correlation-ID": "abc123"})
    assert echoed.headers["X-Correlation-ID"] == "abc123"


def test_deployment_carries_request_correlation_id_into_timeline(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    r = env.client.post(
        "/deployments",
        headers={**RELEASE, "X-Correlation-ID": "trace-xyz"},
        json={"model_id": "pump-model", "version": "1.0.0", "environment": "staging"},
    )
    run_to_completion(env)
    events = env.client.get(f"/deployments/{r.json()['id']}/events", headers=VIEWER).json()["items"]
    assert {e["correlation_id"] for e in events} == {"trace-xyz"}


def test_health_and_metrics_endpoints(env: Env) -> None:
    assert env.client.get("/health/live").json()["status"] == "ok"
    ready = env.client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] == "ok"
    assert env.client.get("/health").status_code == 200
    text = env.client.get("/metrics").text
    assert "http_requests_total" in text and "deployment_transitions_total" in text


def test_model_listing_filters_and_pagination(env: Env) -> None:
    for i in range(3):
        make_model(env, f"model-{i}")
    page = env.client.get("/models?limit=2&q=model", headers=VIEWER).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert env.client.get("/models?tag=pump", headers=VIEWER).json()["total"] == 3
    assert env.client.get("/models?tag=zzz", headers=VIEWER).json()["total"] == 0
    assert env.client.get("/models?limit=0", headers=VIEWER).status_code == 422
    assert env.client.get("/models/model-0/versions", headers=VIEWER).json()["total"] == 0
    _ = (APPROVER, ADMIN)
