# Roadmap: Near-term vs Future

## Near-term (next ~10 weeks; see delivery plan)
- OIDC/JWT authentication; refuse header auth in production.
- First real runtime adapter and adapter contract test-kit.
- Angular application on a generated client (inventory, version detail, deployments, timeline, monitoring, approvals).
- Keyset pagination, event timeline filters.
- Metric rollups, monthly partitions, retention jobs.
- Helm chart, migration Job, dashboards and alerts, runbooks.
- Slot-vs-runtime drift detection; admin force-fail with audit.
- Row-Level Security; per-tenant quotas.
- OpenTelemetry SDK + auto-instrumentation + trace propagation into the worker.

## Future (after pilot, driven by evidence)
| Theme | Item | Trigger to start |
|---|---|---|
| Scale | Queue transport swap to SQS/Kafka behind `JobQueue` | Oldest-pending age or DB CPU SLO breached |
| Scale | TSDB/columnar store for raw metrics | > ~1 B rows/month or rollup query p95 breach |
| Safety | Progressive delivery (canary/shadow, automated rollback on metric breach) | Pilot teams ask for it; needs reliable quality metrics |
| Safety | Model/data lineage, artifact signing and scan gates, SBOM | Compliance requirement |
| Governance | Multi-step/approver-group approvals, change windows, emergency-change flow | Plants with formal change management |
| Monitoring | Async drift analysis jobs, alert routing to owners, SLOs per model | After rollups exist |
| Platform | Event stream (CDC/outbox -> Kafka) for downstream consumers | Other systems need deployment events |
| Platform | Cell-based deployment per region/plant, dedicated DB per regulated tenant | Data residency / blast-radius needs |
| Product | Self-service runtime onboarding, cost attribution per tenant | Platform adoption growth |

Deliberately not on the roadmap: building our own serving runtime or training platform.
