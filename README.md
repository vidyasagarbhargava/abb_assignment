# MLOps Platform - G12 Submission (Principal Software Engineer / Technical Lead)

**Role level: G12.** This submission covers the G12 additions in `02_g12_expectations.md`: architecture and scale, governance, asynchronous processing, observability and leadership artifacts, backed by a working, tested Python backend. **The Angular UI is not included** (see [known limitations](docs/known-limitations.md)).

## Problem
An industrial organization runs many ML models across plants and environments. The platform registers and versions models, gates promotion behind approval, deploys asynchronously to external runtimes, monitors them, and supports retry and safe rollback, with an audit trail and tenant isolation.

## Architecture in one paragraph
Stateless FastAPI API -> services -> pure domain rules. Deployments are accepted with `202` and executed by separate workers via a transactional-outbox `jobs` table. Safety invariants (one in-flight deployment per slot, idempotency, optimistic locking) are enforced by database constraints. Runtimes sit behind an idempotent `ModelRuntime` port; crashes between "runtime succeeded" and "DB recorded" are reconciled with `describe()`. Full detail: [docs/architecture.md](docs/architecture.md).

## Stack
Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL (SQLite for dev/test), Pydantic v2, Prometheus client, OpenTelemetry API, pytest, ruff, mypy (strict), Docker, GitHub Actions.

## Run it
```bash
# local (SQLite, API + embedded worker)
python -m venv .venv && source .venv/bin/activate
make install migrate seed run          # http://127.0.0.1:8000/docs

# containers (PostgreSQL, separate api and worker services)
cp .env.example .env && docker compose up --build
```
Identity is a dev stand-in: send `X-User-Id`, `X-User-Role` (`viewer|developer|approver|release_manager|service|admin`) and `X-Tenant-Id`.

## Tests
```bash
make check                                  # ruff + mypy + pytest (+coverage)
TEST_DATABASE_URL=postgresql://user@localhost/db pytest   # same suite on PostgreSQL
```
66 tests; 93% coverage. Strategy: [docs/test-strategy.md](docs/test-strategy.md).

## Sample workflow
```bash
H='-H X-Tenant-Id:acme -H content-type:application/json'
as() { role=$1; shift; curl -s $H -H "X-User-Id: u-$role" -H "X-User-Role: $role" "$@"; }
B=http://127.0.0.1:8000
as developer -XPOST $B/models -d '{"model_id":"pump","name":"Pump","owner":"reliability"}'
as developer -XPOST $B/models/pump/versions -d '{"version":"1.0.0","framework":"sklearn","artifact_uri":"s3://models/pump/1.0.0","training_data_ref":"s3://data/pump"}'
as developer -XPOST $B/models/pump/versions/1.0.0/validate
as approver  -XPOST $B/models/pump/versions/1.0.0/approve -d '{}'
as release_manager -XPOST $B/deployments -H 'Idempotency-Key: demo-0001' -d '{"model_id":"pump","version":"1.0.0","environment":"staging"}'
as viewer "$B/deployments?model_id=pump"        # REQUESTED -> ... -> SUCCEEDED
```
(zsh users: run in bash or expand `$H` into an array.) Add `"simulate_failure":"TRANSIENT_ONCE"` to watch retry and the timeline at `/events?model_id=pump`.

## API documentation
Swagger UI `/docs`; static contract [docs/openapi.json](docs/openapi.json); conventions [docs/api-design.md](docs/api-design.md).

## Where to look (G12 checklist)

| Expectation | Location |
|---|---|
| Modular architecture, ownership, sync vs async, scale, persistence/cache/queue/concurrency, K8s view, trade-offs | [docs/architecture.md](docs/architecture.md) |
| Approval/promotion, idempotency, concurrency, rollback safety, audit, RBAC, environment controls | [docs/governance.md](docs/governance.md) |
| Async workflow (deployment) | `backend/app/worker.py`, ADR-001 |
| Structured logs, correlation IDs, metrics, health, failure classes, dashboard, OTel | [docs/observability.md](docs/observability.md) |
| The 10 questions | [docs/g12-questions.md](docs/g12-questions.md) |
| ADRs (5) | [docs/adr/](docs/adr/) |
| Delivery plan, risk register, roadmap, review and readiness checklists | [docs/leadership/](docs/leadership/) |
| Known limitations | [docs/known-limitations.md](docs/known-limitations.md) |

## Repository layout
```
backend/app/{domain,services,api,runtime}   pure rules | use cases + transactions | HTTP | runtime port
backend/app/worker.py                       async execution + reconciliation
backend/migrations/                         Alembic (expand/contract)
backend/tests/{unit,api,integration}
docs/  scripts/seed.py  artifacts/ (provided sample data)  docker-compose.yml  Makefile  .github/workflows/ci.yml
```

## Future improvements
See [docs/leadership/roadmap.md](docs/leadership/roadmap.md).
