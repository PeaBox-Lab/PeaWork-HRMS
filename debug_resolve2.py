from datetime import timedelta, timezone
from peawork.db import session_scope
from peawork.services.auth import resolve_session
from peawork.models import Session as AuthSessionModel
from peawork.security import hash_token, utcnow, as_utc
from sqlalchemy import select

TOKEN = "Co8uSL7UCy77o2d6femdpjhRToilCjY1QMaqXBDDrU8"

with session_scope() as db:
    session = db.scalar(select(AuthSessionModel).where(AuthSessionModel.token_hash == hash_token(TOKEN)))
    print("session found:", session is not None)
    if session:
        print("  revoked_at:", session.revoked_at)
        print("  expires_at:", session.expires_at, "type:", type(session.expires_at))
        print("  last_seen_at:", session.last_seen_at, "type:", type(session.last_seen_at))
        print("  user:", session.user)
        print("  user.active:", session.user.active if session.user else None)

        now = utcnow()
        print("  now:", now)
        exp = as_utc(session.expires_at)
        print("  as_utc(expires_at):", exp)
        print("  exp <= now:", exp <= now)
        ls = as_utc(session.last_seen_at)
        print("  now - last_seen:", now - ls)
        print("  idle threshold:", timedelta(minutes=120))
        print("  idle exceeded:", now - ls > timedelta(minutes=120))
