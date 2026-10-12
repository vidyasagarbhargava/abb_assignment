# G12 – Principal Software Engineer / Technical Lead Expectations

The submission must demonstrate implementation ability, technical leadership, architectural judgment and platform thinking.

## Mandatory G12 Additions

### Architecture and Scale
- Modular architecture with clear ownership boundaries
- Synchronous versus asynchronous design rationale
- Scaling approach for thousands of models and deployments
- Persistence, caching, queueing and concurrency choices
- Kubernetes or equivalent deployment view
- Trade-offs and rejected alternatives

### Governance
- Approval and promotion workflow
- Idempotency
- Concurrency strategy
- Rollback safety
- Audit history
- Role-based authorization design
- Environment promotion controls

### Asynchronous Processing
Implement at least one long-running workflow asynchronously, such as deployment, validation, drift analysis or metric aggregation.

### Observability
- Structured logs
- Correlation IDs
- Metrics
- Health/readiness
- Failure classification
- Operational dashboard proposal
- OpenTelemetry is desirable

### Leadership Artifacts
- At least two ADRs
- Delivery plan for a small team
- Risk register
- Near-term versus future roadmap
- Code-review checklist
- Production-readiness checklist

## Questions to Address
1. How would this scale from 100 to 10,000 models?
2. How are conflicting promotions prevented?
3. How is external success/internal database failure reconciled?
4. How are multiple model runtimes supported?
5. How would multi-tenancy work?
6. How are large metric volumes partitioned?
7. How is unsafe rollback prevented?
8. How are zero-downtime schema migrations handled?
9. How is Angular isolated from backend internals?
10. How would work be split across teams?
