import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import Container, build_container
from app.config import Settings
from app.db import Base
from app.main import create_app
from app.runtime.base import RuntimeRegistry
from app.runtime.simulated import SimulatedRuntime
from app.worker import DeploymentWorker


def headers(user: str, role: str, tenant: str = "acme") -> dict[str, str]:
    return {"X-User-Id": user, "X-User-Role": role, "X-Tenant-Id": tenant}


DEV = headers("dana", "developer")
APPROVER = headers("alex", "approver")
RELEASE = headers("rene", "release_manager")
VIEWER = headers("vic", "viewer")
ADMIN = headers("ada", "admin")


@dataclass
class Env:
    client: TestClient
    container: Container
    runtime: SimulatedRuntime
    worker: DeploymentWorker


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # TEST_DATABASE_URL lets CI run the identical suite against real PostgreSQL.
    return Settings(
        database_url=os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}",
        embedded_worker=False,  # tests drive the worker explicitly: deterministic
        retry_backoff_base_s=0.0,
        job_max_attempts=3,
    )


@pytest.fixture
def env(settings: Settings) -> Iterator[Env]:
    runtime = SimulatedRuntime()
    container = build_container(settings, RuntimeRegistry([runtime]))
    Base.metadata.drop_all(container.engine)
    Base.metadata.create_all(container.engine)
    app = create_app(container)
    worker = DeploymentWorker(container.session_factory, settings, container.runtimes, "test-worker")
    with TestClient(app) as client:
        yield Env(client, container, runtime, worker)
    Base.metadata.drop_all(container.engine)
    container.engine.dispose()


# ---- scenario helpers -------------------------------------------------------------------
def make_model(env: Env, model_id: str = "pump-model") -> None:
    r = env.client.post(
        "/models",
        headers=DEV,
        json={"model_id": model_id, "name": "Pump Model", "owner": "reliability", "tags": ["pump"]},
    )
    assert r.status_code == 201, r.text


def make_version(env: Env, model_id: str = "pump-model", version: str = "1.0.0") -> None:
    r = env.client.post(
        f"/models/{model_id}/versions",
        headers=DEV,
        json={
            "version": version, "framework": "scikit-learn", "algorithm": "gbm",
            "artifact_uri": f"s3://models/{model_id}/{version}", "training_data_ref": "s3://data/v1",
        },
    )  # fmt: skip
    assert r.status_code == 201, r.text


def approve_version(env: Env, model_id: str = "pump-model", version: str = "1.0.0") -> None:
    base = f"/models/{model_id}/versions/{version}"
    assert env.client.post(f"{base}/validate", headers=DEV).status_code == 200
    r = env.client.post(f"{base}/approve", headers=APPROVER, json={})
    assert r.status_code == 200, r.text


def deploy(env: Env, version: str, environment: str, *, key: str | None = None, **extra: object):  # type: ignore[no-untyped-def]
    hdrs = dict(RELEASE)
    if key:
        hdrs["Idempotency-Key"] = key
    return env.client.post(
        "/deployments",
        headers=hdrs,
        json={"model_id": "pump-model", "version": version, "environment": environment, **extra},
    )


def run_to_completion(env: Env) -> None:
    for _ in range(10):
        if env.worker.run_once() == 0:
            return
