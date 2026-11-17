import sqlite3
from peawork.security import verify_password

conn = sqlite3.connect("data/peawork_v2.db")
hash_ = conn.execute("SELECT password_hash FROM users WHERE email='admin@example.com'").fetchone()[0]
conn.close()

candidates = [
    "82F5#VKXZAi5nE5ctx_8mvwf",
    "DevSmokePass123!",
    "ChangeMe123!",
    "AdminTest123!",
    "ChangeThisNow123!",
]
for c in candidates:
    print(f"{c!r:35} -> {verify_password(c, hash_)}")