from __future__ import annotations

from fastapi import Response

from .config import Settings


def set_auth_cookies(
    response: Response, settings: Settings, *, session_token: str, csrf_token: str
) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
        domain=settings.cookie_domain,
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
        domain=settings.cookie_domain,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name, path="/", domain=settings.cookie_domain
    )
    response.delete_cookie(
        settings.csrf_cookie_name, path="/", domain=settings.cookie_domain
    )
