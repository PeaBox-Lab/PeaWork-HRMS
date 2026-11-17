from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request

from .config import get_settings
from .db import get_db  # re-export for dependencies
from .domain import Role
from .models import User
from .security import client_ip, constant_time_equals, hash_token


class LoginRequired(Exception):
    """Raised when a browser request needs an authenticated session."""


class PasswordChangeRequired(Exception):
    """Raised when a user must replace a generated or reset password."""


def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise LoginRequired()
    return user


def require_user(
    *roles: Role | str, allow_password_change: bool = False
) -> Callable[[Request], User]:
    allowed = {Role(r) if not isinstance(r, Role) else r for r in roles}

    def dependency(request: Request) -> User:
        user = current_user(request)
        if allowed and user.role not in allowed:
            raise HTTPException(status_code=403, detail="You do not have permission for this action")
        if user.must_change_password and not allow_password_change:
            raise PasswordChangeRequired()
        return user

    return dependency


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or not request.url.path.startswith("/api/")


async def csrf_guard(request: Request) -> None:
    """Double-submit CSRF for cookie-authenticated unsafe requests.

    Bearer-token API clients are not vulnerable to CSRF and are exempt.
    """
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return

    settings = get_settings()
    cookie = request.cookies.get(settings.csrf_cookie_name)
    submitted = request.headers.get("x-csrf-token")
    if not submitted:
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            value = form.get("_csrf")
            submitted = str(value) if value is not None else None

    if not constant_time_equals(submitted, cookie):
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    session = getattr(request.state, "session", None)
    if session is not None and not constant_time_equals(hash_token(submitted or ""), session.csrf_hash):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def request_ip(request: Request) -> str | None:
    return client_ip(request)


__all__ = [
    "LoginRequired",
    "PasswordChangeRequired",
    "csrf_guard",
    "current_user",
    "get_db",
    "request_ip",
    "require_user",
    "wants_html",
]
