# Known Limitations

Stated plainly so reviewers do not have to discover them.

**Scope**
- **No Angular frontend.** This G12 submission focuses on the G12 additions (architecture, governance, async, observability, leadership). The API contract (`docs/openapi.json`) and the isolation strategy for the UI are specified; the UI itself, its tests, and screenshots are not delivered. The compose file notes where the frontend service goes; the brief's acceptance scenarios 5 (Angular monitoring) and 9 (UI error surfacing) are covered at API level only.
- Architecture diagram is Mermaid in Markdown, not a PNG.

**Security**
- Authentication is a dev header adapter (`X-User-Id/Role/Tenant`). It must be replaced by verified JWT before any real use; there is no startup guard yet.
- Tenant isolation is application-enforced; Row-Level Security is designed, not enabled.
- No rate limiting.

**Scale and performance (not load-tested)**
- Offset pagination; metrics aggregated in Python over at most 50k rows per query (`truncated` flag).
- DB-backed queue polls; no `SKIP LOCKED` fast path yet.
- Tag filtering uses a text match over JSON (adequate for slug tags, not indexed).
- Metric samples are never expired.

**Reliability**
- Reverse drift (runtime lost state while DB says SUCCEEDED) is not detected automatically.
- A deployment stuck in DEPLOYING relies on lease expiry/reconciliation; there is no admin force-fail endpoint.
- The only runtime is a simulator; no real adapter has been exercised.
- The embedded worker is for local development; run the worker as its own process (compose does) elsewhere.

**Operability**
- OpenTelemetry: API-level spans only; no SDK/exporter wiring or auto-instrumentation.
- Docker image, compose stack and GitHub Actions workflow were written but not executed in my environment (no Docker; no GitHub run). The application, migrations and the full test suite were run locally against SQLite and PostgreSQL 14.
- Kubernetes manifests/Helm chart are described, not provided.

**Data model**
- Rolling back to a version does not check model/feature-schema compatibility.
- Version `stage` after rollback of a staging+production version reflects whichever slots still serve it (by design), which can surprise a reader expecting `APPROVED`.
