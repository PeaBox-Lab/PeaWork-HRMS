from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from . import __version__
from .api import devices as api_devices
from .api import v1 as api_v1
from .config import get_settings
from .db import create_all, get_engine, session_scope
from .deps import LoginRequired, PasswordChangeRequired, wants_html
from .ha import get_ha_manager
from .middleware import RequestContextMiddleware
from .services.bootstrap import bootstrap, get_organization
from .services.errors import DomainError
from .templating import render
from .web import admin as web_admin
from .web import auth as web_auth
from .web import dashboard as web_dashboard
from .web import employees as web_employees
from .web import job_changes as web_job_changes
from .web import journeys as web_journeys
from .web import leave as web_leave
from .web import lifecycle as web_lifecycle
from .web import org as web_org
from .web import talent as web_talent

log = logging.getLogger("peawork")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    settings.storage_path.mkdir(parents=True, exist_ok=True)

    if settings.should_auto_create_schema:
        create_all()

    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
            tables = set(inspect(get_engine()).get_table_names())
            if "organizations" not in tables and not settings.should_auto_create_schema:
                raise RuntimeError(
                    "Database schema is empty. Run `alembic upgrade head` before starting."
                )
            if settings.should_auto_create_schema:
                result = bootstrap(db, settings, with_demo=settings.seed_demo_data)
                if result.generated_password:
                    log.warning(
                        "Generated bootstrap password for %s: %s  (change it immediately; "
                        "it is shown only once)",
                        result.admin.email,
                        result.generated_password,
                    )
    except SQLAlchemyError as exc:
        raise RuntimeError("Database is not reachable. Check PEAWORK_DATABASE_URL.") from exc

    # Register HA nodes (from config or defaults)
    ha_manager = get_ha_manager()
    if settings.ha_enabled and settings.ha_nodes:
        for node_id in settings.ha_nodes:
            await ha_manager.register_node(node_id)

    # Initialize DR / backup services
    if settings.backup_enabled:
        log.info("DR backups enabled (cron=%s, retention=%d days)",
                 settings.backup_schedule_cron, settings.backup_retention_days)

    # Initialize billing (log only; no external provider yet)
    if settings.billing_enabled:
        log.info("Billing enabled (default plan=%s, currency=%s)",
                 settings.billing_default_plan, settings.billing_currency)

    log.info(
        "PeaWork %s started (env=%s, db=%s)",
        __version__,
        settings.env,
        settings.database_url.split("@")[-1],
    )

    # Start a background readiness monitor
    asyncio.create_task(ha_manager._monitor_readiness())

    yield


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = not settings.is_production
    app = FastAPI(
        title="PeaWork HRMS",
        version=__version__,
        description="Open-source, Linux-hosted HRMS: people, leave, documents and audit.",
        lifespan=lifespan,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings

    if settings.allowed_host_list != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    app.add_middleware(RequestContextMiddleware)
    if settings.ha_enabled:
        from .ha import HAMiddleware

        app.add_middleware(HAMiddleware)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(web_auth.router)
    app.include_router(web_dashboard.router)
    app.include_router(web_employees.router)
    app.include_router(web_lifecycle.router)
    app.include_router(web_job_changes.router)
    app.include_router(web_journeys.router)
    app.include_router(web_talent.router)
    app.include_router(web_leave.router)
    app.include_router(web_org.router)
    app.include_router(web_admin.router)
    app.include_router(api_v1.router)
    app.include_router(api_devices.router)

    # R5 — OpenTelemetry observability (no-op unless PEAWORK_OTEL_ENABLED=true)
    from .otel import init_otel as init_observability

    init_observability(app)

    _install_error_handlers(app)
    _install_ops_routes(app)
    return app


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type="application/problem+json",
    )


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if wants_html(request):
            next_path = request.url.path
            if request.url.query:
                next_path += "?" + request.url.query
            return RedirectResponse(f"/login?next={next_path}", status_code=303)
        return _problem(401, "Unauthorized", "Authentication required")

    @app.exception_handler(PasswordChangeRequired)
    async def _password_change_required(request: Request, exc: PasswordChangeRequired):
        if request.url.path.startswith("/api/"):
            return _problem(403, "Password change required", "Change the temporary password before continuing")
        return RedirectResponse(
            "/profile?error=Change+your+temporary+password+before+continuing", status_code=303
        )

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError):
        if request.url.path.startswith("/api/"):
            return _problem(exc.status_code, "Request rejected", exc.message)
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=exc.message,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return _problem(exc.status_code, "Request failed", str(exc.detail))
        if exc.status_code == 401:
            return RedirectResponse("/login", status_code=303)
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            code=exc.status_code,
            message=str(exc.detail),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        if request.url.path.startswith("/api/"):
            return _problem(500, "Internal error", "An unexpected error occurred")
        return render(
            request,
            "error.html",
            status_code=500,
            code=500,
            message="An unexpected error occurred. The incident has been logged.",
        )


