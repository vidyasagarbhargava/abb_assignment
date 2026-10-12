# ADR-004: Shared-schema multi-tenancy keyed by tenant_id

## Status
Accepted (with a planned hardening step)

## Context
The organization operates many plants and business units; some may need isolation from others.
Tenant count is expected in the tens to low hundreds, with very uneven size.

## Decision
One schema; `tenant_id` is the leading column of every primary key/index; models are keyed
`(tenant_id, model_id)`; every service call receives a `Principal` carrying the tenant and filters
by it; cross-tenant lookups return 404. Tenant comes from verified identity claims, never request
bodies. Hardening path: PostgreSQL Row-Level Security via `SET LOCAL app.tenant_id`.

## Alternatives Considered
- **Schema-per-tenant**: strong isolation, but migrations and connection pools multiply with tenant count.
- **Database-per-tenant**: strongest isolation and noisy-neighbour control; highest cost. Reserved for regulated tenants (the key being on every row makes extraction straightforward).
- **Tenant in the URL path**: leaks tenancy into the public contract and invites mismatch with the token.

## Consequences
### Positive
- One migration, one deployment, cheap to onboard a tenant.
- Tested isolation, including per-tenant uniqueness of slugs and idempotency keys.
### Negative
- Isolation relies on every query filtering by tenant until RLS lands; a missed filter is a data leak.
- Noisy neighbours share resources (mitigated later by quotas and fair queue claiming).
### Follow-up Actions
- RLS policies + a test that runs the suite with a non-owner DB role.
- Per-tenant rate limits and quotas at the gateway.
- Tenant-aware fairness in job claiming.
