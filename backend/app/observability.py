"""Structured logging, correlation IDs, Prometheus metrics and optional OpenTelemetry spans."""

import contextvars
import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> contextvars.Token[str | None]:
    return _correlation_id.set(value)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our access log


# ---- Prometheus: labels are bounded enums / route templates, never ids. -------------------
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
DEPLOYMENT_TRANSITIONS = Counter(
    "deployment_transitions_total", "Deployment state transitions", ["to_status", "environment"]
)
DEPLOYMENT_FAILURES = Counter(
    "deployment_failures_total", "Deployment failures by class", ["failure_class", "environment"]
)
DEPLOYMENT_DURATION = Histogram(
    "deployment_duration_seconds",
    "Request-to-terminal duration",
    ["environment", "outcome"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
)
WORKER_JOBS = Counter("worker_jobs_total", "Worker job outcomes", ["outcome"])
QUEUE_DEPTH = Gauge("queue_depth", "Jobs by status", ["status"])
OLDEST_PENDING_AGE = Gauge("queue_oldest_pending_age_seconds", "Age of oldest runnable job")
RECONCILIATIONS = Counter("reconciliations_total", "Stale-lease recoveries", ["result"])


# ---- OpenTelemetry (optional). With only the API installed these are no-op spans; set
# OTEL_* env vars and install the SDK/exporter (or use opentelemetry-instrument) to ship them.
try:  # pragma: no cover - exercised implicitly
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer("mlops.platform")
except ImportError:  # pragma: no cover
    _tracer = None  # type: ignore[assignment]


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as sp:
        for key, value in attributes.items():
            sp.set_attribute(key, value)
        yield
