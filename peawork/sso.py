"""OpenID Connect / SSO authentication support for R5.

Provides a foundation for OIDC and SAML 2.0 single sign-on integration.
This module is optional — when ``sso_enabled`` is ``False`` (default) it
is a no‑op and all existing auth (session-based, Argon2id passwords, CSRF)
continues to work unchanged.

Typical deployment flow:

1. Set ``PEAWORK_SSO_ENABLED=true`` in production.
2. Configure the IdP endpoints (authorization, token, userinfo, JWKS).
3. Set ``PEAWORK_SSO_REDIRECT_URL`` to match the IdP callback route.
4. Add the IdP's client ID/secret and scope.
5. Mount the ``/sso`` routes (see ``main.py`` / ``api/v1.py``).
6. The middleware populates ``request.state.user`` with the IdP‑derived
   user record (or creates/updates a local ``User`` row).

Docstrings and type stubs follow the existing PeaWork conventions.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from sqlalchemy import select, update, or_, func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..domain import Role, CheckInStatus
from ..models import User, Employee, Organization
from ..services.audit import record as audit_record


# ---------------------------------------------------------------------------
# Helper: derive or discover a display name from an IdP claim
# ---------------------------------------------------------------------------

def _display_name_from_claims(claims: Dict[str, Any]) -> str:
    """Extract a full name from common OIDC / SAML claim names."""
    # Prefer "name", then "full_name", then join "first" + "last"
    for key in ("name", "full_name", "display_name"):
        if key in claims and claims[key]:
            return str(claims[key])
    first = claims.get("given_name", "") or ""
    last = claims.get("family_name", "") or ""
    if first and last:
        return f"{first} {last}".strip()
    if first:
        return first
    # Fallback to the subject (sub) claim
    sub = claims.get("sub", "")
    if sub:
        return str(sub)
    return "Unknown User"


# ---------------------------------------------------------------------------
# SCIM Models / Helpers
# ---------------------------------------------------------------------------

SCIM_ME = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_ATTRS = {
    "userName", "name", "familyName", "givenName", "middleName",
    "emails", "phoneNumbers", "addresses", "photos", "locale",
    "timezone", "title", "department", "manager", "organizationUrl",
    "admin", "externalId", "meta"
}

SCIM_NAME_FIELDS = {"familyName", "givenName", "middleName", "honorificPrefix", "honorificSuffix"}


def scim_attribute_filter_to_sql(filter_str: str, attr_map: Dict[str, str]) -> str:
    """Very naive SCIM filter -> SQL ``WHERE`` clause.

    This is a stub for R5 — a production implementation would use
    ``authlib``, ``cattrs``, or a dedicated SCIM library.
    """
    # Acceptable filters: eq Ne attr eq
    # Example: "userName eq \"bjensen\""
    # We only support simple equality on known attributes
    if not filter_str or 'eq' not in filter_str.lower():
        return ""

    # Very simple parse: "userName eq \"value\""
    try:
        parts = filter_str.split("eq")
        if len(parts) != 2:
            return ""
        attr = parts[0].strip()
        value = parts[1].strip().strip('"').strip("'")
        if attr in attr_map:
            # Return a simple SQL condition; the caller must use parameterized queries
            return f"{attr_map[attr]} = '{value}'"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# SCIM User Operations
# ---------------------------------------------------------------------------

def scim_list_users(
    db: Session,
    *,
    filter: str | None = None,
    filter_attrs: List[str] | None = None,
    start_index: int = 1,
    count: int = 10,
    sort_by: str | None = None,
) -> Dict[str, Any]:
    """SCIM ``GET /Users`` list response.

    Returns a SCIM-compliant JSON body with ``Resources`` and pagination.
    """
    from sqlalchemy import select

    stmt = select(User)
    total = db.scalar(func.count(User.id))

    # Apply simple filter if provided
    if filter:
        # Map SCIM attribute names to DB column names via a simple dict
        attr_map = {
            "userName": "user_name",  # would be a column; for now we skip
            "familyName": "full_name",
            "givenName": "",  # no separate column
            "emails": "",
        }
        sql_cond = scim_attribute_filter_to_sql(filter, attr_map)
        if sql_cond:
            stmt = stmt.where(func.sql(sql_cond))  # type: ignore[arg-type]

    # Order by meta.lastModified desc (freshest first)
    stmt = stmt.order_by(func.coalesce(User.updated_at, User.created_at).desc())

    # Pagination
    offset = (start_index - 1) * count
    stmt = stmt.offset(offset).limit(count)

    users = db.scalars(stmt).all()
    resources = [_scim_user_resource(u) for u in users]

    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "totalTotal": total,
        "startIndex": start_index,
        "count": len(resources),
        "Resources": resources,
    }


def _scim_user_resource(user: User) -> Dict[str, Any]:
    """Convert a User ORM row into a SCIM User resource dict."""
    # Build name sub-object
    name_parts: Dict[str, Any] = {}
    for field in SCIM_NAME_FIELDS:
        val = getattr(user, field, "") or ""
        if val:
            name_parts[field] = val

    # Build emails list
    emails: List[Dict[str, Any]] = []
    # We don't have a separate Email model here; emit a primary email if available
    if user.email:
        emails.append({
            "value": user.email,
            "type": "work",
            "primary": True,
        })

    # Build the resource
    resource: Dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "externalId": user.sso_provider_subject if user.is_sso_user else str(user.id),
        "userName": user.email or "",
        "name": {
            "familyName": name_parts.get("familyName", ""),
            "givenName": name_parts.get("givenName", ""),
        } if name_parts else None,
        "emails": emails,
        "active": user.is_active,
        "meta": {
            "resourceType": "User",
            "created": user.created_at.isoformat() if user.created_at else "",
            "lastModified": user.updated_at.isoformat() if user.updated_at else user.created_at.isoformat() if user.created_at else "",
        },
    }
    if user.full_name:
        resource["name"]["formatted"] = user.full_name
    if user.sso_picture:
        resource["photos"] = [{"value": user.sso_picture}]

    return resource


def scim_get_user(db: Session, user_id: str) -> Optional[Dict[str, Any]]:
    """SCIM ``GET /Users/{id}`` response."""
    user = db.get(User, user_id)
    if user is None:
        return None
    return _scim_user_resource(user)


def scim_create_user(
    db: Session,
    *,
    user_name: str,
    emails: List[Dict[str, Any]],
    given_name: str | None = None,
    family_name: str | None = None,
    middle_name: str | None = None,
    password: str | None = None,
) -> Dict[str, Any]:
    """SCIM ``POST /Users`` create response.

    For SSO users, ``password`` is accepted but ignored — login is via IdP.
    """
    # Derive name parts
    gn = given_name or ""
    fn = family_name or ""
    fm = middle_name or ""

    # Find or create user by userName (email) or externalId
    user = db.scalar(
        select(User).where(User.user_name == user_name)
        if user_name
        else select(User).where(User.sso_provider_subject == user_name)
    )

    if user is None:
        # Generate a random password if one was provided (we'll hash it but
        # SSO login will always be the path forward)
        pwd_hash = ""
        if password:
            from ..security import hash_password
            pwd_hash = hash_password(password)

        user = User(
            user_name=user_name,
            email=emails[0]["value"] if emails else "",
            full_name=f"{fn} {gn}".strip() if fn or gn else "SSO User",
            password_hash=pwd_hash,
            must_change_password=bool(password),  # force reset if password was given
            role=Role.EMPLOYEE,
            is_sso_user=True,
            sso_provider="scim_onboard",
            sso_provider_subject=user_name,
        )
        db.add(user)
        db.flush()
    else:
        # Update existing
        user.user_name = user_name
        user.email = emails[0]["value"] if emails else user.email
        user.full_name = f"{fn} {gn}".strip() if fn or gn else user.full_name
        if password:
            from ..security import hash_password
            user.password_hash = hash_password(password)
            user.must_change_password = True
        user.updated_at = utcnow()
        db.flush()

    return _scim_user_resource(user)


def scim_update_user(
    db: Session,
    *,
    user_id: str,
    operations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """SCIM ``PUT /Users/{id}`` update (PATCH) response.

    Supports the standard SCIM ``add``, ``replace``, ``remove`` operations
    on known attributes.
    """
    user = db.get(User, user_id)
    if user is None:
        return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "id": user_id, "detail": "notFound"}

    for op in operations:
        op_type = op.get("operation")
        path = op.get("path", "").lower()
        value = op.get("value")

        if op_type == "replace" and path == "emails":
            # Replace the primary email
            if value and isinstance(value, dict):
                user.email = value.get("value", "")
                user.updated_at = utcnow()
        elif op_type == "replace" and path == "name":
            # Replace name sub-object
            name_val = value or {}
            if "givenName" in name_val:
                user.full_name = f"{name_val.get('givenName', '')} {name_val.get('familyName', '')}".strip() or user.full_name
            if "familyName" in name_val:
                user.full_name = f"{user.full_name} {name_val.get('familyName', '')}".strip() if user.full_name else name_val.get("familyName", "")
            if "givenName" not in name_val and "familyName" not in name_val:
                # fallback: set formatted name
                user.full_name = name_val.get("formatted", user.full_name)
            if user.full_name:
                user.updated_at = utcnow()
        elif op_type == "remove" and path == "emails":
            user.email = ""
            user.updated_at = utcnow()

    db.flush()
    return _scim_user_resource(user)


def scim_delete_user(db: Session, user_id: str) -> Dict[str, Any]:
    """SCIM ``DELETE /Users/{id}`` response.

    SCIM spec says 200 with Resources body or 204 No Content. We return 200.
    """
    user = db.get(User, user_id)
    if user is None:
        return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "id": user_id, "detail": "notFound"}
    # Soft-delete: mark as inactive rather than hard delete
    user.is_active = False
    user.updated_at = utcnow()
    db.flush()
    return _scim_user_resource(user)


# ---------------------------------------------------------------------------
# Provisioning: sync User from SCIM request
# ---------------------------------------------------------------------------

def scim_provision_user(
    db: Session,
    *,
    operation: str,
    user_data: Dict[str, Any],
) -> Dict[str, Any]:
    """High‑level provisioning entry point.

    Maps SCIM ``operation`` one of: ``"add"``, ``"replace"``, ``"remove"``
    to the corresponding helper.  Used by the R5 provisioning endpoint.
    """
    op = operation.lower()
    if op == "query":
        return scim_list_users(db)
    elif op == "create":
        return scim_create_user(
            db,
            user_name=user_data.get("userName", ""),
            emails=user_data.get("emails", []),
            given_name=user_data.get("name", {}).get("givenName") if isinstance(user_data.get("name"), dict) else None,
            family_name=user_data.get("name", {}).get("familyName") if isinstance(user_data.get("name"), dict) else None,
        )
    elif op == "patch":
        return scim_update_user(db, user_id=user_data.get("id", ""), operations=user_data.get("Operations", []))
    elif op == "delete":
        return scim_delete_user(db, user_data.get("id", ""))
    elif op == "get":
        return scim_get_user(db, user_data.get("id", ""))
    else:
        return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "id": "", "detail": f"unsupported operation: {op}"}


# ---------------------------------------------------------------------------
# Exported for main.py / api/v1.py wiring
# ---------------------------------------------------------------------------

__all__ = [
    "sso_upsert_user",
    "sso_verify_id_token",
    "sso_login_url",
    "sso_logout_url",
    "sso_auth_middleware",
    "_display_name_from_claims",
    "scim_list_users",
    "scim_get_user",
    "scim_create_user",
    "scim_update_user",
    "scim_delete_user",
    "scim_provision_user",
    "scim_attribute_filter_to_sql",
    "_display_name_from_claims",
]