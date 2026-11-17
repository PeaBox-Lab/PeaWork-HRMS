from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import ValidationError


class FormError(ValueError):
    pass


def text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_date(value: str | None, field: str = "date") -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FormError(f"{field} must be a valid date (YYYY-MM-DD)") from exc


def parse_uuid(value: str | None, field: str = "id") -> UUID | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise FormError(f"{field} is invalid") from exc


def parse_decimal(value: str | None, field: str = "amount") -> Decimal | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise FormError(f"{field} must be a number") from exc


def validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "Invalid input")
    return f"{location}: {message}" if location else message


__all__ = [
    "FormError",
    "parse_date",
    "parse_decimal",
    "parse_uuid",
    "text",
    "validation_message",
]
