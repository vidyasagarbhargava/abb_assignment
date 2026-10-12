"""Multiple runtimes, version comparison, and the Angular-facing contract."""

from app.runtime.simulated import SimulatedRuntime
from tests.conftest import (
    DEV,
    VIEWER,
    Env,
    approve_version,
    deploy,
    make_model,
    make_version,
    run_to_completion,
)


def test_unknown_runtime_is_rejected_at_registration(env: Env) -> None:
    make_model(env)
    r = env.client.post(
        "/models/pump-model/versions",
        headers=DEV,
        json={"version": "1.0.0", "framework": "sk", "artifact_uri": "s3://a/b", "runtime": "triton"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_RUNTIME"


def test_versions_route_to_their_own_runtime_adapter(env: Env) -> None:
    class Edge(SimulatedRuntime):
        name = "edge"

    edge = Edge()
    env.container.runtimes.register(edge)
    make_model(env)
    make_version(env, version="1.0.0")
    env.client.post(
        "/models/pump-model/versions",
        headers=DEV,
        json={
            "version": "2.0.0",
            "framework": "onnx",
            "artifact_uri": "s3://a/b",
            "training_data_ref": "s3://d",
            "runtime": "edge",
        },
    )
    for v in ("1.0.0", "2.0.0"):
        approve_version(env, version=v)
    deploy(env, "2.0.0", "staging")
    run_to_completion(env)
    assert edge.side_effects == 1 and env.runtime.side_effects == 0
    deploy(env, "1.0.0", "staging")
    run_to_completion(env)
    assert env.runtime.side_effects == 1


def test_compare_versions_returns_deltas(env: Env) -> None:
    make_model(env)

    def pts(version: str, latency: float, drift: float) -> list[dict[str, object]]:
        return [
            {
                "version": version,
                "environment": "production",
                "ts": f"2026-07-0{d}T00:00:00Z",
                "latency_ms": latency,
                "throughput_rpm": 1000,
                "error_rate": 0.01,
                "quality_score": 0.9,
                "drift_score": drift,
                "availability": 99.9,
            }
            for d in range(1, 4)
        ]

    env.client.post("/models/pump-model/metrics", headers=DEV, json={"points": pts("1.0.0", 100, 0.1)})
    env.client.post("/models/pump-model/metrics", headers=DEV, json={"points": pts("2.0.0", 80, 0.4)})
    r = env.client.get("/models/pump-model/compare?base=1.0.0&candidate=2.0.0", headers=VIEWER).json()
    assert r["delta"]["latency_avg_ms"] == -20
    assert r["base"]["status"] == "HEALTHY" and r["candidate"]["status"] == "DEGRADED"


def test_metrics_for_unknown_model_and_empty_series(env: Env) -> None:
    assert env.client.get("/models/ghost/metrics", headers=VIEWER).status_code == 404
    make_model(env)
    m = env.client.get("/models/pump-model/metrics", headers=VIEWER).json()
    assert m["status"] == "NO_DATA" and m["series"] == [] and m["summary"] is None


def test_metric_batch_limit_and_validation(env: Env) -> None:
    make_model(env)
    bad = env.client.post(
        "/models/pump-model/metrics",
        headers=DEV,
        json={
            "points": [
                {
                    "version": "1.0.0",
                    "environment": "production",
                    "ts": "2026-07-01T00:00:00Z",
                    "latency_ms": -1,
                    "throughput_rpm": 1,
                    "error_rate": 2,
                    "quality_score": 0.5,
                    "drift_score": 0.1,
                    "availability": 99,
                }
            ]
        },
    )
    assert bad.status_code == 422 and len(bad.json()["error"]["details"]) == 2
    env.container.settings.max_metric_batch = 1
    two = {
        "version": "1.0.0",
        "environment": "production",
        "latency_ms": 1,
        "throughput_rpm": 1,
        "error_rate": 0.1,
        "quality_score": 0.5,
        "drift_score": 0.1,
        "availability": 99,
    }
    r = env.client.post(
        "/models/pump-model/metrics",
        headers=DEV,
        json={"points": [{**two, "ts": "2026-07-01T00:00:00Z"}, {**two, "ts": "2026-07-02T00:00:00Z"}]},
    )
    assert r.json()["error"]["code"] == "BATCH_TOO_LARGE"


def test_openapi_exposes_error_envelope_and_idempotency_header(env: Env) -> None:
    spec = env.client.get("/openapi.json").json()
    assert "ErrorResponse" in spec["components"]["schemas"]
    params = spec["paths"]["/deployments"]["post"]["parameters"]
    assert any(p["name"] == "Idempotency-Key" for p in params)
