"""Monitoring ingestion and read-side aggregation."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.enums import MonitoringStatus
from app.domain.errors import NotFoundError, ValidationFailedError
from app.domain.monitoring import Point, Summary, classify, summarize
from app.domain.rbac import Permission, Principal
from app.orm import MetricPoint, Model
from app.services.common import commit, record_audit

Bucket = Literal["hour", "day"]


@dataclass(frozen=True)
class SeriesBucket:
    start: datetime
    summary: Summary


@dataclass(frozen=True)
class MetricsView:
    model_id: str
    window_start: datetime | None
    window_end: datetime | None
    summary: Summary | None
    status: MonitoringStatus
    series: list[SeriesBucket]
    truncated: bool


class MetricsService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.s = session
        self.settings = settings

    def _require_model(self, p: Principal, model_id: str) -> None:
        if self.s.get(Model, (p.tenant_id, model_id)) is None:
            raise NotFoundError(f"Model '{model_id}' not found.", code="MODEL_NOT_FOUND")

    def ingest(self, p: Principal, model_id: str, rows: list[dict[str, object]]) -> int:
        """Idempotent batch insert: re-sent samples (same series + timestamp) are ignored."""
        p.require(Permission.METRICS_WRITE)
        self._require_model(p, model_id)
        if len(rows) > self.settings.max_metric_batch:
            raise ValidationFailedError(
                f"Batch exceeds {self.settings.max_metric_batch} points.", code="BATCH_TOO_LARGE"
            )
        if not rows:
            return 0
        payload = [{**r, "tenant_id": p.tenant_id, "model_id": model_id} for r in rows]
        dialect = self.s.get_bind().dialect.name
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = insert(MetricPoint).values(payload).on_conflict_do_nothing().returning(MetricPoint.id)
        result = self.s.execute(stmt)
        inserted = len(result.all())
        record_audit(
            self.s, p, action="metrics.ingested", entity_type="metrics", entity_id=model_id,
            model_id=model_id, after={"submitted": len(rows)},
        )  # fmt: skip
        commit(self.s)
        return inserted

    def query(
        self,
        p: Principal,
        model_id: str,
        *,
        environment: str | None,
        version: str | None,
        start: datetime | None,
        end: datetime | None,
        window_hours: int,
        bucket: Bucket,
    ) -> MetricsView:
        p.require(Permission.MODEL_READ)
        self._require_model(p, model_id)
        base = [MetricPoint.tenant_id == p.tenant_id, MetricPoint.model_id == model_id]
        if environment:
            base.append(MetricPoint.environment == environment)
        if version:
            base.append(MetricPoint.version == version)

        # Anchor at the newest sample, not wall-clock, so replayed/batch data still renders.
        window_end = end or self.s.scalar(select(func.max(MetricPoint.ts)).where(*base))
        if window_end is None:
            return MetricsView(model_id, None, None, None, MonitoringStatus.NO_DATA, [], False)
        window_start = start or (window_end - timedelta(hours=window_hours))

        limit = self.settings.max_metric_rows_per_query
        rows = list(
            self.s.scalars(
                select(MetricPoint)
                .where(*base, MetricPoint.ts >= window_start, MetricPoint.ts <= window_end)
                .order_by(MetricPoint.ts)
                .limit(limit + 1)
            )
        )
        truncated = len(rows) > limit
        points = [
            Point(r.ts, r.latency_ms, r.throughput_rpm, r.error_rate, r.quality_score,
                  r.drift_score, r.availability)
            for r in rows[:limit]
        ]  # fmt: skip
        buckets: dict[datetime, list[Point]] = defaultdict(list)
        for pt in points:
            floor = pt.timestamp.replace(minute=0, second=0, microsecond=0)
            if bucket == "day":
                floor = floor.replace(hour=0)
            buckets[floor].append(pt)
        series = [
            SeriesBucket(start, s)
            for start, pts in sorted(buckets.items())
            if (s := summarize(pts)) is not None
        ]
        summary = summarize(points)
        return MetricsView(model_id, window_start, window_end, summary, classify(summary), series, truncated)

    def compare(
        self, p: Principal, model_id: str, base: str, candidate: str, environment: str | None,
        window_hours: int,
    ) -> dict[str, object]:  # fmt: skip
        views = {
            v: self.query(
                p, model_id, environment=environment, version=v, start=None, end=None,
                window_hours=window_hours, bucket="day",
            )
            for v in (base, candidate)
        }  # fmt: skip
        b, c = views[base].summary, views[candidate].summary
        delta: dict[str, float] | None = None
        if b and c:
            bd, cd = asdict(b), asdict(c)
            delta = {k: cd[k] - bd[k] for k in bd if isinstance(bd[k], float) and isinstance(cd[k], float)}
        return {
            "base": {"version": base, "summary": b, "status": views[base].status},
            "candidate": {"version": candidate, "summary": c, "status": views[candidate].status},
            "delta": delta,
        }
