# API Design

Interactive docs: `/docs` (Swagger UI) and `/redoc` on the running API. Static contract: [openapi.json](openapi.json) (`make openapi`).

## Conventions
- Resource-oriented, JSON. Identity headers (dev adapter): `X-User-Id`, `X-User-Role`, `X-Tenant-Id`.
- `X-Correlation-ID` accepted/returned on every call.
- Lists: `{items, total, limit, offset}`; `limit` 1-200.
- Long-running operations return `202` + `Location` and are observed via `GET /deployments/{id}` and its `/events`.
- Mutations that clients retry accept `Idempotency-Key`.
- Requests reject unknown fields (422) to surface client typos early.

## Error envelope
```json
{"error": {"code": "STAGING_REQUIRED", "message": "...", "details": null, "correlation_id": "..."}}
```

| HTTP | Used for | Example codes |
|---|---|---|
| 401 | missing identity | `UNAUTHENTICATED` |
| 403 | role lacks permission | `INSUFFICIENT_PERMISSION`, `UNKNOWN_ROLE` |
| 404 | unknown or other-tenant resource | `MODEL_NOT_FOUND`, `VERSION_NOT_FOUND`, `DEPLOYMENT_NOT_FOUND` |
| 409 | state/precondition conflict | `APPROVAL_REQUIRED`, `STAGING_REQUIRED`, `DEPLOYMENT_IN_PROGRESS`, `PROMOTION_CONFLICT`, `VERSION_ALREADY_ACTIVE`, `ROLLBACK_*`, `INVALID_STAGE_TRANSITION`, `SELF_APPROVAL_FORBIDDEN`, `CONCURRENT_MODIFICATION`, `MODEL_EXISTS`, `VERSION_EXISTS`, `RETRY_NOT_ALLOWED` |
| 422 | invalid input | `VALIDATION_ERROR` (with per-field `details`), `IDEMPOTENCY_KEY_REUSED`, `UNKNOWN_RUNTIME`, `BATCH_TOO_LARGE`, `SIMULATION_DISABLED` |
| 5xx | our fault, no internals leaked | `INTERNAL_ERROR` |

## Endpoints

| Method and path | Purpose | Permission |
|---|---|---|
| `POST /models`, `GET /models` (`q, owner, tag, limit, offset`), `GET /models/{id}` | Registry | write / read |
| `POST /models/{id}/versions`, `GET /models/{id}/versions` (`stage`), `GET .../versions/{v}` | Versions | write / read |
| `POST .../versions/{v}/validate` | DRAFT -> VALIDATED | `version:validate` |
| `POST .../versions/{v}/approve`, `.../reject` | Approval (four-eyes) | `version:approve` |
| `POST .../versions/{v}/archive` | Retire (not while serving) | `version:archive` |
| `POST /deployments` (202) | Request deployment; `Idempotency-Key`; optional `expected_current_deployment_id`, `simulate_failure` | `deploy:<env>` |
| `GET /deployments` (`model_id, environment, status, version`), `GET /deployments/{id}` | Status tracking, history | read |
| `GET /deployments/{id}/events`, `GET /events?model_id=` | Timeline | read |
| `POST /deployments/{id}/retry` (202) | Retry a FAILED deployment | `deploy:<env>` |
| `POST /deployments/{id}/rollback` (202) | Roll back the serving deployment | `deploy:<env>` |
| `GET /models/{id}/metrics` (`environment, version, window_hours, bucket`) | Latency, throughput, error, quality, drift, availability, last successful inference, monitoring status, series | read |
| `POST /models/{id}/metrics` (202) | Idempotent batch ingest (<= 1000) | `metrics:write` |
| `GET /models/{id}/compare?base=&candidate=` | Version comparison with deltas | read |
| `GET /audit` | Audit log | `audit:read` |
| `GET /health`, `/health/live`, `/health/ready`, `/metrics` | Operability | none |

## Evolution
Additive changes only inside `v1` (new optional fields, new endpoints). Breaking changes ship as `/v2` with a deprecation period. The Angular client is generated from `openapi.json`, so drift is a compile-time failure.
