# ADR-005: Pure domain core, services own transactions, thin API

## Status
Accepted

## Context
The product's value is its rules (who may promote what, when rollback is safe). Rules that live in
route handlers get duplicated, drift, and are slow to test. Multiple teams will touch the code.

## Decision
- `domain/` is pure (no I/O, no framework imports): state machines, promotion policy, rollback planning, RBAC matrix, metric classification.
- `services/` load facts, ask the domain for a verdict, and own the transaction and audit write.
- `api/` only translates HTTP <-> services and renders one error envelope. Response schemas are separate from ORM models.
- Dependency direction is inward only; wiring happens in `bootstrap.py`.

## Alternatives Considered
- **Logic in route handlers / ORM models (active-record style)**: fastest to write, hardest to test and share.
- **Full hexagonal/DDD with repositories for every aggregate**: more ceremony than the current size justifies; services use SQLAlchemy sessions directly and the runtime port is the only true external seam.

## Consequences
### Positive
- Rules are unit-tested without a database or HTTP; lifecycle bugs are caught in milliseconds.
- Clear ownership boundaries per directory for CODEOWNERS.
### Negative
- Services depend on SQLAlchemy directly, so swapping the ORM is not free.
- Some duplication between ORM models and Pydantic schemas (deliberate: it is the isolation boundary).
### Follow-up Actions
- Import-linter rule in CI to enforce the dependency direction.
