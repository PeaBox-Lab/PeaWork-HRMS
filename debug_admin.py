import sqlite3

conn = sqlite3.connect("data/peawork_v2.db")
print("admin:", conn.execute("SELECT email, password_hash, active, must_change_password FROM users WHERE email='admin@example.com'").fetchone())
conn.close()