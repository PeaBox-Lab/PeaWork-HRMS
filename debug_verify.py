from peawork.security import verify_password
import sqlite3

conn = sqlite3.connect("data/peawork_v2.db")
hash_ = conn.execute("SELECT password_hash FROM users WHERE email='admin@example.com'").fetchone()[0]
conn.close()

print("stored hash:", hash_)
print("verify AdminV2Pass123!:", verify_password("AdminV2Pass123!", hash_))
print("verify 82F5#VKXZAi5nE5ctx_8mvwf:", verify_password("82F5#VKXZAi5nE5ctx_8mvwf", hash_))