def _install_ops_routes(app: FastAPI) -> None:
    settings = get_settings()

    @app.get("/health", tags=["ops"])
    def health():
        settings = get_settings()
        database = "postgresql" if settings.database_url.startswith("postgresql") else "sqlite"
        return {
            "status": "ok",
            "version": __version__,
            "deployment": settings.env,
            "database": database,
        }

    @app.get("/health/live", tags=["ops"])
    def health_live():
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready", tags=["ops"])
    def health_ready():
        try:
            with session_scope() as db:
                db.execute(text("SELECT 1"))
                org = get_organization(db)
            return {
                "status": "ready",
                "version": __version__,
                "database": "ok",
                "organization": org.name if org else None,
            }
        except Exception:
            log.exception("readiness check failed")
            return JSONResponse(
                {"status": "not_ready", "database": "unavailable"}, status_code=503
            )

    # HA routes (only if ha_enabled)
    @app.get("/health/ha", tags=["ops"])
    def health_ha():
        manager = get_ha_manager()
        return manager.readiness_status()

    @app.post("/health/ha/register", tags=["ops"])
    def ha_register(node_id: str):
        import asyncio

        manager = get_ha_manager()
        asyncio.create_task(manager.register_node(node_id))
        return {"detail": "node registered"}

    @app.post("/health/ha/heartbeat/{node_id}", tags=["ops"])
    def ha_heartbeat(node_id: str):
        import asyncio

        manager = get_ha_manager()
        asyncio.create_task(manager.heartbeat(node_id))
        return {"detail": "heartbeat recorded"}

    # Backup/DR routes (if enabled)
    if settings.backup_enabled:
        from .backup import (
            create_backup as _create_backup,
            restore_backup as _restore_backup,
            list_backups as _list_backups,
            delete_backup as _delete_backup,
            pitr_status as _pitr_status,
            cleanup_old_backups,
            cleanup_old_wal_files,
        )

        @app.get("/health/dr", tags=["ops"])
        def health_dr():
            return {
                "backup_enabled": settings.backup_enabled,
                "cross_region": settings.backup_cross_region,
                "pitr_enabled": settings.pitr_enabled,
                "retention_days": settings.backup_retention_days,
            }

        @app.get("/health/dr/backups", tags=["ops"])
        def dr_list_backups():
            return _list_backups()

        @app.post("/health/dr/backups", tags=["ops"])
        def dr_create_backup(label: str = "manual"):
            return _create_backup(label=label)

        @app.post("/health/dr/backups/{filename}/restore", tags=["ops"])
        def dr_restore_backup(filename: str):
            return _restore_backup(filename)

        @app.delete("/health/dr/backups/{filename}", tags=["ops"])
        def dr_delete_backup(filename: str):
            _delete_backup(filename)
            return {"detail": "backup deleted"}

        @app.get("/health/dr/pitr", tags=["ops"])
        def dr_pitr_status():
            return _pitr_status()

        @app.post("/health/dr/cleanup", tags=["ops"])
        def dr_cleanup(backup_days: int | None = None, wal_days: int | None = None):
            backups_deleted = cleanup_old_backups(backup_days)
            wal_deleted = cleanup_old_wal_files(wal_days)
            return {
                "backups_deleted": len(backups_deleted),
                "wal_files_deleted": wal_deleted,
                "details": {"backups": backups_deleted, "wal": []},
            }

    @app.get("/metrics", tags=["ops"])
    def metrics(request: Request):
        settings = get_settings()
        if settings.metrics_token:
            supplied = request.headers.get("x-metrics-token") or request.headers.get(
                "authorization", ""
            ).removeprefix("Bearer ").strip()
            if supplied != settings.metrics_token:
                return _problem(401, "Unauthorized", "A valid metrics token is required")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app = create_app()
