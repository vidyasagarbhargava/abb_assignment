"""Deployment request, retry and rollback: the governed, idempotent write path.

All three only *enqueue* work (status REQUESTED + a Job row, one commit). The worker owns
execution. Concurrency safety comes from the database, not from check-then-act in Python:
  * partial unique index  -> one in-flight deployment per (tenant, model, environment)
  * unique idempotency key -> duplicate submissions converge on one row
  * optimistic row_version -> lost updates become 409s
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.domain.enums import (
    ACTIVE_DEPLOYMENT_STATUSES,
    ApprovalStatus,
    DeploymentKind,
    DeploymentStatus,
    Environment,
    FailureClass,
    JobStatus,
    SimulatedFailure,
    Stage,
)
from app.domain.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from app.domain.lifecycle import (
    DeploymentFacts,
    VersionFacts,
    assert_deployment_transition,
    assert_promotion_allowed,
    plan_rollback,
)
from app.domain.rbac import Permission, Principal, deploy_permission
from app.observability import (
    DEPLOYMENT_DURATION,
    DEPLOYMENT_FAILURES,
    DEPLOYMENT_TRANSITIONS,
    get_correlation_id,
)
from app.orm import (
    Deployment,
    DeploymentEvent,
    EnvironmentSlot,
    Job,
    ModelVersion,
    new_id,
)
from app.runtime.base import RuntimeRegistry
from app.services.common import commit, record_audit

NOT_PROVIDED = object()


@dataclass(frozen=True)
class DeploymentRequest:
    model_id: str
    version: str
    environment: Environment
    simulate_failure: SimulatedFailure = SimulatedFailure.NONE
    reason: str | None = None
    # Optimistic promotion guard. NOT_PROVIDED = skip; None = "I expect nothing is serving".
    expected_current_deployment_id: object = NOT_PROVIDED


@dataclass(frozen=True)
class SubmitResult:
    deployment: Deployment
    replayed: bool


def fingerprint(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def apply_transition(
    session: Session,
    dep: Deployment,
    to: DeploymentStatus,
    event: str,
    *,
    detail: dict[str, object] | None = None,
    failure_class: FailureClass | None = None,
    failure_message: str | None = None,
) -> None:
    """The single place a deployment changes status: validates, stamps, logs the timeline."""
    current = DeploymentStatus(dep.status)
    assert_deployment_transition(current, to)
    dep.status = to
    now = utcnow()
    if to in (DeploymentStatus.SUCCEEDED, DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK):
        dep.completed_at = dep.completed_at if to == DeploymentStatus.ROLLED_BACK else now
    if failure_class is not None:
        dep.failure_class = failure_class
        dep.failure_message = failure_message
    session.add(
        DeploymentEvent(
            tenant_id=dep.tenant_id,
            deployment_id=dep.id,
            model_id=dep.model_id,
            version=dep.version,
            environment=dep.environment,
            event=event,
            from_status=current,
            to_status=to,
            failure_class=failure_class,
            detail=detail or {},
            correlation_id=dep.correlation_id or get_correlation_id(),
        )  # fmt: skip
    )
    DEPLOYMENT_TRANSITIONS.labels(to_status=to, environment=dep.environment).inc()
    if failure_class is not None and to == DeploymentStatus.FAILED:
        DEPLOYMENT_FAILURES.labels(failure_class=failure_class, environment=dep.environment).inc()
    if to in (DeploymentStatus.SUCCEEDED, DeploymentStatus.FAILED):
        DEPLOYMENT_DURATION.labels(environment=dep.environment, outcome=str(to)).observe(
            max((now - dep.created_at).total_seconds(), 0.0)
        )


def version_facts(mv: ModelVersion) -> VersionFacts:
    return VersionFacts(mv.version, Stage(mv.stage), ApprovalStatus(mv.approval_status))


class DeploymentService:
    def __init__(self, session: Session, settings: Settings, runtimes: RuntimeRegistry) -> None:
        self.s = session
        self.settings = settings
        self.runtimes = runtimes

    # ---- reads ----------------------------------------------------------------------------
    def get(self, p: Principal, deployment_id: str) -> Deployment:
        p.require(Permission.MODEL_READ)
        dep = self.s.get(Deployment, deployment_id)
        if dep is None or dep.tenant_id != p.tenant_id:  # same 404: do not leak other tenants
            raise NotFoundError(f"Deployment '{deployment_id}' not found.", code="DEPLOYMENT_NOT_FOUND")
        return dep

    def list_deployments(
        self,
        p: Principal,
        *,
        model_id: str | None,
        environment: Environment | None,
        status: DeploymentStatus | None,
        version: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Deployment], int]:
        p.require(Permission.MODEL_READ)
        stmt = select(Deployment).where(Deployment.tenant_id == p.tenant_id)
        if model_id:
            stmt = stmt.where(Deployment.model_id == model_id)
        if environment:
            stmt = stmt.where(Deployment.environment == environment)
        if status:
            stmt = stmt.where(Deployment.status == status)
        if version:
            stmt = stmt.where(Deployment.version == version)
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.s.scalars(stmt.order_by(Deployment.created_at.desc()).limit(limit).offset(offset))
        return list(rows), total

    def events(
        self, p: Principal, *, deployment_id: str | None, model_id: str | None, limit: int, offset: int
    ) -> tuple[list[DeploymentEvent], int]:
        p.require(Permission.MODEL_READ)
        stmt = select(DeploymentEvent).where(DeploymentEvent.tenant_id == p.tenant_id)
        if deployment_id:
            stmt = stmt.where(DeploymentEvent.deployment_id == deployment_id)
        if model_id:
            stmt = stmt.where(DeploymentEvent.model_id == model_id)
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.s.scalars(stmt.order_by(DeploymentEvent.id.desc()).limit(limit).offset(offset))
        return list(rows), total

    # ---- writes ---------------------------------------------------------------------------
    def request(self, p: Principal, req: DeploymentRequest, idempotency_key: str | None) -> SubmitResult:
        p.require(deploy_permission(req.environment))
        if req.simulate_failure != SimulatedFailure.NONE and not self.settings.allow_failure_simulation:
            raise ValidationFailedError(
                "Failure simulation is disabled in this environment.", code="SIMULATION_DISABLED"
            )
        fp = fingerprint("deploy", req.model_id, req.version, str(req.environment), str(req.simulate_failure))
        replay = self._replay(p, idempotency_key, fp)
        if replay:
            return replay
        try:
            return self._request(p, req, idempotency_key, fp)
        except ConflictError:
            # A concurrent twin with the same key may have won between our replay check and
            # the slot check; in that case the correct answer is the replay, not a conflict.
            self.s.rollback()
            if replay := self._replay(p, idempotency_key, fp):
                return replay
            raise

    def _request(
        self, p: Principal, req: DeploymentRequest, idempotency_key: str | None, fp: str
    ) -> SubmitResult:
        mv = self._get_version(p, req.model_id, req.version)
        self.runtimes.get(mv.runtime)
        assert_promotion_allowed(
            version_facts(mv),
            req.environment,
            has_successful_staging=self._has_successful_staging(p, mv),
            require_staging=self.settings.require_staging_before_production,
        )
        head = self._slot_head(p, req.model_id, req.environment)
        if req.expected_current_deployment_id is not NOT_PROVIDED and (
            (head.id if head else None) != req.expected_current_deployment_id
        ):
            raise ConflictError(
                "The serving deployment changed since you last looked.",
                code="PROMOTION_CONFLICT",
                details={"current_deployment_id": head.id if head else None},
            )
        if head is not None and head.version_id == mv.id:
            raise ConflictError(
                f"Version {mv.version} is already serving in {req.environment}.",
                code="VERSION_ALREADY_ACTIVE",
                details={"deployment_id": head.id},
            )
        dep = self._enqueue(
            p, mv, req.environment, DeploymentKind.DEPLOY, idempotency_key, fp,
            simulate=req.simulate_failure, reason=req.reason, rollback_of=None,
        )  # fmt: skip
        return self._commit_new(p, dep, idempotency_key, fp, "deployment.requested")

    def retry(self, p: Principal, deployment_id: str) -> Deployment:
        dep = self.get(p, deployment_id)
        p.require(deploy_permission(Environment(dep.environment)))
        if dep.status != DeploymentStatus.FAILED:
            raise ConflictError(
                f"Only FAILED deployments can be retried (current: {dep.status}).",
                code="RETRY_NOT_ALLOWED",
            )
        self._assert_slot_free(p, dep.model_id, dep.environment)
        apply_transition(
            self.s, dep, DeploymentStatus.REQUESTED, "retry_requested",
            detail={"by": p.user, "previous_failure": dep.failure_class},
        )  # fmt: skip
        dep.failure_class = None
        dep.failure_message = None
        dep.completed_at = None
        self.s.add(self._new_job(dep))
        record_audit(
            self.s, p, action="deployment.retried", entity_type="deployment",
            entity_id=dep.id, model_id=dep.model_id,
        )  # fmt: skip
        try:
            commit(self.s)
        except IntegrityError as exc:  # another deployment took the slot first
            self.s.rollback()
            raise ConflictError(
                "Another deployment is in progress for this model and environment.",
                code="DEPLOYMENT_IN_PROGRESS",
            ) from exc
        return dep

    def rollback(
        self, p: Principal, deployment_id: str, reason: str | None, idempotency_key: str | None
    ) -> SubmitResult:
        target_dep = self.get(p, deployment_id)
        env = Environment(target_dep.environment)
        p.require(deploy_permission(env))
        fp = fingerprint("rollback", deployment_id)
        replay = self._replay(p, idempotency_key, fp)
        if replay:
            return replay
        try:
            return self._rollback(p, target_dep, env, reason, idempotency_key, fp)
        except ConflictError:
            self.s.rollback()
            if replay := self._replay(p, idempotency_key, fp):
                return replay
            raise

    def _rollback(
        self,
        p: Principal,
        target_dep: Deployment,
        env: Environment,
        reason: str | None,
        idempotency_key: str | None,
        fp: str,
    ) -> SubmitResult:
        head = self._slot_head(p, target_dep.model_id, env)
        if head is None or head.id != target_dep.id:
            raise ConflictError(
                "Only the deployment currently serving this environment can be rolled back.",
                code="ROLLBACK_NOT_CURRENT",
                details={"current_deployment_id": head.id if head else None},
            )
        self._assert_slot_free(p, target_dep.model_id, env)
        history = list(
            self.s.scalars(
                select(Deployment).where(
                    Deployment.tenant_id == p.tenant_id,
                    Deployment.model_id == target_dep.model_id,
                    Deployment.environment == env,
                    Deployment.status == DeploymentStatus.SUCCEEDED,
                )
            )
        )
        facts = {d.id: _dep_facts(d) for d in history}
        version_ids = {d.version_id for d in history}
        versions = {
            v.id: v for v in self.s.scalars(select(ModelVersion).where(ModelVersion.id.in_(version_ids)))
        }
        chosen = plan_rollback(
            facts[target_dep.id], list(facts.values()),
            {vid: version_facts(v) for vid, v in versions.items()},
        )  # fmt: skip
        mv = versions[chosen.version_id]
        self.runtimes.get(mv.runtime)
        dep = self._enqueue(
            p, mv, env, DeploymentKind.ROLLBACK, idempotency_key, fp,
            simulate=SimulatedFailure.NONE, reason=reason, rollback_of=target_dep.id,
        )  # fmt: skip
        return self._commit_new(p, dep, idempotency_key, fp, "deployment.rollback_requested")

    # ---- internals ------------------------------------------------------------------------
    def _get_version(self, p: Principal, model_id: str, version: str) -> ModelVersion:
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

    def _has_successful_staging(self, p: Principal, mv: ModelVersion) -> bool:
        return (
            self.s.scalar(
                select(func.count())
                .select_from(Deployment)
                .where(
                    Deployment.tenant_id == p.tenant_id,
                    Deployment.version_id == mv.id,
                    Deployment.environment == Environment.STAGING,
                    Deployment.status.in_([DeploymentStatus.SUCCEEDED, DeploymentStatus.ROLLED_BACK]),
                )
            )
            or 0
        ) > 0

    def _slot_head(self, p: Principal, model_id: str, env: str) -> Deployment | None:
        slot = self.s.get(EnvironmentSlot, (p.tenant_id, model_id, str(env)))
        if slot is None or slot.current_deployment_id is None:
            return None
        return self.s.get(Deployment, slot.current_deployment_id)

    def _assert_slot_free(self, p: Principal, model_id: str, env: str) -> None:
        active = self.s.scalar(
            select(Deployment).where(
                Deployment.tenant_id == p.tenant_id,
                Deployment.model_id == model_id,
                Deployment.environment == env,
                Deployment.status.in_(ACTIVE_DEPLOYMENT_STATUSES),
            )
        )
        if active is not None:
            raise ConflictError(
                "Another deployment is in progress for this model and environment.",
                code="DEPLOYMENT_IN_PROGRESS",
                details={"deployment_id": active.id, "status": active.status},
            )

    def _replay(self, p: Principal, key: str | None, fp: str) -> SubmitResult | None:
        if not key:
            return None
        existing = self.s.scalar(
            select(Deployment).where(Deployment.tenant_id == p.tenant_id, Deployment.idempotency_key == key)
        )
        if existing is None:
            return None
        if existing.request_fingerprint != fp:
            raise ValidationFailedError(
                "Idempotency-Key was already used with a different request.",
                code="IDEMPOTENCY_KEY_REUSED",
            )
        return SubmitResult(existing, replayed=True)

    def _enqueue(
        self,
        p: Principal,
        mv: ModelVersion,
        env: Environment,
        kind: DeploymentKind,
        key: str | None,
        fp: str,
        *,
        simulate: SimulatedFailure,
        reason: str | None,
        rollback_of: str | None,
    ) -> Deployment:
        self._assert_slot_free(p, mv.model_id, env)
        return Deployment(
            id=new_id("dep-"), tenant_id=p.tenant_id, model_id=mv.model_id, version_id=mv.id,
            version=mv.version, environment=env, kind=kind, status=DeploymentStatus.REQUESTED,
            requested_by=p.user, idempotency_key=key, request_fingerprint=fp,
            rollback_of_id=rollback_of, reason=reason, simulate_failure=simulate,
            correlation_id=get_correlation_id(), created_at=utcnow(),
        )  # fmt: skip

    def _new_job(self, dep: Deployment) -> Job:
        return Job(
            tenant_id=dep.tenant_id, deployment_id=dep.id, status=JobStatus.PENDING,
            max_attempts=self.settings.job_max_attempts, run_after=utcnow(),
        )  # fmt: skip

    def _commit_new(
        self, p: Principal, dep: Deployment, key: str | None, fp: str, action: str
    ) -> SubmitResult:
        self.s.add(dep)
        self.s.add(
            DeploymentEvent(
                tenant_id=dep.tenant_id,
                deployment_id=dep.id,
                model_id=dep.model_id,
                version=dep.version,
                environment=dep.environment,
                event=action.split(".")[1],
                from_status=None,
                to_status=DeploymentStatus.REQUESTED,
                detail={"kind": dep.kind, "by": p.user, "rollback_of": dep.rollback_of_id},
                correlation_id=dep.correlation_id,
            )  # fmt: skip
        )
        self.s.add(self._new_job(dep))
        record_audit(
            self.s, p, action=action, entity_type="deployment", entity_id=dep.id,
            model_id=dep.model_id,
            after={"version": dep.version, "environment": dep.environment, "kind": dep.kind},
        )  # fmt: skip
        DEPLOYMENT_TRANSITIONS.labels(to_status=DeploymentStatus.REQUESTED, environment=dep.environment).inc()
        try:
            commit(self.s)
        except IntegrityError as exc:
            # Lost a race. The unique indexes make the loser's insert fail; classify why.
            self.s.rollback()
            if key:
                winner = self._replay(p, key, fp)
                if winner:
                    return winner
            raise ConflictError(
                "Another deployment is in progress for this model and environment.",
                code="DEPLOYMENT_IN_PROGRESS",
            ) from exc
        return SubmitResult(dep, replayed=False)


def _dep_facts(d: Deployment) -> DeploymentFacts:
    completed: datetime | None = d.completed_at
    return DeploymentFacts(
        d.id, d.version_id, d.version, DeploymentStatus(d.status), DeploymentKind(d.kind), completed
    )
