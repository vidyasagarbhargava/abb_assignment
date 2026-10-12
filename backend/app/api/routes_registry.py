from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from app.api.deps import ContainerDep, PrincipalDep, SessionDep
from app.api.schemas import (
    CompareOut,
    DecisionBody,
    ErrorResponse,
    MetricsIngest,
    MetricsIngestResult,
    MetricsOut,
    ModelCreate,
    ModelOut,
    Page,
    RejectBody,
    VersionCreate,
    VersionOut,
)
from app.domain.enums import Stage
from app.services.metrics_service import MetricsService
from app.services.registry_service import ModelFilter, RegistryService

router = APIRouter(tags=["registry"], responses={"default": {"model": ErrorResponse}})

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _svc(session: SessionDep, c: ContainerDep) -> RegistryService:
    return RegistryService(session, c.settings, c.runtimes)


@router.post("/models", response_model=ModelOut, status_code=201)
def create_model(body: ModelCreate, p: PrincipalDep, session: SessionDep, c: ContainerDep) -> Any:
    data = body.model_dump(exclude={"model_id", "metadata"})
    return _svc(session, c).create_model(p, model_id=body.model_id, metadata_=body.metadata, **data)


@router.get("/models", response_model=Page[ModelOut])
def list_models(
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    q: str | None = None,
    owner: str | None = None,
    tag: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    rows, total = _svc(session, c).list_models(p, ModelFilter(q, owner, tag, limit, offset))
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/models/{model_id}", response_model=ModelOut)
def get_model(model_id: str, p: PrincipalDep, session: SessionDep, c: ContainerDep) -> Any:
    return _svc(session, c).get_model(p, model_id)


@router.post("/models/{model_id}/versions", response_model=VersionOut, status_code=201)
def register_version(
    model_id: str, body: VersionCreate, p: PrincipalDep, session: SessionDep, c: ContainerDep
) -> Any:
    data = body.model_dump(exclude={"version", "runtime", "metadata"})
    return _svc(session, c).register_version(
        p, model_id, version=body.version, runtime=body.runtime, metadata_=body.metadata, **data
    )


@router.get("/models/{model_id}/versions", response_model=Page[VersionOut])
def list_versions(
    model_id: str,
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    stage: Stage | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Any:
    rows, total = _svc(session, c).list_versions(p, model_id, stage=stage, limit=limit, offset=offset)
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/models/{model_id}/versions/{version}", response_model=VersionOut)
def get_version(model_id: str, version: str, p: PrincipalDep, session: SessionDep, c: ContainerDep) -> Any:
    return _svc(session, c).get_version(p, model_id, version)


@router.post("/models/{model_id}/versions/{version}/validate", response_model=VersionOut)
def validate_version(
    model_id: str, version: str, p: PrincipalDep, session: SessionDep, c: ContainerDep
) -> Any:
    return _svc(session, c).validate(p, model_id, version)


@router.post("/models/{model_id}/versions/{version}/approve", response_model=VersionOut)
def approve_version(
    model_id: str, version: str, body: DecisionBody, p: PrincipalDep, session: SessionDep, c: ContainerDep
) -> Any:
    return _svc(session, c).approve(p, model_id, version, body.reason)


@router.post("/models/{model_id}/versions/{version}/reject", response_model=VersionOut)
def reject_version(
    model_id: str, version: str, body: RejectBody, p: PrincipalDep, session: SessionDep, c: ContainerDep
) -> Any:
    return _svc(session, c).reject(p, model_id, version, body.reason)


@router.post("/models/{model_id}/versions/{version}/archive", response_model=VersionOut)
def archive_version(
    model_id: str, version: str, p: PrincipalDep, session: SessionDep, c: ContainerDep
) -> Any:
    return _svc(session, c).archive(p, model_id, version)


# ---- monitoring ---------------------------------------------------------------------------
@router.get("/models/{model_id}/metrics", response_model=MetricsOut)
def get_metrics(
    model_id: str,
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    environment: str | None = None,
    version: str | None = None,
    window_hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24 * 30,
    bucket: Literal["hour", "day"] = "day",
) -> Any:
    return MetricsService(session, c.settings).query(
        p, model_id, environment=environment, version=version, start=None, end=None,
        window_hours=window_hours, bucket=bucket,
    )  # fmt: skip


@router.post("/models/{model_id}/metrics", response_model=MetricsIngestResult, status_code=202)
def ingest_metrics(
    model_id: str, body: MetricsIngest, p: PrincipalDep, session: SessionDep, c: ContainerDep
) -> Any:
    rows = [pt.model_dump() for pt in body.points]
    inserted = MetricsService(session, c.settings).ingest(p, model_id, rows)
    return MetricsIngestResult(submitted=len(rows), inserted=inserted)


@router.get("/models/{model_id}/compare", response_model=CompareOut)
def compare_versions(
    model_id: str,
    base: str,
    candidate: str,
    p: PrincipalDep,
    session: SessionDep,
    c: ContainerDep,
    environment: str | None = None,
    window_hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24 * 30,
) -> Any:
    return MetricsService(session, c.settings).compare(
        p, model_id, base, candidate, environment, window_hours
    )
