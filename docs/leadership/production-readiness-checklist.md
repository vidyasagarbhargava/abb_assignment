# Production-Readiness Checklist

Status: [x] done in this repo, [ ] required before production.

**Security**
- [ ] Verified OIDC/JWT authentication replaces header adapter; service refuses to start with header auth when `MLOPS_DEPLOYMENT_ENV=production`
- [x] RBAC with separation of duties, tested
- [x] Tenant scoping on all queries, tested
- [ ] Row-Level Security enabled and verified with a non-owner DB role
- [ ] TLS at ingress; secrets from the secret store; DB credentials rotated
- [x] `allow_failure_simulation` configurable (must be false in production)
- [x] No secrets in Git; `.env.example` only
- [ ] Rate limiting and request size limits at the gateway
- [x] Dependency audit configured in CI

**Reliability**
- [x] Idempotent create/retry/rollback; idempotent runtime port
- [x] Crash and external-success/DB-failure recovery tested
- [x] Bounded retries with backoff; failure classification
- [ ] Slot-vs-runtime drift check and stuck-deployment alert
- [ ] Load test at target volume; chaos test (kill worker mid-deploy, DB failover)
- [ ] Graceful shutdown tested (worker drains or lease expires cleanly)

**Data**
- [x] Migrations versioned; `alembic check` and up/down in CI on PostgreSQL
- [ ] Backups + PITR configured and a restore rehearsed
- [x] Audit append-only (ORM + PostgreSQL trigger)
- [ ] Retention + partitioning for `metric_points`, `deployment_events`, `jobs`
- [ ] Audit export to WORM/SIEM

**Observability**
- [x] Structured JSON logs with correlation ids
- [x] Prometheus metrics; readiness/liveness endpoints
- [ ] Dashboards and alerts deployed, each alert with a runbook
- [ ] OTel SDK + exporter configured; trace propagation into the worker
- [ ] SLOs agreed and burn-rate alerts enabled

**Delivery**
- [x] CI: lint, types, tests (SQLite + PostgreSQL), migration check, audit, image build
- [x] Container runs as non-root with a healthcheck
- [ ] Helm/K8s manifests: resource limits, PDB, HPA/KEDA, migration Job, anti-affinity
- [ ] Rollout/rollback procedure for the platform itself documented and rehearsed
- [ ] On-call rotation, escalation path, runbooks (stuck deployment, queue stalled, DB failover)

**Product**
- [ ] Angular UI complete with loading/empty/error/success states, accessibility pass
- [ ] Pilot plant signed off on the approval workflow
