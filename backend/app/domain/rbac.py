"""Role-based authorization. Pure and table-driven so the matrix is reviewable and testable.

Identity is resolved at the edge (see api/deps.py). In production the Principal is built from
verified OIDC/JWT claims; the dev header adapter is a stand-in with the same shape.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import Environment, Role
from app.domain.errors import ForbiddenError


class Permission(StrEnum):
    MODEL_READ = "model:read"
    MODEL_WRITE = "model:write"
    VERSION_VALIDATE = "version:validate"
    VERSION_APPROVE = "version:approve"
    VERSION_ARCHIVE = "version:archive"
    DEPLOY_STAGING = "deploy:staging"
    DEPLOY_PRODUCTION = "deploy:production"
    METRICS_WRITE = "metrics:write"
    AUDIT_READ = "audit:read"


_READ = {Permission.MODEL_READ}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(_READ),
    Role.DEVELOPER: frozenset(
        _READ
        | {
            Permission.MODEL_WRITE,
            Permission.VERSION_VALIDATE,
            Permission.DEPLOY_STAGING,
            Permission.METRICS_WRITE,
        }
    ),
    # Approvers cannot deploy and developers cannot approve: separation of duties.
    Role.APPROVER: frozenset(_READ | {Permission.VERSION_APPROVE, Permission.AUDIT_READ}),
    Role.RELEASE_MANAGER: frozenset(
        _READ
        | {
            Permission.DEPLOY_STAGING,
            Permission.DEPLOY_PRODUCTION,
            Permission.VERSION_ARCHIVE,
        }
    ),
    Role.SERVICE: frozenset(_READ | {Permission.METRICS_WRITE}),
    Role.ADMIN: frozenset(Permission),
}


@dataclass(frozen=True)
class Principal:
    user: str
    role: Role
    tenant_id: str

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise ForbiddenError(
                f"Role '{self.role}' lacks permission '{permission}'.",
                code="INSUFFICIENT_PERMISSION",
                details={"required": str(permission)},
            )


def deploy_permission(environment: Environment) -> Permission:
    """Environment promotion control: production needs a stronger grant than staging."""
    return (
        Permission.DEPLOY_PRODUCTION if environment == Environment.PRODUCTION else Permission.DEPLOY_STAGING
    )
