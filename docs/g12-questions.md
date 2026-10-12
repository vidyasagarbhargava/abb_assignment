# G12 Questions

Each answer says what exists in the repo (**Implemented**, with the test that proves it) and what is a design for later (**Designed**).

## 1. How would this scale from 100 to 10,000 models?

Where the load actually is: model/version/deployment writes are human-rate (tiny). Reads (inventory, timelines) grow with models. **Metrics are the real scaling problem**: 10,000 models x 3 environments x 1 sample/min = 30,000 series, ~43 M rows/day.

| Tier | 100 models (today) | 10,000 models |
|---|---|---|
| API | 1-2 stateless replicas | HPA on CPU + p95; no change to code |
| Registry/deployments | Single PostgreSQL | Same, plus read replica for list/timeline endpoints; indexes already lead with `tenant_id` |
| Queue/workers | DB outbox, 1-2 workers | Workers scale on `queue_oldest_pending_age_seconds` (KEDA). The CAS claim is contention-free per job; if polling load matters, add `FOR UPDATE SKIP LOCKED` on PostgreSQL or move the transport to SQS/Kafka behind the same claim/ack seam |
| Metrics | Raw rows, Python aggregation (capped at 50k rows/query, `truncated` flag) | Rollups + partitioning + TSDB (Q6) |
| Lists | `limit/offset` | Keyset pagination on `(created_at, id)` |
| Runtime fan-out | In-process thread pool | Per-runtime worker pools and per-runtime rate limits so one slow cluster cannot starve others |

Honest limits today: offset pagination degrades on deep pages; the metrics endpoint aggregates in Python; there is no per-tenant rate limiting. These are listed in [known-limitations.md](known-limitations.md) with the trigger that makes each one urgent. Load testing has not been done; the numbers above are capacity reasoning, not measurements.

## 2. How are conflicting promotions prevented?

**Implemented.** Three layered guards, the last two enforced by the database:

1. **Partial unique index** `uq_active_deployment_per_slot` on `(tenant, model, environment) WHERE status IN (REQUESTED, VALIDATING, DEPLOYING)`. Two simultaneous promotions cannot both be in flight; the loser gets 409 `DEPLOYMENT_IN_PROGRESS`.
2. **Optimistic promotion guard**: the client may send `expected_current_deployment_id`. If the slot head changed since the operator looked, 409 `PROMOTION_CONFLICT` returns the real head.
3. **Optimistic `row_version`** on versions, deployments, slots: concurrent approve/archive/complete races become 409 `CONCURRENT_MODIFICATION`, never lost updates.

Tests: `test_concurrent_conflicting_promotions_one_winner` (4 racing threads, exactly one 202, three 409; run on SQLite and PostgreSQL), `test_stale_promotion_guard`, `test_conflicting_promotions_second_request_loses`.

## 3. How is external success / internal database failure reconciled?

**Implemented**, proven by `test_external_success_then_db_failure_is_reconciled_without_second_side_effect`.

The dangerous window is: runtime deployed, then our final commit fails. Protocol:

1. Commit `DEPLOYING` *before* calling the runtime (durable intent).
2. Call runtime with `idempotency_key = deployment_id`. Repeats are no-ops.
3. Record success + slot pointer + stages + job DONE in one transaction.
4. If step 3 fails or the worker dies, the job stays RUNNING and its lease expires. `recover_stale_jobs` then asks `runtime.describe(deployment_id)`:
   - runtime says READY -> converge to SUCCEEDED (`reconciled_success` event), no second deploy;
   - runtime has no record -> requeue; the retried deploy is idempotent.
5. The reverse case (DB says success, runtime lost it) is a [Designed] periodic drift check: compare each slot head to `describe()` and raise an alert/auto-redeploy.

The test injects a failure into the final commit, asserts `side_effects == 1` and `calls == 1` after recovery.

## 4. How are multiple model runtimes supported?

**Implemented.** `runtime/base.py` defines a small `ModelRuntime` port: `deploy(request)` (idempotent on key) and `describe(key)`. `RuntimeRegistry` maps names to adapters; each *version* records its `runtime`. Adding KServe, SageMaker, Triton or an edge fleet means one adapter class plus registration in `bootstrap.py`; no change to the deployment service, worker, or API. Adapters raise three typed errors (`Transient`, `Timeout`, `Permanent`) which the worker maps to retry/fail and to the `failure_class` taxonomy. Test: `test_versions_route_to_their_own_runtime_adapter`. [Designed] Adapter contract test-kit that every adapter must pass (idempotency, timeout mapping, describe-after-deploy).

## 5. How would multi-tenancy work?

**Implemented (shared schema, application-enforced).** Every table has `tenant_id`; models are keyed `(tenant_id, model_id)`; every query filters by the `Principal.tenant_id`; idempotency keys are per tenant; cross-tenant ids return 404 so existence does not leak. Test: `test_tenant_isolation` (including the same slug living independently in two tenants).

**Designed hardening**: PostgreSQL Row-Level Security with `SET LOCAL app.tenant_id` per transaction, so a missing `WHERE` becomes an empty result rather than a leak; per-tenant quotas and rate limits at the gateway; per-tenant queue fairness (round-robin claim) so one tenant's burst cannot starve others; optional dedicated database for regulated tenants because the tenant key is already on every row. The tenant comes from a verified JWT claim in production, never from a client-supplied header.

