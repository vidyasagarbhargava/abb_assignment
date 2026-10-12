from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.domain.errors import ConflictError
from app.domain.rbac import Principal
from app.observability import get_correlation_id
from app.orm import AuditEvent


def commit(session: Session) -> None:
    """Commit, translating a lost optimistic-lock race into a retryable 409."""
    try:
        session.commit()
    except StaleDataError as exc:
        session.rollback()
        raise ConflictError(
            "The resource was modified concurrently; reload and retry.",
            code="CONCURRENT_MODIFICATION",
        ) from exc


def record_audit(
    session: Session,
    principal: Principal,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    model_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            tenant_id=principal.tenant_id,
            actor=principal.user,
            actor_role=str(principal.role),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            model_id=model_id,
            before=before,
            after=after,
            correlation_id=get_correlation_id(),
        )
    )
