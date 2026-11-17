import httpx, re

BASE = "http://127.0.0.1:8090"

s = httpx.Client(follow_redirects=False)
r = s.get(f"{BASE}/profile")
print("GET /profile:", r.status_code)
print("  location:", r.headers.get("location"))
print("  body:", r.text[:200])