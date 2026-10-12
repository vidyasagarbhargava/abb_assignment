"""Runtime port: the only seam between the platform and a model-serving system.

Adapters (KServe, SageMaker, Azure ML, Triton, ...) implement `ModelRuntime`. The contract
is deliberately small and *idempotent on `idempotency_key`* so that a retry after an
ambiguous failure (external success, local DB failure) is always safe.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.errors import ValidationFailedError


@dataclass(frozen=True)
class RuntimeDeployRequest:
    idempotency_key: str  # the platform deployment id
    model_id: str
    version: str
    artifact_uri: str
    environment: str
    attempt: int
    simulate_failure: str = "NONE"


@dataclass(frozen=True)
class RuntimeDeployment:
    external_ref: str
    state: str  # READY | PENDING | FAILED


class ModelRuntime(Protocol):
    name: str

    def deploy(self, request: RuntimeDeployRequest) -> RuntimeDeployment:
        """Create or converge the serving endpoint. Must be idempotent on idempotency_key.
        Raise RuntimeTransientError / RuntimePermanentError / RuntimeTimeoutError."""
        ...

    def describe(self, idempotency_key: str) -> RuntimeDeployment | None:
        """Return the runtime's view of a previous deploy, or None if it never happened.
        Used by reconciliation after crashes."""
        ...


class RuntimeRegistry:
    def __init__(self, runtimes: list[ModelRuntime] | None = None) -> None:
        self._runtimes: dict[str, ModelRuntime] = {}
        for runtime in runtimes or []:
            self.register(runtime)

    def register(self, runtime: ModelRuntime) -> None:
        self._runtimes[runtime.name] = runtime

    def names(self) -> list[str]:
        return sorted(self._runtimes)

    def has(self, name: str) -> bool:
        return name in self._runtimes

    def get(self, name: str) -> ModelRuntime:
        try:
            return self._runtimes[name]
        except KeyError:
            raise ValidationFailedError(
                f"Unknown runtime '{name}'. Available: {self.names()}", code="UNKNOWN_RUNTIME"
            ) from None
