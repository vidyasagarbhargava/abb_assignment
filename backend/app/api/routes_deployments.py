from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response
from sqlalchemy import func, select

from app.api.deps import ContainerDep, PrincipalDep, SessionDep
from app.api.schemas import (
    AuditOut,
    DeploymentCreate,
    DeploymentEventOut,
    DeploymentOut,
    ErrorResponse,
    Page,
    RollbackBody,
)
from app.domain.enums import DeploymentStatus, Environment
from app.domain.rbac import Permission
from app.orm import AuditEvent
from app.services.deployment_service import NOT_PROVIDED, DeploymentRequest, DeploymentService

router = APIRouter(tags=["deployments"], responses={"default": {"model": ErrorResponse}})

IdemKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key", min_length=8, max_length=128,
        description="Client-generated unique key. Replaying it returns the original deployment.",
    ),
]  # fmt: skip
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _svc(session: SessionDep, c: ContainerDep) -> DeploymentService:
    return DeploymentService(session, c.settings, c.runtimes)


@router.post("/deployments", response_model=DeploymentOut, status_code=202)
def request_deployment(
    body: DeploymentCreate,
    response: Response,
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    idempotency_key: IdemKey = None,
) -> Any:
    """Accepts the request and returns immediately; poll `GET /deployments/{id}`."""
    expected = (
        body.expected_current_deployment_id
        if "expected_current_deployment_id" in body.model_fields_set
        else NOT_PROVIDED
    )
    result = _svc(session, c).request(
        p,
        DeploymentRequest(
            model_id=body.model_id, version=body.version, environment=body.environment,
            simulate_failure=body.simulate_failure, reason=body.reason,
            expected_current_deployment_id=expected,
        ),
        idempotency_key,
    )  # fmt: skip
    if result.replayed:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    response.headers["Location"] = f"/deployments/{result.deployment.id}"
    return result.deployment


@router.get("/deployments", response_model=Page[DeploymentOut])
def list_deployments(
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    model_id: str | None = None,
    environment: Environment | None = None,
    status: DeploymentStatus | None = None,
    version: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    rows, total = _svc(session, c).list_deployments(
        p, model_id=model_id, environment=environment, status=status, version=version,
        limit=limit, offset=offset,
    )  # fmt: skip
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(deployment_id: str, p: PrincipalDep, session: SessionDep, c: ContainerDep) -> Any:
    return _svc(session, c).get(p, deployment_id)


@router.post("/deployments/{deployment_id}/retry", response_model=DeploymentOut, status_code=202)
def retry_deployment(deployment_id: str, p: PrincipalDep, session: SessionDep, c: ContainerDep) -> Any:
    return _svc(session, c).retry(p, deployment_id)


@router.post("/deployments/{deployment_id}/rollback", response_model=DeploymentOut, status_code=202)
def rollback_deployment(
    deployment_id: str,
    response: Response,
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    body: RollbackBody | None = None,
    idempotency_key: IdemKey = None,
) -> Any:
    """Creates a new ROLLBACK deployment (async) targeting the last safe version."""
    result = _svc(session, c).rollback(p, deployment_id, body.reason if body else None, idempotency_key)
    if result.replayed:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return result.deployment


@router.get("/deployments/{deployment_id}/events", response_model=Page[DeploymentEventOut])
def deployment_events(
    deployment_id: str,
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Any:
    svc = _svc(session, c)
    svc.get(p, deployment_id)  # 404 + tenant check
    rows, total = svc.events(p, deployment_id=deployment_id, model_id=None, limit=limit, offset=offset)
    return Page(items=list(reversed(rows)), total=total, limit=limit, offset=offset)


@router.get("/events", response_model=Page[DeploymentEventOut], tags=["timeline"])
def event_timeline(
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    model_id: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    rows, total = _svc(session, c).events(
        p, deployment_id=None, model_id=model_id, limit=limit, offset=offset
    )
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/audit", response_model=Page[AuditOut], tags=["governance"])
def audit_log(
    p: PrincipalDep,
    session: SessionDep,
    entity_type: str | None = None,
    entity_id: str | None = None,
    model_id: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    p.require(Permission.AUDIT_READ)
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == p.tenant_id)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if model_id:
        stmt = stmt.where(AuditEvent.model_id == model_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit).offset(offset))
    return Page(items=list(rows), total=total, limit=limit, offset=offset)
