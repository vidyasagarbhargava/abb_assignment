"""Asynchronous deployment worker.

Execution model (at-least-once, effectively-once):
  1. CLAIM    compare-and-swap a PENDING job to RUNNING with a lease (portable: no SKIP LOCKED
              required, but safe with many workers on Postgres or SQLite).
  2. INTENT   commit DEPLOYING *before* calling the runtime, so a crash leaves evidence.
  3. EXECUTE  call the runtime with idempotency_key = deployment id (replays are no-ops).
  4. RECORD   one transaction: deployment SUCCEEDED, slot pointer, version stages, job DONE.
If step 4 fails after step 3 succeeded (external success / internal DB failure), the lease
expires and `recover_stale_jobs` asks the runtime what really happened and converges.
"""

import logging
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import utcnow
from app.domain.enums import (
    DeploymentKind,
    DeploymentStatus,
    Environment,
    FailureClass,
    JobStatus,
    Stage,
)
from app.domain.errors import (
    DomainError,
    RuntimePermanentError,
    RuntimeTimeoutError,
    RuntimeTransientError,
)
from app.domain.lifecycle import assert_deployable
from app.observability import (
    OLDEST_PENDING_AGE,
    QUEUE_DEPTH,
    RECONCILIATIONS,
    WORKER_JOBS,
    set_correlation_id,
    span,
)
from app.orm import Deployment, EnvironmentSlot, Job, ModelVersion
from app.runtime.base import RuntimeDeployment, RuntimeDeployRequest, RuntimeRegistry
from app.services.common import commit
from app.services.deployment_service import apply_transition, version_facts

log = logging.getLogger("mlops.worker")


class DeploymentWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        runtimes: RuntimeRegistry,
        worker_id: str | None = None,
    ) -> None:
        self._sf = session_factory
        self._settings = settings
        self._runtimes = runtimes
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="runtime-call")

    # ---- public loop ----------------------------------------------------------------------
    def run_once(self, max_jobs: int = 100) -> int:
        """Recover stale work, then drain runnable jobs. Returns jobs processed."""
        self.recover_stale_jobs()
        processed = 0
        while processed < max_jobs and (job_id := self._claim_next()) is not None:
            self.process_job(job_id)
            processed += 1
        self.publish_queue_metrics()
        return processed

    def run_forever(self, stop: threading.Event) -> None:
        log.info("worker started", extra={"worker_id": self.worker_id})
        while not stop.is_set():
            try:
                if self.run_once() == 0:
                    stop.wait(self._settings.worker_poll_interval_s)
            except Exception:  # keep the loop alive; the failure is logged and metered
                log.exception("worker loop error")
                WORKER_JOBS.labels(outcome="loop_error").inc()
                stop.wait(self._settings.worker_poll_interval_s * 4)
        log.info("worker stopped", extra={"worker_id": self.worker_id})

    # ---- claim ----------------------------------------------------------------------------
    def _claim_next(self) -> str | None:
        now = utcnow()
        with self._sf() as s:
            candidates = s.scalars(
                select(Job.id)
                .where(Job.status == JobStatus.PENDING, Job.run_after <= now)
                .order_by(Job.run_after)
                .limit(10)
            ).all()
            for job_id in candidates:
                won = s.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == JobStatus.PENDING)
                    .values(
                        status=JobStatus.RUNNING,
                        lease_owner=self.worker_id,
                        lease_expires_at=now + timedelta(seconds=self._settings.worker_lease_s),
                        attempts=Job.attempts + 1,
                        updated_at=now,
                    )
                )
                s.commit()
                if getattr(won, "rowcount", 0) == 1:
                    return job_id
        return None

    # ---- processing -----------------------------------------------------------------------
    def process_job(self, job_id: str) -> str:
        with self._sf() as s:
            job = s.get(Job, job_id)
            assert job is not None
            dep = s.get(Deployment, job.deployment_id)
            assert dep is not None
            set_correlation_id(dep.correlation_id)
            try:
                with span("deployment.process", deployment_id=dep.id, environment=dep.environment):
                    return self._process(s, job, dep)
            except Exception as exc:  # defensive: classify unknown failures as INTERNAL
                s.rollback()
                log.exception("unexpected worker failure", extra={"deployment_id": dep.id})
                return self._fail_internal(job_id, str(exc))
            finally:
                set_correlation_id(None)

    def _process(self, s: Session, job: Job, dep: Deployment) -> str:
        if dep.status in (
            DeploymentStatus.SUCCEEDED,
            DeploymentStatus.FAILED,
            DeploymentStatus.ROLLED_BACK,
        ):
            job.status = JobStatus.DONE
            commit(s)
            WORKER_JOBS.labels(outcome="skipped").inc()
            return "skipped"

        if dep.status == DeploymentStatus.REQUESTED:
            apply_transition(s, dep, DeploymentStatus.VALIDATING, "validation_started")
            commit(s)

        mv = s.get(ModelVersion, dep.version_id)
        assert mv is not None
        if dep.status == DeploymentStatus.VALIDATING:
            try:
                # Approval may have been revoked between request and execution.
                assert_deployable(version_facts(mv))
                runtime = self._runtimes.get(mv.runtime)
            except DomainError as exc:
                return self._fail(s, job, dep, FailureClass.POLICY, exc.message)
            apply_transition(s, dep, DeploymentStatus.DEPLOYING, "deploy_started")
            dep.attempt_count += 1
            commit(s)  # intent is durable before any external side effect
        else:  # resumed after a crash while DEPLOYING
            runtime = self._runtimes.get(mv.runtime)
            dep.attempt_count += 1
            commit(s)

        request = RuntimeDeployRequest(
            idempotency_key=dep.id, model_id=dep.model_id, version=dep.version,
            artifact_uri=mv.artifact_uri, environment=dep.environment,
            attempt=dep.attempt_count, simulate_failure=dep.simulate_failure,
        )  # fmt: skip
        try:
            with span("runtime.deploy", runtime=runtime.name):
                future = self._pool.submit(runtime.deploy, request)
                result = future.result(timeout=self._settings.runtime_timeout_s)
        except (FutureTimeout, RuntimeTimeoutError) as exc:
            return self._handle_transient(s, job, dep, FailureClass.TIMEOUT, str(exc) or "timeout")
        except RuntimeTransientError as exc:
            return self._handle_transient(s, job, dep, FailureClass.RUNTIME_TRANSIENT, str(exc))
        except RuntimePermanentError as exc:
            return self._fail(s, job, dep, FailureClass.RUNTIME_PERMANENT, str(exc))

        return self._succeed(s, job, dep, result, event="deploy_succeeded")

    def _succeed(
        self, s: Session, job: Job, dep: Deployment, result: RuntimeDeployment, *, event: str
    ) -> str:
        dep.external_ref = result.external_ref
        dep.failure_class = None
        dep.failure_message = None
        apply_transition(s, dep, DeploymentStatus.SUCCEEDED, event, detail={"ref": result.external_ref})

        slot = s.get(EnvironmentSlot, (dep.tenant_id, dep.model_id, dep.environment))
        previous_id = slot.current_deployment_id if slot else None
        if slot is None:
            slot = EnvironmentSlot(
                tenant_id=dep.tenant_id, model_id=dep.model_id, environment=dep.environment
            )
            s.add(slot)
        slot.current_deployment_id = dep.id

        affected = {dep.version_id}
        if previous_id and previous_id != dep.id:
            prev = s.get(Deployment, previous_id)
            if prev is not None:
                affected.add(prev.version_id)

        if dep.kind == DeploymentKind.ROLLBACK and dep.rollback_of_id:
            original = s.get(Deployment, dep.rollback_of_id)
            if original is not None and original.status == DeploymentStatus.SUCCEEDED:
                apply_transition(
                    s, original, DeploymentStatus.ROLLED_BACK, "rolled_back",
                    detail={"rolled_back_by": dep.id},
                )  # fmt: skip
                affected.add(original.version_id)

        s.flush()
        for version_id in affected:
            self._refresh_stage(s, version_id)
        job.status = JobStatus.DONE
        job.lease_owner = None
        commit(s)
        WORKER_JOBS.labels(outcome="succeeded").inc()
        log.info("deployment succeeded", extra={"deployment_id": dep.id, "event": event})
        return "succeeded"

    @staticmethod
    def _refresh_stage(s: Session, version_id: str) -> None:
        """Derive serving stage from what the environment slots actually point at."""
        mv = s.get(ModelVersion, version_id)
        if mv is None or mv.stage in (Stage.ARCHIVED, Stage.DRAFT, Stage.VALIDATED):
            return
        envs = set(
            s.scalars(
                select(Deployment.environment)
                .join(EnvironmentSlot, EnvironmentSlot.current_deployment_id == Deployment.id)
                .where(Deployment.version_id == version_id)
            )
        )
        if Environment.PRODUCTION in envs:
            mv.stage = Stage.PRODUCTION
        elif Environment.STAGING in envs:
            mv.stage = Stage.STAGING
        else:
            mv.stage = Stage.APPROVED

    def _handle_transient(self, s: Session, job: Job, dep: Deployment, fc: FailureClass, message: str) -> str:
        if job.attempts < job.max_attempts:
            backoff = self._settings.retry_backoff_base_s * (2 ** (job.attempts - 1))
            apply_transition(
                s, dep, DeploymentStatus.REQUESTED, "retry_scheduled",
                detail={"attempt": job.attempts, "backoff_s": backoff, "error": message},
                failure_class=fc, failure_message=message,
            )  # fmt: skip
            job.status = JobStatus.PENDING
            job.run_after = utcnow() + timedelta(seconds=backoff)
            job.lease_owner = None
            job.last_error = message
            commit(s)
            WORKER_JOBS.labels(outcome="retry_scheduled").inc()
            log.warning(
                "transient failure, retry scheduled",
                extra={"deployment_id": dep.id, "failure_class": fc, "backoff_s": backoff},
            )
            return "retry_scheduled"
        return self._fail(s, job, dep, fc, f"retries exhausted: {message}")

    def _fail(self, s: Session, job: Job, dep: Deployment, fc: FailureClass, message: str) -> str:
        apply_transition(
            s, dep, DeploymentStatus.FAILED, "deploy_failed",
            detail={"error": message}, failure_class=fc, failure_message=message,
        )  # fmt: skip
        job.status = JobStatus.DEAD
        job.last_error = message
        job.lease_owner = None
        commit(s)
        WORKER_JOBS.labels(outcome="failed").inc()
        log.error(
            "deployment failed",
            extra={"deployment_id": dep.id, "failure_class": fc, "error": message},
        )
        return "failed"

    def _fail_internal(self, job_id: str, message: str) -> str:
        """Last resort: record INTERNAL failure in a fresh session."""
        with self._sf() as s:
            job = s.get(Job, job_id)
            assert job is not None
            dep = s.get(Deployment, job.deployment_id)
            assert dep is not None
            if dep.status in (
                DeploymentStatus.REQUESTED,
                DeploymentStatus.SUCCEEDED,
                DeploymentStatus.FAILED,
                DeploymentStatus.ROLLED_BACK,
            ):
                # Nothing was started (or already terminal): leave for lease recovery/retry.
                job.status = JobStatus.PENDING if dep.status == DeploymentStatus.REQUESTED else JobStatus.DONE
                job.last_error = message
                commit(s)
                WORKER_JOBS.labels(outcome="internal_error").inc()
                return "internal_error"
            # VALIDATING/DEPLOYING: leave the lease to expire so reconciliation decides.
            job.last_error = message
            commit(s)
            WORKER_JOBS.labels(outcome="internal_error").inc()
            return "internal_error"

    # ---- reconciliation -------------------------------------------------------------------
    def recover_stale_jobs(self) -> int:
        """Reconcile jobs whose worker died or whose final DB commit failed.

        For a DEPLOYING deployment we ask the runtime what happened, rather than guessing:
          runtime says READY -> converge to SUCCEEDED without a second side effect;
          runtime has no record -> safe to requeue (idempotent deploy).
        """
        now = utcnow()
        recovered = 0
        with self._sf() as s:
            stale_ids = s.scalars(
                select(Job.id).where(Job.status == JobStatus.RUNNING, Job.lease_expires_at < now)
            ).all()
        for job_id in stale_ids:
            with self._sf() as s:
                job = s.get(Job, job_id)
                if job is None or job.status != JobStatus.RUNNING:
                    continue
                dep = s.get(Deployment, job.deployment_id)
                assert dep is not None
                set_correlation_id(dep.correlation_id)
                try:
                    recovered += self._reconcile_one(s, job, dep)
                except Exception:
                    s.rollback()
                    log.exception("reconciliation failed", extra={"deployment_id": dep.id})
                    RECONCILIATIONS.labels(result="error").inc()
                finally:
                    set_correlation_id(None)
        return recovered

    def _reconcile_one(self, s: Session, job: Job, dep: Deployment) -> int:
        if dep.status in (DeploymentStatus.SUCCEEDED, DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK):
            job.status = JobStatus.DONE
            commit(s)
            RECONCILIATIONS.labels(result="already_terminal").inc()
            return 1
        if dep.status == DeploymentStatus.DEPLOYING:
            mv = s.get(ModelVersion, dep.version_id)
            assert mv is not None
            seen = self._runtimes.get(mv.runtime).describe(dep.id)
            if seen is not None and seen.state == "READY":
                self._succeed(s, job, dep, seen, event="reconciled_success")
                RECONCILIATIONS.labels(result="converged_success").inc()
                log.warning("reconciled external success", extra={"deployment_id": dep.id})
                return 1
        job.status = JobStatus.PENDING
        job.lease_owner = None
        job.run_after = utcnow()
        commit(s)
        RECONCILIATIONS.labels(result="requeued").inc()
        return 1

    # ---- metrics --------------------------------------------------------------------------
    def publish_queue_metrics(self) -> None:
        with self._sf() as s:
            counts: dict[str, int] = {
                str(status): n
                for status, n in s.execute(select(Job.status, func.count()).group_by(Job.status))
            }
            oldest = s.scalar(select(func.min(Job.run_after)).where(Job.status == JobStatus.PENDING))
        for status in JobStatus:
            QUEUE_DEPTH.labels(status=status).set(counts.get(status, 0))
        OLDEST_PENDING_AGE.set(max((utcnow() - oldest).total_seconds(), 0) if oldest else 0)


def main() -> None:  # pragma: no cover - process entrypoint
    from app.bootstrap import build_container
    from app.observability import configure_logging

    container = build_container()
    configure_logging(container.settings.log_level)
    stop = threading.Event()
    DeploymentWorker(container.session_factory, container.settings, container.runtimes).run_forever(stop)


if __name__ == "__main__":  # pragma: no cover
    main()