## 6. How are large metric volumes partitioned?

**Implemented**: idempotent batch ingest (`ON CONFLICT DO NOTHING` on `(tenant, model, version, env, ts)`), composite index `(tenant, model, env, ts)`, bounded query window and row cap with a `truncated` flag.

**Designed** (in order of need):
1. Native PostgreSQL range partitioning of `metric_points` by month (sub-partition by hash of `tenant_id` if one tenant dominates); drop/detach old partitions instead of `DELETE`.
2. Pre-aggregated rollups (`metric_rollup_hour`, `_day`: count, sum, min, max, p95 sketch) written by an async aggregation job; dashboards read rollups only. Raw retention 14-30 days, hourly 1 year, daily indefinitely.
3. Beyond ~1 B rows/month: move raw samples to a TSDB/columnar store (Timescale, ClickHouse, Prometheus remote-write) behind `MetricsService`; the registry database keeps only status summaries.
4. Ingest through a queue/stream so bursts do not hit the OLTP database.

## 7. How is unsafe rollback prevented?

**Implemented** in `domain/lifecycle.plan_rollback` + `DeploymentService.rollback`. A rollback is refused unless all hold:

| Rule | Error code |
|---|---|
| The deployment being rolled back is the one currently serving that slot (stale UI cannot roll back the wrong thing) | `ROLLBACK_NOT_CURRENT` |
| It is SUCCEEDED | `ROLLBACK_NOT_SUCCEEDED` |
| No deployment is in flight for the slot | `DEPLOYMENT_IN_PROGRESS` |
| An earlier SUCCEEDED deployment of a *different* version exists (ROLLED_BACK ones are never targets: they were judged bad) | `ROLLBACK_NO_TARGET` |
| Target version is still approved and not archived | `ROLLBACK_TARGET_UNSAFE` |
| Caller holds the permission for that environment (production needs `deploy:production`) | 403 |

Rollback is a new, audited, async deployment, not an in-place mutation, and uses idempotency keys. Tests: `test_unsafe_rollbacks_are_refused`, `test_rollback_*` unit tests. [Designed] additional gates: schema/feature-contract compatibility check between versions, and a health gate that auto-aborts if the rollback target's own metrics are CRITICAL.

## 8. How are zero-downtime schema migrations handled?

Process (expand / migrate / contract), with the migration run as a pre-rollout Job:

1. **Expand**: additive only (new nullable column / new table / new index created `CONCURRENTLY`). Old code ignores it. Set `lock_timeout` so DDL fails fast instead of queueing behind traffic.
2. **Deploy code** that writes both old and new shapes and reads the new with fallback. API and workers may run mixed versions during rollout, so code N must work on schema N and N+1.
3. **Backfill** in small batches, resumable, outside the DDL transaction.
4. **Contract** in a *later* release after no running code reads the old shape: drop column / constraint.
5. Never rename or retype in place; add-copy-switch-drop.

**Implemented**: Alembic baseline (`0001`), `alembic check` + `upgrade head` + `downgrade base` in CI against PostgreSQL, migrations free of application imports (types frozen as plain SQLAlchemy). **Designed**: lint rule that rejects blocking DDL (`DROP COLUMN`, non-concurrent index, `NOT NULL` without default) in non-contract migrations.

## 9. How is Angular isolated from backend internals?

- The only coupling is the public REST contract: `docs/openapi.json` (generated from the Pydantic schemas in `api/schemas.py`; `make openapi`). The UI generates its TypeScript client from it, so a breaking change shows up as a compile error, not a runtime surprise.
- Response schemas are separate from ORM models; internals (table names, `row_version`, `tenant_id`, job rows) are never serialized.
- One error envelope (`{error:{code,message,details,correlation_id}}`) with stable machine `code`s; the UI branches on `code`, never on message text.
- Async work is exposed as resources (202 + `Location`, poll `GET /deployments/{id}` or the event timeline), so the UI does not know a queue exists.
- `X-Correlation-ID` is returned on every response and shown in UI error toasts so support can find the exact server log line.
- CORS allow-list and auth are gateway/edge concerns; the UI holds no secrets. Versioning: additive changes only within `v1`; breaking changes go to `/v2` with a deprecation window.
- [Designed] consumer-driven contract tests run in backend CI against the generated client.

## 10. How would work be split across teams?

| Team (size) | Owns | Interfaces it exposes/consumes |
|---|---|---|
| Platform core (2) | `domain/`, persistence, migrations, authn/RBAC, CI | Domain rules library, `Principal` |
| Deployment & runtime (2) | `deployment_service`, `worker`, `runtime/` adapters, reconciliation | `ModelRuntime` port; deployment REST |
| Registry & governance (1-2) | registry, approvals, audit, policy rules | Registry REST; policy extension points |
| Monitoring (1-2) | ingest, rollups, status classification, alerts | Metrics REST; rollup tables |
| Frontend (2) | Angular app, generated client, UI tests | OpenAPI contract only |
| SRE/platform (1) | Helm/K8s, observability stack, SLOs, runbooks | `/metrics`, `/health/*`, OTel |

Seams that make this possible without constant coordination: the pure `domain/` package, the `ModelRuntime` port, the OpenAPI contract, and the DB schema owned through migrations. CODEOWNERS maps directories to teams; cross-seam changes need an ADR. See [leadership/delivery-plan.md](leadership/delivery-plan.md) for sequencing for a small team.
