from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..domain import AuditAction
from ..models import LoginAttempt, Session as AuthSession, User
from ..security import (
    as_utc,
    hash_password,
    hash_token,
    new_csrf_token,
    new_token,
    password_needs_rehash,
    utcnow,
    verify_password,
)
from . import audit
from .errors import ValidationError

THROTTLE_WINDOW = timedelta(minutes=15)
MAX_FAILURES = 5

# Pre-computed hash keeps failed-login timing close to the success path.
_DUMMY_HASH = hash_password("not-a-real-password")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _recent_failures(db: Session, email: str, ip: str | None) -> int:
    cutoff = utcnow() - THROTTLE_WINDOW
    stmt = select(func.count(LoginAttempt.id)).where(
        LoginAttempt.email == email,
        LoginAttempt.success.is_(False),
        LoginAttempt.created_at >= cutoff,
    )
    if ip:
        stmt = stmt.where(LoginAttempt.ip == ip)
    return int(db.scalar(stmt) or 0)


def _record_attempt(db: Session, email: str, ip: str | None, success: bool) -> None:
    db.add(LoginAttempt(email=email, ip=ip, success=success))


def is_throttled(db: Session, email: str, ip: str | None) -> bool:
    return _recent_failures(db, normalize_email(email), ip) >= MAX_FAILURES


def authenticate(
    db: Session,
    *,
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User | None:
    email = normalize_email(email)
    if is_throttled(db, email, ip):
        audit.record(
            db,
            actor=None,
            action=AuditAction.LOGIN_FAILED,
            entity_type="auth",
            details={"email": email, "reason": "throttled"},
        )
        return None

    user = db.scalar(select(User).where(User.email == email))
    stored = user.password_hash if user else _DUMMY_HASH
    valid = verify_password(password, stored)

    if not user or not user.active or not valid:
        _record_attempt(db, email, ip, False)
        audit.record(
            db,
            actor=None,
            action=AuditAction.LOGIN_FAILED,
            entity_type="auth",
            org_id=user.org_id if user else None,
            details={"email": email, "reason": "invalid_credentials"},
        )
        db.flush()
        return None

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    _record_attempt(db, email, ip, True)
    audit.record(db, actor=user, action=AuditAction.LOGIN, entity_type="auth")
    db.flush()
    return user


def create_session(
    db: Session,
    *,
    user: User,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, str, AuthSession]:
    settings = get_settings()
    raw_token = new_token()
    csrf_token = new_csrf_token()
    now = utcnow()
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        csrf_hash=hash_token(csrf_token),
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=now,
    )
    db.add(session)
    db.flush()
    return raw_token, csrf_token, session


def resolve_session(db: Session, raw_token: str) -> AuthSession | None:
    settings = get_settings()
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token)))
    if session is None or session.revoked_at is not None:
        return None
    now = utcnow()
    if as_utc(session.expires_at) <= now:
        return None
    last_seen = as_utc(session.last_seen_at) or now
    if now - last_seen > timedelta(minutes=settings.session_idle_minutes):
        return None
    if session.user is None or not session.user.active:
        return None
    # Throttle the last-seen write to once per 5 minutes.
    if now - last_seen > timedelta(minutes=5):
        session.last_seen_at = now
        db.flush()
    return session


def revoke_session(db: Session, session: AuthSession) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        db.flush()


def revoke_all_sessions(db: Session, user_id: UUID) -> int:
    sessions = db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
    ).all()
    for session in sessions:
        session.revoked_at = utcnow()
    db.flush()
    return len(sessions)


def change_password(db: Session, user: User, new_password: str) -> None:
    # request.state.user is loaded by middleware in a separate, closed session.
    # Re-attach to the request transaction before mutating anything.
    if user not in db:
        persisted = db.get(User, user.id)
        if persisted is None:
            raise ValidationError("User account not found")
        user = persisted
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    revoke_all_sessions(db, user.id)
    db.flush()
