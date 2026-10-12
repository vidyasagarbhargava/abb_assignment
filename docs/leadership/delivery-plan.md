# Delivery Plan (small team)

**Team assumed**: 1 tech lead (hands-on), 2 backend, 1 frontend, 0.5 SRE. Six two-week-equivalent increments are not needed; this is a 10-week plan in five 2-week milestones. Every milestone ends in something demoable and shippable behind a feature flag.

| Milestone | Goal | Backend | Frontend | SRE | Exit criteria |
|---|---|---|---|---|---|
| M0 (done in this repo) | Walking skeleton of the risky parts | Registry, governed async deploy, rollback, audit, RBAC, metrics, reconciliation, CI | - | compose, health, Prometheus | 66 tests green on SQLite + PostgreSQL; failure-injection tests pass |
| M1 wk 1-2 | Production auth + first real runtime | OIDC/JWT `Principal`; first real adapter (e.g. KServe) + adapter contract tests; RLS spike | Scaffold app, generate client from `openapi.json`, inventory + version detail | Helm chart, migration Job, secrets | Real deploy to a staging cluster via the API |
| M2 wk 3-4 | Operator experience | Keyset pagination; timeline/event filters; `traceparent` propagation | Deployment view, event timeline, error/loading/empty states, approval screen | Dashboards v1 + alerts | A release manager can promote and roll back from the UI |
| M3 wk 5-6 | Monitoring at scale | Hourly/daily rollups, partitioned `metric_points`, aggregation job | Monitoring dashboard, version comparison | Retention jobs, capacity test | 1k models x 3 envs sustained ingest within p95 targets |
| M4 wk 7-8 | Hardening | Drift check (slot vs runtime), force-fail admin action, per-tenant quotas, RLS on | Accessibility, responsive pass, UI tests | Load/chaos test, runbooks, SLO burn alerts | Production-readiness checklist 100% |
| M5 wk 9-10 | Pilot | Fix-forward from pilot; multi-runtime second adapter | Polish | On-call rotation | One plant on production for two weeks without Sev-1 |

## Working agreements
- Trunk-based, short-lived branches, PRs required, the [code-review checklist](code-review-checklist.md) is the PR template.
- Definition of done: tests (unit + API + failure path), migration compatible with N-1 code, metrics/logs for new failure modes, docs/ADR if a seam changes.
- Contract-first: the OpenAPI diff is reviewed in PRs; the frontend never waits on backend internals.
- ADRs for any change to a seam (domain API, runtime port, schema ownership, tenancy).
- Weekly 30-minute risk-register review.

## Sequencing rationale
Retire the highest-uncertainty risks first (correctness under concurrency/failure, which M0 already addresses; then real runtime integration in M1), delay what is replaceable (caching, UI polish, TSDB). The UI starts in M1 against the generated client, so frontend and backend run in parallel from week 1.

## What I would cut if the schedule slips
Version comparison UI, TSDB migration, second runtime adapter. Not negotiable: authn/RBAC, audit, reconciliation, rollback safety.
