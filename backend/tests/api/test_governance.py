"""RBAC, separation of duties, tenancy isolation, audit trail, conflicting promotions."""

from tests.conftest import (
    APPROVER,
    DEV,
    RELEASE,
    VIEWER,
    Env,
    approve_version,
    deploy,
    headers,
    make_model,
    make_version,
    run_to_completion,
)  # fmt: skip


def test_rbac_matrix_enforced_at_api(env: Env) -> None:
    assert (
        env.client.post(
            "/models", headers=VIEWER, json={"model_id": "ab", "name": "n", "owner": "o"}
        ).status_code
        == 403
    )
    make_model(env)
    make_version(env)  # noqa: E702
    base = "/models/pump-model/versions/1.0.0"
    assert env.client.post(f"{base}/validate", headers=DEV).status_code == 200
    denied = env.client.post(f"{base}/approve", headers=DEV, json={})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"
    assert env.client.post(f"{base}/approve", headers=APPROVER, json={}).status_code == 200
    # developers may deploy to staging but never to production
    assert deploy(env, "1.0.0", "staging").status_code == 202  # RELEASE helper
    prod = env.client.post(
        "/deployments", headers=DEV,
        json={"model_id": "pump-model", "version": "1.0.0", "environment": "production"},
    )  # fmt: skip
    assert prod.status_code == 403


def test_four_eyes_registrant_cannot_self_approve(env: Env) -> None:
    make_model(env)
    make_version(env)  # noqa: E702
    env.client.post("/models/pump-model/versions/1.0.0/validate", headers=DEV)
    both = headers("dana", "admin")  # same user id as the registrant
    r = env.client.post("/models/pump-model/versions/1.0.0/approve", headers=both, json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"


def test_cannot_skip_validation_or_approve_twice(env: Env) -> None:
    make_model(env)
    make_version(env)  # noqa: E702
    base = "/models/pump-model/versions/1.0.0"
    skip = env.client.post(f"{base}/approve", headers=APPROVER, json={})
    assert skip.json()["error"]["code"] == "INVALID_STAGE_TRANSITION"
    env.client.post(f"{base}/validate", headers=DEV)
    assert env.client.post(f"{base}/approve", headers=APPROVER, json={}).status_code == 200
    again = env.client.post(f"{base}/approve", headers=APPROVER, json={})
    assert again.json()["error"]["code"] == "INVALID_STAGE_TRANSITION"


def test_validation_gate_rejects_bad_artifact_scheme(env: Env) -> None:
    make_model(env)
    env.client.post(
        "/models/pump-model/versions", headers=DEV,
        json={"version": "1.0.0", "framework": "sk", "artifact_uri": "http://evil/x"},
    )  # fmt: skip
    r = env.client.post("/models/pump-model/versions/1.0.0/validate", headers=DEV)
    assert r.json()["error"]["code"] == "VALIDATION_FAILED" and len(r.json()["error"]["details"]) == 2


def test_tenant_isolation(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    dep = deploy(env, "1.0.0", "staging").json()["id"]
    other = headers("mallory", "admin", tenant="globex")
    assert env.client.get("/models/pump-model", headers=other).status_code == 404
    assert env.client.get(f"/deployments/{dep}", headers=other).status_code == 404
    assert env.client.get("/models", headers=other).json()["total"] == 0
    # the same slug can exist independently in another tenant
    r = env.client.post("/models", headers=other, json={"model_id": "pump-model", "name": "x", "owner": "y"})
    assert r.status_code == 201


def test_duplicate_registration_conflicts(env: Env) -> None:
    make_model(env)
    make_version(env)  # noqa: E702
    r = env.client.post("/models", headers=DEV, json={"model_id": "pump-model", "name": "n", "owner": "o"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_EXISTS"
    r = env.client.post(
        "/models/pump-model/versions", headers=DEV,
        json={"version": "1.0.0", "framework": "sk", "artifact_uri": "s3://a/b"},
    )  # fmt: skip
    assert r.json()["error"]["code"] == "VERSION_EXISTS"


def test_conflicting_promotions_second_request_loses(env: Env) -> None:
    make_model(env)
    for v in ("1.0.0", "2.0.0"):
        make_version(env, version=v)
        approve_version(env, version=v)  # noqa: E702
    first = deploy(env, "1.0.0", "staging")
    second = deploy(env, "2.0.0", "staging")  # same slot while first is in flight
    assert first.status_code == 202
    assert second.status_code == 409 and second.json()["error"]["code"] == "DEPLOYMENT_IN_PROGRESS"
    run_to_completion(env)
    assert deploy(env, "2.0.0", "staging").status_code == 202  # slot is free again


def test_stale_promotion_guard(env: Env) -> None:
    make_model(env)
    for v in ("1.0.0", "2.0.0"):
        make_version(env, version=v)
        approve_version(env, version=v)  # noqa: E702
    d1 = deploy(env, "1.0.0", "staging").json()["id"]
    run_to_completion(env)
    # caller believes nothing is serving -> stale view -> rejected with the real head
    stale = deploy(env, "2.0.0", "staging", expected_current_deployment_id=None)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "PROMOTION_CONFLICT"
    assert stale.json()["error"]["details"]["current_deployment_id"] == d1
    assert deploy(env, "2.0.0", "staging", expected_current_deployment_id=d1).status_code == 202


def test_archive_blocked_while_serving(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    deploy(env, "1.0.0", "staging")
    run_to_completion(env)  # noqa: E702
    r = env.client.post("/models/pump-model/versions/1.0.0/archive", headers=RELEASE)
    assert r.json()["error"]["code"] == "VERSION_IN_USE"


def test_audit_trail_is_complete_and_restricted(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    deploy(env, "1.0.0", "staging")
    run_to_completion(env)  # noqa: E702
    assert env.client.get("/audit", headers=VIEWER).status_code == 403
    items = env.client.get("/audit?limit=100", headers=APPROVER).json()["items"]
    actions = {i["action"] for i in items}
    assert {"model.created", "version.registered", "version.validated", "version.approved",
            "deployment.requested"} <= actions  # fmt: skip
    approved = next(i for i in items if i["action"] == "version.approved")
    assert approved["actor"] == "alex" and approved["before"]["stage"] == "VALIDATED"
    assert approved["after"]["stage"] == "APPROVED" and approved["correlation_id"]


def test_failure_simulation_can_be_disabled(env: Env) -> None:
    make_model(env)
    make_version(env)
    approve_version(env)  # noqa: E702
    env.container.settings.allow_failure_simulation = False
    r = deploy(env, "1.0.0", "staging", simulate_failure="PERMANENT")
    assert r.status_code == 422 and r.json()["error"]["code"] == "SIMULATION_DISABLED"
