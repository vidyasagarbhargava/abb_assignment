# ADR-002: Enforce promotion concurrency and idempotency in the database

## Status
Accepted

## Context
Two operators (or a retrying client and its original request) can promote to the same
model/environment at the same time. Application-level "check then insert" is racy across API
replicas. A wrong outcome here means two versions believing they own an environment.

## Decision
Encode invariants as constraints:
1. Partial unique index on `deployments(tenant, model, environment) WHERE status IN (REQUESTED, VALIDATING, DEPLOYING)`: at most one in-flight deployment per slot.
2. Unique `(tenant, idempotency_key)`: one deployment per client key.
3. Optimistic `row_version` (SQLAlchemy `version_id_col`) on versions, deployments, environment slots.
4. An `environment_slots` pointer is the single source of truth for what is serving; version stage is derived from it.
5. Optional client guard `expected_current_deployment_id` for stale-view protection.

Services do friendly pre-checks for error messages, but on `IntegrityError` they re-classify (replay vs conflict). Concurrency is tested with racing threads on SQLite and PostgreSQL.

## Alternatives Considered
- **Distributed lock (Redis/etcd/ZooKeeper)**: new failure mode (lock expiry mid-operation, split brain) guarding data that already has a transactional home.
- **`SELECT ... FOR UPDATE` on a slot row only**: works on PostgreSQL, not meaningful on SQLite, and easy to forget in new code paths; constraints cannot be forgotten.
- **Single-writer / serialised queue per model**: correct but couples API latency to queue and still needs a DB invariant for safety.

## Consequences
### Positive
- Correct under any number of API replicas and workers; new code paths inherit the guarantee.
- Losers get a precise 409 with a code the UI can act on.
### Negative
- Partial unique indexes are dialect-specific (supported on PostgreSQL and SQLite; not MySQL).
- Constraint violations must be mapped to domain errors carefully (a real bug we hit and fixed: flush outside the try block; and a same-key twin racing the slot check).
- A slot is blocked by a stuck DEPLOYING deployment until reconciliation or an operator acts.
### Follow-up Actions
- Alert on deployments in DEPLOYING longer than a threshold.
- Admin "force-fail" action with audit for operators.
