"""Pure metric aggregation and health classification."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import MonitoringStatus


@dataclass(frozen=True)
class Point:
    timestamp: datetime
    latency_ms: float
    throughput_rpm: float
    error_rate: float
    quality_score: float
    drift_score: float
    availability: float


@dataclass(frozen=True)
class Thresholds:
    crit_availability: float = 98.5
    crit_error_rate: float = 0.05
    crit_drift: float = 0.5
    warn_availability: float = 99.0
    warn_error_rate: float = 0.02
    warn_drift: float = 0.3
    warn_quality: float = 0.8
    warn_latency_p95_ms: float = 200.0


@dataclass(frozen=True)
class Summary:
    count: int
    latency_avg_ms: float
    latency_p95_ms: float
    throughput_avg_rpm: float
    error_rate_avg: float
    quality_avg: float
    drift_avg: float
    availability_avg: float
    last_successful_inference: datetime | None


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile; deterministic and dependency-free."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def summarize(points: Sequence[Point]) -> Summary | None:
    if not points:
        return None
    served = [p.timestamp for p in points if p.throughput_rpm > 0 and p.error_rate < 1.0]
    return Summary(
        count=len(points),
        latency_avg_ms=_mean([p.latency_ms for p in points]),
        latency_p95_ms=percentile([p.latency_ms for p in points], 0.95),
        throughput_avg_rpm=_mean([p.throughput_rpm for p in points]),
        error_rate_avg=_mean([p.error_rate for p in points]),
        quality_avg=_mean([p.quality_score for p in points]),
        drift_avg=_mean([p.drift_score for p in points]),
        availability_avg=_mean([p.availability for p in points]),
        last_successful_inference=max(served) if served else None,
    )


def classify(summary: Summary | None, t: Thresholds = Thresholds()) -> MonitoringStatus:
    if summary is None:
        return MonitoringStatus.NO_DATA
    if (
        summary.availability_avg < t.crit_availability
        or summary.error_rate_avg > t.crit_error_rate
        or summary.drift_avg > t.crit_drift
    ):
        return MonitoringStatus.CRITICAL
    if (
        summary.availability_avg < t.warn_availability
        or summary.error_rate_avg > t.warn_error_rate
        or summary.drift_avg > t.warn_drift
        or summary.quality_avg < t.warn_quality
        or summary.latency_p95_ms > t.warn_latency_p95_ms
    ):
        return MonitoringStatus.DEGRADED
    return MonitoringStatus.HEALTHY
