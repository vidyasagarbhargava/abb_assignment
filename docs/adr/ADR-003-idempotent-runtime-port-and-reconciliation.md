# ADR-003: Runtime port with idempotent adapters and describe()-based reconciliation

## Status
Accepted

## Context
The platform changes the real world (a serving endpoint) and its own database. These cannot be
committed atomically. A crash or DB failure between "runtime succeeded" and "we recorded it" must
neither lose the success nor double-deploy. We also must support several runtimes (KServe,
SageMaker, edge) behind one workflow.

## Decision
- Define a small `ModelRuntime` port: `deploy(request)` idempotent on `idempotency_key` (= deployment id) and `describe(key)`.
- Persist intent (`DEPLOYING`) before the external call; record success in a single transaction after.
- On lease expiry, reconcile: if `describe()` shows READY, converge to SUCCEEDED without a second side effect; if the runtime has no record, requeue.
- Adapters raise only `RuntimeTransientError`, `RuntimeTimeoutError`, `RuntimePermanentError`; the worker maps these to retry/fail and to `failure_class`.
- Runtime is chosen per version (`model_versions.runtime`) and resolved through `RuntimeRegistry`.

## Alternatives Considered
- **Two-phase commit / XA**: no model-serving platform supports it.
- **Saga with compensating actions**: heavier; idempotent forward recovery is sufficient because deploy is convergent ("make runtime match desired state").
- **Fire-and-forget + periodic full resync only**: simpler, but leaves long unexplained windows and no per-deployment timeline.
- **Runtime-specific code in the worker**: would multiply branches per runtime and block team-parallel work.

## Consequences
### Positive
- Failure-injection test proves exactly-one side effect with DB failure after external success.
- New runtime = one adapter + contract tests; no workflow changes.
### Negative
- Requires every adapter to implement true idempotency and a reliable `describe()`; weak adapters break the guarantee.
- Reverse drift (runtime lost state, DB says success) is not yet detected automatically.
### Follow-up Actions
- Adapter contract test-kit.
- Periodic slot-vs-runtime drift check with alerting.
- Propagate `traceparent` through the job row.
