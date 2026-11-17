from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import get_settings
from .i18n import get_locale, translate
from .security import as_utc

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _date(value: date | datetime | None, fmt: str = "%d %b %Y") -> str:
    if value is None:
        return "—"
    return value.strftime(fmt)


def _datetime(value: datetime | None, fmt: str = "%d %b %Y, %H:%M") -> str:
    value = as_utc(value)
    if value is None:
        return "—"
    return value.strftime(fmt)


def _money(value: Decimal | float | int | None, currency: str = "INR") -> str:
    if value is None:
        return "—"
    return f"{currency} {Decimal(str(value)):,.2f}"


def _initials(first: str | None, last: str | None = None) -> str:
    parts = [p for p in [first, last] if p]
    return "".join(p[0].upper() for p in parts)[:2] or "?"


# i18n filter for templates
def _translate(key: str, request: Request | None = None) -> str:
    locale = "en"
    if request is not None:
        locale = get_locale(request)
    return translate(key, locale)


templates.env.filters["date"] = _date
templates.env.filters["datetime"] = _datetime
templates.env.filters["money"] = _money
templates.env.filters["initials"] = _initials
templates.env.filters["_"] = _translate
templates.env.globals["app_version"] = __version__


def render(request: Request, template: str, *, status_code: int = 200, **context: Any):
    settings = get_settings()
    locale = get_locale(request)
    base_context: dict[str, Any] = {
        "request": request,
        "settings": settings,
        "user": getattr(request.state, "user", None),
        "org": getattr(request.state, "org", None),
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "flash": request.query_params.get("msg"),
        "error_message": request.query_params.get("error"),
        "locale": locale,
        "_": lambda k: translate(k, locale),
    }
    base_context.update(context)
    return templates.TemplateResponse(
        request, template, base_context, status_code=status_code
    )
