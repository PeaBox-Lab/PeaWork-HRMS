import re, sqlite3, httpx

CSRF_RE = re.compile(r'name="_csrf"\s+value="([^"]+)"')
BASE = "http://127.0.0.1:8090"

s = httpx.Client(follow_redirects=False)

def csrf(text):
    m = CSRF_RE.search(text)
    return m.group(1) if m else None

# Login
r = s.get(f"{BASE}/login")
tok = csrf(r.text)
r = s.post(f"{BASE}/login", data={"_csrf": tok, "email": "admin@example.com", "password": "82F5#VKXZAi5nE5ctx_8mvwf", "next": "/"})

print("Cookies:", dict(s.cookies))
session_cookie = s.cookies.get("pw_session")
print("Session cookie:", session_cookie)

# Query DB for session
conn = sqlite3.connect("data/peawork_v2.db")
print("Sessions in DB:")
for row in conn.execute("SELECT id, user_id, substr(token_hash,1,20), revoked_at, expires_at, last_seen_at FROM sessions"):
    print(" ", row)

# Check hash_token directly
from peawork.security import hash_token
if session_cookie:
    print("Hash of cookie token:", hash_token(session_cookie))

conn.close()