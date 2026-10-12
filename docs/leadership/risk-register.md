# Risk Register

Likelihood (L) and Impact (I): 1 low - 5 high. Score = L x I.

| ID | Risk | L | I | Score | Mitigation (status) | Owner | Trigger / early warning |
|---|---|:-:|:-:|:-:|---|---|---|
| R1 | Runtime adapter is not truly idempotent, breaking exactly-once outcomes | 3 | 5 | 15 | Port contract + failure-injection tests (done); adapter contract test-kit (planned M1) | Runtime team | Duplicate runtime resources; `reconciliations_total{requeued}` high |
| R2 | A missed `tenant_id` filter leaks data across tenants | 2 | 5 | 10 | Principal-only service API, tenant test (done); RLS + non-owner role test (M4) | Platform core | Any cross-tenant finding in review/pentest |
| R3 | Metrics volume overwhelms OLTP database | 4 | 4 | 16 | Row cap + idempotent ingest (done); rollups + partitions (M3); TSDB option | Monitoring | Ingest p95 latency, table size growth, vacuum lag |
| R4 | DB-as-queue contention at high job rate | 2 | 3 | 6 | SKIP LOCKED fast path; `JobQueue` seam; move to SQS/Kafka | Deployment | `queue_oldest_pending_age_seconds`, DB CPU |
| R5 | Stuck DEPLOYING deployment blocks a slot | 3 | 3 | 9 | Lease expiry + reconciliation (done); alert on age, admin force-fail (M4) | Deployment | Deployment in DEPLOYING > threshold |
| R6 | Runtime state drifts from DB (reverse case) | 3 | 4 | 12 | Periodic slot-vs-runtime check (M4) | Deployment | Alert from drift check; incident reports |
| R7 | Dev-header authn used in production | 2 | 5 | 10 | Startup guard to refuse header auth when `deployment_env=production` (M1); JWT adapter | Platform core | Config review in readiness checklist |
| R8 | Blocking migration causes downtime | 3 | 4 | 12 | Expand/contract policy, CI migration checks (done); DDL linter (M2) | Platform core | Lock waits in deploy window |
| R9 | Approval workflow too rigid for plants (people bypass the platform) | 3 | 4 | 12 | Configurable staging-first rule (done); pilot feedback loop (M5); emergency path with extra audit | Product/lead | Deployments happening outside the platform |
| R10 | Frontend blocked or diverges from API | 3 | 3 | 9 | OpenAPI-generated client, contract tests; UI starts in M1 | Frontend | Hand-written types appearing in UI code |
| R11 | Single maintainer knowledge concentration | 3 | 3 | 9 | ADRs, runbooks, pairing, CODEOWNERS with 2 owners per area | Lead | Bus-factor review each milestone |
| R12 | Alert fatigue / unactionable alerts | 3 | 2 | 6 | Alerts keyed to `failure_class`, SLO burn rate, runbook link per alert | SRE | Page volume per week |
| R13 | Dependency vulnerabilities | 3 | 3 | 9 | `pip-audit` in CI (configured), Dependabot, pinned image digests | Platform core | CI failure |
| R14 | PostgreSQL-only DDL (audit trigger) untested on other engines | 2 | 2 | 4 | Dialect-guarded; PostgreSQL is the production target; CI runs it | Platform core | - |
