from __future__ import annotations

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
from .middleware import RequestContextMiddleware
from .services.bootstrap import bootstrap, get_organization
from .services.errors import DomainError
from .templating import render
from .web import admin as web_admin
from .web import auth as web_auth
from .web import dashboard as web_dashboard
from .web import employees as web_employees
from .web import leave as web_leave
from .web import org as web_org

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

    log.info(
        "PeaWork %s started (env=%s, db=%s)",
        __version__,
        settings.env,
        settings.database_url.split("@")[-1],
    )
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

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(web_auth.router)
    app.include_router(web_dashboard.router)
    app.include_router(web_employees.router)
    app.include_router(web_leave.router)
    app.include_router(web_org.router)
    app.include_router(web_admin.router)
    app.include_router(api_v1.router)
    app.include_router(api_devices.router)

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
