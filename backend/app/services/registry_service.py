"""Model registry and version lifecycle (validate -> approve -> serve -> archive)."""

from dataclasses import dataclass

from sqlalchemy import String, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.domain.enums import ApprovalStatus, Stage
from app.domain.errors import (
    ConflictError,
    NotFoundError,
    PolicyViolationError,
)
from app.domain.lifecycle import assert_version_transition
from app.domain.rbac import Permission, Principal
from app.orm import Deployment, EnvironmentSlot, Model, ModelVersion
from app.runtime.base import RuntimeRegistry
from app.services.common import commit, record_audit


@dataclass(frozen=True)
class ModelFilter:
    q: str | None = None
    owner: str | None = None
    tag: str | None = None
    limit: int = 50
    offset: int = 0


class RegistryService:
    def __init__(self, session: Session, settings: Settings, runtimes: RuntimeRegistry) -> None:
        self.s = session
        self.settings = settings
        self.runtimes = runtimes

    # ---- models ---------------------------------------------------------------------------
    def create_model(self, p: Principal, *, model_id: str, **fields: object) -> Model:
        p.require(Permission.MODEL_WRITE)
        model = Model(tenant_id=p.tenant_id, model_id=model_id, created_by=p.user, **fields)
        self.s.add(model)
        record_audit(
            self.s, p, action="model.created", entity_type="model", entity_id=model_id,
            model_id=model_id, after={"name": model.name, "owner": model.owner},
        )  # fmt: skip
        try:
            commit(self.s)
        except IntegrityError as exc:
            self.s.rollback()
            raise ConflictError(f"Model '{model_id}' already exists.", code="MODEL_EXISTS") from exc
        return model

    def get_model(self, p: Principal, model_id: str) -> Model:
        p.require(Permission.MODEL_READ)
        model = self.s.get(Model, (p.tenant_id, model_id))
        if model is None:
            raise NotFoundError(f"Model '{model_id}' not found.", code="MODEL_NOT_FOUND")
        return model

    def list_models(self, p: Principal, f: ModelFilter) -> tuple[list[Model], int]:
        p.require(Permission.MODEL_READ)
        stmt = select(Model).where(Model.tenant_id == p.tenant_id)
        if f.q:
            like = f"%{f.q.lower()}%"
            stmt = stmt.where(func.lower(Model.name).like(like) | func.lower(Model.model_id).like(like))
        if f.owner:
            stmt = stmt.where(Model.owner == f.owner)
        if f.tag:
            # Portable (SQLite + Postgres) substring match on the JSON text; tags are slugs.
            stmt = stmt.where(cast(Model.tags, String).like(f'%"{f.tag}"%'))
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(self.s.scalars(stmt.order_by(Model.created_at.desc()).limit(f.limit).offset(f.offset)))
        return rows, total

    # ---- versions -------------------------------------------------------------------------
    def register_version(
        self, p: Principal, model_id: str, *, version: str, runtime: str | None, **fields: object
    ) -> ModelVersion:
        p.require(Permission.MODEL_WRITE)
        self.get_model(p, model_id)
        runtime_name = runtime or self.settings.default_runtime
        self.runtimes.get(runtime_name)  # 422 if unknown
        mv = ModelVersion(
            tenant_id=p.tenant_id, model_id=model_id, version=version, runtime=runtime_name,
            created_by=p.user, stage=Stage.DRAFT, approval_status=ApprovalStatus.PENDING,
            **fields,
        )  # fmt: skip
        self.s.add(mv)
        record_audit(
            self.s, p, action="version.registered", entity_type="version",
            entity_id=f"{model_id}@{version}", model_id=model_id,
            after={"stage": Stage.DRAFT, "artifact_uri": mv.artifact_uri},
        )  # fmt: skip
        try:
            commit(self.s)
        except IntegrityError as exc:
            self.s.rollback()
            raise ConflictError(
                f"Version '{version}' of '{model_id}' already exists.", code="VERSION_EXISTS"
            ) from exc
        return mv

    def get_version(self, p: Principal, model_id: str, version: str) -> ModelVersion:
        p.require(Permission.MODEL_READ)
        mv = self.s.scalar(
            select(ModelVersion).where(
                ModelVersion.tenant_id == p.tenant_id,
                ModelVersion.model_id == model_id,
                ModelVersion.version == version,
            )
        )
        if mv is None:
            raise NotFoundError(f"Version '{version}' of '{model_id}' not found.", code="VERSION_NOT_FOUND")
        return mv

    def list_versions(
        self, p: Principal, model_id: str, *, stage: Stage | None, limit: int, offset: int
    ) -> tuple[list[ModelVersion], int]:
        self.get_model(p, model_id)
        stmt = select(ModelVersion).where(
            ModelVersion.tenant_id == p.tenant_id, ModelVersion.model_id == model_id
        )
        if stage:
            stmt = stmt.where(ModelVersion.stage == stage)
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.s.scalars(stmt.order_by(ModelVersion.created_at.desc()).limit(limit).offset(offset))
        return list(rows), total

    # ---- lifecycle ------------------------------------------------------------------------
    def validate(self, p: Principal, model_id: str, version: str) -> ModelVersion:
        p.require(Permission.VERSION_VALIDATE)
        mv = self.get_version(p, model_id, version)
        problems = self._validation_problems(mv)
        if problems:
            raise PolicyViolationError(
                "Version failed validation.", code="VALIDATION_FAILED", details=problems
            )
        return self._move(p, mv, Stage.VALIDATED, "version.validated")

    def approve(self, p: Principal, model_id: str, version: str, reason: str | None) -> ModelVersion:
        p.require(Permission.VERSION_APPROVE)
        mv = self.get_version(p, model_id, version)
        if mv.created_by == p.user:
            raise PolicyViolationError(
                "The registrant cannot approve their own version (four-eyes principle).",
                code="SELF_APPROVAL_FORBIDDEN",
            )
        mv.approval_status = ApprovalStatus.APPROVED
        mv.approved_by = p.user
        mv.approved_at = utcnow()
        mv.decision_reason = reason
        return self._move(p, mv, Stage.APPROVED, "version.approved")

    def reject(self, p: Principal, model_id: str, version: str, reason: str) -> ModelVersion:
        p.require(Permission.VERSION_APPROVE)
        mv = self.get_version(p, model_id, version)
        mv.approval_status = ApprovalStatus.REJECTED
        mv.decision_reason = reason
        return self._move(p, mv, Stage.DRAFT, "version.rejected")

    def archive(self, p: Principal, model_id: str, version: str) -> ModelVersion:
        p.require(Permission.VERSION_ARCHIVE)
        mv = self.get_version(p, model_id, version)
        serving = self.s.scalar(
            select(func.count())
            .select_from(EnvironmentSlot)
            .join(Deployment, Deployment.id == EnvironmentSlot.current_deployment_id)
            .where(
                EnvironmentSlot.tenant_id == p.tenant_id,
                Deployment.version_id == mv.id,
            )
        )
        if serving:
            raise PolicyViolationError(
                "Version is currently serving; roll it off before archiving.",
                code="VERSION_IN_USE",
            )
        return self._move(p, mv, Stage.ARCHIVED, "version.archived")

    # ---- internals ------------------------------------------------------------------------
    def _move(self, p: Principal, mv: ModelVersion, target: Stage, action: str) -> ModelVersion:
        before = {"stage": mv.stage, "approval_status": mv.approval_status}
        assert_version_transition(Stage(mv.stage), target)
        mv.stage = target
        record_audit(
            self.s, p, action=action, entity_type="version",
            entity_id=f"{mv.model_id}@{mv.version}", model_id=mv.model_id,
            before=before, after={"stage": target, "approval_status": mv.approval_status},
        )  # fmt: skip
        commit(self.s)
        return mv

    def _validation_problems(self, mv: ModelVersion) -> list[str]:
        problems: list[str] = []
        scheme = mv.artifact_uri.split("://", 1)[0] if "://" in mv.artifact_uri else ""
        if scheme not in self.settings.allowed_artifact_schemes:
            problems.append(f"artifact_uri scheme '{scheme}' not in allowed set")
        if not mv.training_data_ref:
            problems.append("training_data_ref is required for lineage")
        return problems
