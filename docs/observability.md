# Observability

## Structured logs [Implemented]

JSON to stdout, one object per line: `ts, level, logger, message, correlation_id` plus event-specific fields (`deployment_id`, `failure_class`, `duration_ms`, ...). Health and `/metrics` scrapes are not logged. No request bodies are logged (PII/secrets).

## Correlation IDs [Implemented]

`X-Correlation-ID` is accepted or generated per request, returned on every response, included in every error body, written to audit rows, and **persisted on the deployment**. The async worker restores it from the deployment, so one id links: HTTP request -> audit record -> every timeline event -> worker logs -> reconciliation logs (`test_deployment_carries_request_correlation_id_into_timeline`).

## Metrics [Implemented, Prometheus at `/metrics`]

| Metric | Type | Labels (bounded) |
|---|---|---|
| `http_requests_total`, `http_request_duration_seconds` | counter, histogram | method, route *template*, status |
| `deployment_transitions_total` | counter | to_status, environment |
| `deployment_failures_total` | counter | failure_class, environment |
| `deployment_duration_seconds` | histogram | environment, outcome |
| `worker_jobs_total` | counter | outcome (succeeded, failed, retry_scheduled, skipped, internal_error, loop_error) |
| `queue_depth` | gauge | status |
| `queue_oldest_pending_age_seconds` | gauge | - |
| `reconciliations_total` | counter | result (converged_success, requeued, already_terminal, error) |

No label ever contains a model id, deployment id or user id (cardinality is bounded by design).

## Health and readiness [Implemented]

- `GET /health/live`: process is up; no dependencies.
- `GET /health/ready` (alias `GET /health`): checks DB, lists registered runtimes, reports oldest pending job; 503 when the DB is unreachable. Wire to the Kubernetes readiness probe; liveness to `/health/live`.
- [Designed] worker heartbeat row so readiness can also report "no worker alive for N seconds".

## Failure classification [Implemented]

| `failure_class` | Meaning | Automatic action | Operator action |
|---|---|---|---|
| `POLICY` | Governance rule no longer holds (approval revoked, archived, unknown runtime) | none | Fix approval, request again |
| `RUNTIME_TRANSIENT` | Runtime 5xx / connectivity | exponential backoff, up to `job_max_attempts` | Retry after runtime recovers |
| `TIMEOUT` | Runtime did not answer in time | same as transient | Check runtime capacity |
| `RUNTIME_PERMANENT` | Runtime rejected the artifact/config | none | Fix the artifact; do not blind-retry |
| `INTERNAL` | Our bug / infra | lease expires, reconciliation decides | Page engineering |

The class is on the deployment, on its timeline events, and in `deployment_failures_total`, so alerts route by cause.

## OpenTelemetry [Partially implemented]

Spans are emitted with the OTel **API** (`deployment.process`, `runtime.deploy`); they are no-ops until an SDK/exporter is configured, so there is no runtime cost or hard dependency. Enable with the standard `OTEL_*` variables via `opentelemetry-instrument` and the SDK + OTLP exporter in the image. Not yet done: auto-instrumentation of FastAPI/SQLAlchemy, W3C `traceparent` propagation into the worker (today correlation id plays that role).

## Operational dashboard proposal [Designed]

One Grafana board, four rows, each answering one question.

1. **Is the platform healthy?** API request rate, error ratio (5xx), p50/p95/p99 latency by route; readiness status; pod restarts.
2. **Is work flowing?** `queue_depth` by status, `queue_oldest_pending_age_seconds`, worker jobs by outcome, retries scheduled/min, reconciliations by result.
3. **Are deployments succeeding?** Deployment success rate by environment (7d), failures by `failure_class` (stacked), p95 request-to-terminal duration, current FAILED / stuck-in-DEPLOYING count (drill-down to `/events?model_id=`).
4. **Are models healthy?** Count of models by monitoring status (HEALTHY/DEGRADED/CRITICAL/NO_DATA), top-10 by drift and error rate, models with no inference in N minutes.

### Alerts (initial)

| Alert | Condition | Severity |
|---|---|---|
| Queue stalled | `queue_oldest_pending_age_seconds > 120` for 5 m | page |
| Deployment failure spike | `RUNTIME_PERMANENT` or `INTERNAL` rate above baseline for 15 m | page |
| Reconciliation errors | `reconciliations_total{result="error"}` increasing | page |
| Not ready | `/health/ready` failing 2 m | page |
| API error budget burn | 5xx ratio burning 2% budget in 1 h | ticket/page by burn rate |
| Production model CRITICAL | monitoring status CRITICAL for 10 m | notify owner |

SLO proposal: 99.9% of API requests succeed (excluding 4xx); 99% of deployments reach a terminal state within 10 minutes of request.
