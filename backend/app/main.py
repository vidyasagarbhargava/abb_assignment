import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes_deployments, routes_registry
from app.api.schemas import HealthOut
from app.bootstrap import Container, build_container
from app.domain.enums import JobStatus
from app.domain.errors import DomainError
from app.observability import (
    HTTP_LATENCY,
    HTTP_REQUESTS,
    configure_logging,
    get_correlation_id,
    new_correlation_id,
    set_correlation_id,
)
from app.orm import Job
from app.worker import DeploymentWorker

log = logging.getLogger("mlops.api")


def _error(status: int, code: str, message: str, details: object = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "correlation_id": get_correlation_id(),
            }
        },
    )


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    configure_logging(container.settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = threading.Event()
        thread: threading.Thread | None = None
        if container.settings.embedded_worker:
            worker = DeploymentWorker(container.session_factory, container.settings, container.runtimes)
            thread = threading.Thread(target=worker.run_forever, args=(stop,), daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=5)

    app = FastAPI(
        title="MLOps Platform API",
        version="1.0.0",
        description="Register, approve, deploy, monitor and roll back ML models.",
        lifespan=lifespan,
    )
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=container.settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-ID", "Idempotent-Replay", "Location"],
    )

    @app.middleware("http")
    async def correlation_and_metrics(request: Request, call_next):  # type: ignore[no-untyped-def]
        cid = request.headers.get("X-Correlation-ID") or new_correlation_id()
        token = set_correlation_id(cid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = getattr(request.scope.get("route"), "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)
            if route not in ("/health/live", "/metrics"):
                log.info(
                    "request",
                    extra={
                        "method": request.method, "route": route, "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )  # fmt: skip
            set_correlation_id(None)
            del token

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return _error(422, "VALIDATION_ERROR", "Request validation failed.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException | HTTPException) -> JSONResponse:
        return _error(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "An unexpected error occurred.")

    app.include_router(routes_registry.router)
    app.include_router(routes_deployments.router)

    @app.get("/health/live", response_model=HealthOut, tags=["ops"])
    def live() -> HealthOut:
        return HealthOut(status="ok")

    @app.get("/health/ready", response_model=HealthOut, tags=["ops"])
    def ready(response: Response) -> HealthOut:
        checks: dict[str, object] = {}
        try:
            with container.session_factory() as s:
                s.execute(text("SELECT 1"))
                pending = s.scalar(
                    select(Job.run_after)
                    .where(Job.status == JobStatus.PENDING)
                    .order_by(Job.run_after)
                    .limit(1)
                )
            checks["database"] = "ok"
            checks["runtimes"] = container.runtimes.names()
            checks["oldest_pending_job"] = pending.isoformat() if pending else None
        except Exception as exc:
            log.error("readiness check failed", extra={"error": str(exc)})
            response.status_code = 503
            return HealthOut(status="unavailable", checks={"database": "error"})
        return HealthOut(status="ok", checks=checks)

    @app.get("/health", response_model=HealthOut, tags=["ops"])
    def health(response: Response) -> HealthOut:
        return ready(response)

    @app.get("/metrics", include_in_schema=False)
    def prometheus() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
