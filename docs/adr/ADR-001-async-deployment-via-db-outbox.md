# ADR-001: Asynchronous deployments via a database-backed outbox queue

## Status
Accepted

## Context
Deployments call external runtimes that take seconds to minutes and fail transiently. They must
survive API restarts, be retryable, be observable, and must never be "recorded but never executed"
or "executed but never recorded". The system is a control plane with human-rate write volume.

## Decision
`POST /deployments` validates, then in **one transaction** writes the deployment (REQUESTED), its
timeline event, an audit record and a `jobs` row, and returns `202`. Separate worker processes claim
jobs with a compare-and-swap lease (`UPDATE ... WHERE status='PENDING'`), execute with idempotent
runtime calls, retry transient failures with exponential backoff, and record the outcome in one
transaction. A reconciler recovers expired leases using the runtime's `describe()`.

## Alternatives Considered
- **FastAPI `BackgroundTasks`**: lost on restart, no retry, no visibility. Rejected.
- **Celery + Redis/RabbitMQ**: mature, but adds a second stateful system and a dual-write problem (DB commit and broker publish are not atomic) which then needs its own outbox anyway.
- **Kafka/SQS directly**: right at larger scale; premature operational cost now.
- **Synchronous call in the request**: couples API capacity to runtime latency and loses work on timeouts/restarts.
- **Postgres `LISTEN/NOTIFY` / `SKIP LOCKED` only**: a good optimisation, not portable to the SQLite dev/test path; the CAS claim gives the same safety and can add `SKIP LOCKED` on PostgreSQL later.

## Consequences
### Positive
- Atomic enqueue; no dual write. Queue state is inspectable with SQL and exposed as metrics.
- Same code path in dev (SQLite), CI and production (PostgreSQL). Easy deterministic tests (`worker.run_once()`).
- Crash safety is testable (see reliability tests).
### Negative
- Polling adds latency (default 0.5 s) and read load on `jobs`.
- DB is both system of record and queue; a hot queue competes with OLTP.
- Not suited to >~1k jobs/s without moving the transport.
### Follow-up Actions
- Add `SKIP LOCKED` fast path on PostgreSQL.
- Introduce a `JobQueue` interface and an SQS/Kafka implementation when `queue_oldest_pending_age_seconds` or DB CPU says so.
- Per-tenant fairness in claim order.
