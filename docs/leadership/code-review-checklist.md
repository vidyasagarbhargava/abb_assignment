# Code-Review Checklist

**Correctness and domain**
- [ ] Business rules live in `domain/`, not in routes or the UI. New rules have unit tests including the refusal path.
- [ ] Every status change goes through `apply_transition` / the transition tables.
- [ ] Authorization is enforced in the service layer with the right permission for the environment.

**Concurrency and reliability**
- [ ] Invariants are enforced by a DB constraint, not only a pre-check. `IntegrityError`/`StaleDataError` paths are mapped to a domain error.
- [ ] Any external call is idempotent and keyed; intent is committed before the call; success recorded atomically after.
- [ ] Retries have a bound, backoff and a failure classification. No unbounded loops or sleeps in request paths.
- [ ] A new async step has a crash/recovery test.

**Data and migrations**
- [ ] Migration is additive (or an explicit contract step) and compatible with the previous app version. Large tables: batched backfill, concurrent index, `lock_timeout`.
- [ ] `alembic check` is clean. No app imports in migrations.
- [ ] New queries are tenant-scoped and indexed with `tenant_id` first; list endpoints are paginated and bounded.

**API**
- [ ] Request/response are typed Pydantic models; requests forbid unknown fields; ORM types never escape.
- [ ] Errors use the shared envelope with a stable `code`. Status codes follow the conventions (409 state conflict, 422 invalid input, 403 permission).
- [ ] Mutating endpoints that clients may retry accept `Idempotency-Key`. OpenAPI is regenerated and the diff reviewed for breaking changes.

**Security**
- [ ] No secrets, tokens or PII in code, logs, errors or test fixtures. Tenant comes from the Principal only.
- [ ] Inputs validated at the boundary; no string-built SQL; new dependencies reviewed and `pip-audit` clean.

**Observability**
- [ ] New failure modes log structured events with `correlation_id` and increment a bounded-cardinality metric.
- [ ] No ids/user input used as metric labels.

**Tests and quality**
- [ ] Tests assert behaviour (outcomes, error codes), not implementation. Failure paths covered, not only happy paths.
- [ ] Concurrency claims are tested with real threads against the DB.
- [ ] `ruff`, `mypy --strict`, and tests pass on SQLite and PostgreSQL; coverage did not drop.
- [ ] Docs/ADR updated if a seam, schema ownership, or policy changed.
