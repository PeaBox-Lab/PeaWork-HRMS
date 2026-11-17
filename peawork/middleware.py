from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .config import get_settings
from .cookies import clear_auth_cookies
from .db import session_scope
from .metrics import REQUEST_COUNT, REQUEST_LATENCY
from .security import client_ip, new_csrf_token
from .services.auth import resolve_session
from .services.bootstrap import get_organization

log = logging.getLogger("peawork.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id, security headers, CSRF bootstrap and session resolution.

    This middleware never reads the request body, so downstream form parsing
    and file uploads remain intact (the v1 CSRF bug is not repeated).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path.startswith("/static/"):
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            return response

        started = time.perf_counter()
        settings = get_settings()
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.client_ip = client_ip(request)

        csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
        set_csrf = False
        if not csrf_cookie:
            csrf_cookie = new_csrf_token()
            set_csrf = True
        request.state.csrf_token = csrf_cookie
        request.state.session = None
        request.state.user = None
        request.state.org = None

        cookie_token = request.cookies.get(settings.session_cookie_name)
        authorization = request.headers.get("authorization", "")
        bearer_token = (
            authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        )
        raw_token = cookie_token or bearer_token
        invalid_cookie = False

        try:
            with session_scope() as db:
                if raw_token:
                    session = resolve_session(db, raw_token)
                    if session is not None:
                        request.state.session = session
                        request.state.user = session.user
                    elif cookie_token:
                        invalid_cookie = True
                request.state.org = get_organization(db)
        except Exception as _e:
            import traceback as _tb
            print("MIDDLEWARE ERROR:", _tb.format_exc(), flush=True)
            log.exception("failed to resolve request context", extra={"request_id": request.state.request_id})

        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - started
            REQUEST_COUNT.labels(request.method, path, "500").inc()
            REQUEST_LATENCY.labels(request.method, path).observe(elapsed)
            raise

        elapsed = time.perf_counter() - started
        route = request.scope.get("route")
        route_path = getattr(route, "path", path)
        status = str(response.status_code)
        REQUEST_COUNT.labels(request.method, route_path, status).inc()
        REQUEST_LATENCY.labels(request.method, route_path).observe(elapsed)

        if set_csrf:
            response.set_cookie(
                settings.csrf_cookie_name,
                csrf_cookie,
                max_age=settings.session_ttl_hours * 3600,
                httponly=False,
                secure=settings.secure_cookies,
                samesite="lax",
                path="/",
                domain=settings.cookie_domain,
            )
        if invalid_cookie:
            clear_auth_cookies(response, settings)

        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        if settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if response.headers.get("content-type", "").startswith(("text/html", "application/json")):
            response.headers.setdefault("Cache-Control", "no-store")

        user = request.state.user
        log.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "route": route_path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 1),
                    "user_id": str(user.id) if user else None,
                    "ip": request.state.client_ip,
                }
            )
        )
        return response
