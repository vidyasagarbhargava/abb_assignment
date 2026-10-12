from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.bootstrap import Container
from app.domain.enums import Role
from app.domain.errors import ForbiddenError, UnauthenticatedError
from app.domain.rbac import Principal


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_session(container: Annotated[Container, Depends(get_container)]) -> Iterator[Session]:
    with container.session_factory() as session:
        yield session


def get_principal(
    x_user_id: Annotated[str | None, Header(description="Caller identity (dev adapter)")] = None,
    x_user_role: Annotated[
        str | None, Header(description="viewer|developer|approver|release_manager|service|admin")
    ] = None,
    x_tenant_id: Annotated[str | None, Header(description="Tenant scope")] = None,
) -> Principal:
    """Dev-mode identity from headers. Replace with a verified-JWT dependency in production;
    the rest of the system only ever sees `Principal`."""
    if not x_user_id or not x_user_role or not x_tenant_id:
        raise UnauthenticatedError(
            "X-User-Id, X-User-Role and X-Tenant-Id headers are required.",
            code="UNAUTHENTICATED",
        )
    try:
        role = Role(x_user_role)
    except ValueError:
        raise ForbiddenError(f"Unknown role '{x_user_role}'.", code="UNKNOWN_ROLE") from None
    return Principal(user=x_user_id, role=role, tenant_id=x_tenant_id)


SessionDep = Annotated[Session, Depends(get_session)]
ContainerDep = Annotated[Container, Depends(get_container)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
