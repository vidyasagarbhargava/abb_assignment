# Test Strategy

Run: `make test` (or `cd backend && pytest`). Cross-database: `TEST_DATABASE_URL=postgresql://... pytest`. Current result: 66 tests passing on SQLite and on PostgreSQL 14; 93% line coverage (CI gate: 85%).

## Unit tests (`tests/unit`)
Pure domain: version and deployment state machines (valid and invalid transitions), promotion policy (approval, staging-first, archived), rollback planning (target selection, no target, archived target, rolled-back history ignored, only-from-succeeded), RBAC matrix, percentile/summary/monitoring classification, last-successful-inference edge case.

## API tests (`tests/api`)
Through HTTP with the real app, DB and worker driven deterministically (`worker.run_once()`):
- **Acceptance** (`test_acceptance.py`): the ten brief scenarios plus async semantics (202 before execution), transient self-healing, retry exhaustion, timeout classification, rollback happy and unsafe paths, correlation-id propagation, health/metrics endpoints, pagination/filters, error envelope.
- **Governance** (`test_governance.py`): RBAC at the API, four-eyes, skip-validation, validation gate, tenant isolation, duplicate registration, conflicting promotions, stale promotion guard, archive-while-serving, audit trail content and permissions, simulation switch.
- **Extensibility** (`test_extensibility.py`): unknown runtime, per-version runtime routing, comparison deltas, empty metrics, ingest validation and batch limit, OpenAPI contract.

## Integration / failure-mode tests (`tests/integration`)
- External success then DB failure -> reconciled, exactly one side effect.
- Worker dies after claim -> lease respected, then recovered.
- Two workers racing for one job -> exactly one wins.
- Six concurrent identical idempotent requests -> one deployment.
- Four concurrent conflicting promotions -> one winner.
- Audit log rejects UPDATE/DELETE.
- Approval revoked between request and execution -> `POLICY` failure, runtime never called.
- Backoff delays retries.

## Angular tests
Not applicable to this submission (no UI). Planned: component tests for loading/empty/error/success, service tests against the generated client with HTTP mocking, and a Playwright run of the end-to-end scenario below.

## End-to-end scenario
Register model -> register version -> validate -> approve (different user) -> deploy staging -> deploy production -> ingest/view metrics -> roll back. Covered by `test_acceptance.py` across `test_1_to_4`, `test_5`, `test_7`.

## CI, coverage and limitations
`.github/workflows/ci.yml`: ruff, mypy `--strict`, pytest on Python 3.11/3.12 with SQLite, pytest again against PostgreSQL service, migration upgrade/check/downgrade, `pip-audit`, Docker build. Known gaps: no load/performance tests; no chaos test against a real runtime; concurrency tests use threads in one process (they exercise DB constraints, not multi-host timing); the Docker image and compose file were authored but not built in my environment (no Docker available); CI has not run on GitHub yet.
