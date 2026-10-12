"""In-memory runtime with deterministic failure injection (the brief's failure simulation)."""

import threading

from app.domain.enums import SimulatedFailure
from app.domain.errors import (
    RuntimePermanentError,
    RuntimeTimeoutError,
    RuntimeTransientError,
)
from app.runtime.base import RuntimeDeployment, RuntimeDeployRequest


class SimulatedRuntime:
    name = "simulated"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._deployments: dict[str, RuntimeDeployment] = {}
        self.calls = 0  # every deploy() invocation
        self.side_effects = 0  # actual creations; proves idempotency in tests

    def deploy(self, request: RuntimeDeployRequest) -> RuntimeDeployment:
        with self._lock:
            self.calls += 1
            existing = self._deployments.get(request.idempotency_key)
            if existing is not None:
                return existing

            mode = request.simulate_failure
            if mode == SimulatedFailure.PERMANENT:
                raise RuntimePermanentError("simulated: artifact rejected by runtime")
            if mode == SimulatedFailure.TIMEOUT:
                raise RuntimeTimeoutError("simulated: runtime timed out")
            if mode == SimulatedFailure.TRANSIENT_ALWAYS or (
                mode == SimulatedFailure.TRANSIENT_ONCE and request.attempt <= 1
            ):
                raise RuntimeTransientError("simulated: runtime returned 503")

            result = RuntimeDeployment(
                external_ref=f"sim://{request.environment}/{request.model_id}/{request.version}",
                state="READY",
            )
            self._deployments[request.idempotency_key] = result
            self.side_effects += 1
            return result

    def describe(self, idempotency_key: str) -> RuntimeDeployment | None:
        with self._lock:
            return self._deployments.get(idempotency_key)
