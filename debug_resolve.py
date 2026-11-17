from peawork.db import session_scope
from peawork.services.auth import resolve_session

TOKEN = "Co8uSL7UCy77o2d6femdpjhRToilCjY1QMaqXBDDrU8"

with session_scope() as db:
    s = resolve_session(db, TOKEN)
    print("resolve_session:", s)
    if s:
        print("  user:", s.user)
        print("  expires_at:", s.expires_at)
        print("  last_seen_at:", s.last_seen_at)
        print("  revoked_at:", s.revoked_at)