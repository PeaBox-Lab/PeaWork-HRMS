"""Smoke test for R4 goals/OKRs endpoints."""
import re
import json
import httpx

c = httpx.Client(base_url="http://127.0.0.1:8090", follow_redirects=False, timeout=15)

# login
page = c.get("/login")
m = re.search(r'name="(_csrf)"\s+value="([^"]+)"', page.text)
token = m.group(2) if m else None
r = c.post("/login", data={"_csrf": token, "email": "admin@example.com", "password": "AdminV2Pass123!", "next": "/"})
print("login status:", r.status_code)

# Get CSRF token for API calls
csrf_token = c.cookies.get("pw_csrf")
headers = {"X-CSRF-Token": csrf_token, "Content-Type": "application/json"}

# Create a goal
payload = {
    "code": "GOAL1",
    "title": "Increase customer satisfaction",
    "objective": "Improve CSAT score by 15%",
    "due_date": "2026-12-31"
}

r = c.post("/api/v1/goals", json=payload, headers=headers)
print("create goal status:", r.status_code)
if r.status_code == 201:
    print("create goal response:", json.dumps(r.json(), indent=2)[:500])
else:
    print("error:", r.text[:200])

# List goals
r = c.get("/api/v1/goals", headers=headers)
print("list goals status:", r.status_code)
if r.status_code == 200:
    print("list goals response:", json.dumps(r.json(), indent=2)[:500])
else:
    print("error:", r.text[:200])

# Get goal detail
goal_id = r.json()[0]["id"] if r.status_code == 200 and r.json() else None
if goal_id:
    r = c.get(f"/api/v1/goals/{goal_id}", headers=headers)
    print("get goal status:", r.status_code)
    if r.status_code == 200:
        print("get goal response:", json.dumps(r.json(), indent=2)[:500])
    else:
        print("error:", r.text[:200])

    # Update progress
    r = c.post(f"/api/v1/goals/{goal_id}/progress", json={"progress": 0.5, "note": "Halfway there"}, headers=headers)
    print("update progress status:", r.status_code)
    if r.status_code == 200:
        print("update progress response:", json.dumps(r.json(), indent=2)[:500])
    else:
        print("error:", r.text[:200])

    # Get progress history
    r = c.get(f"/api/v1/goals/{goal_id}/progress", headers=headers)
    print("progress history status:", r.status_code)
    if r.status_code == 200:
        print("progress history response:", json.dumps(r.json(), indent=2)[:500])
    else:
        print("error:", r.text[:200])

# Test duplicate code
r = c.post("/api/v1/goals", json={"code": "GOAL1", "title": "Duplicate", "objective": "Should fail"}, headers=headers)
print("duplicate goal status:", r.status_code, "(expect 422)")

# Test invalid progress
if goal_id:
    r = c.post(f"/api/v1/goals/{goal_id}/progress", json={"progress": 1.5}, headers=headers)
    print("invalid progress status:", r.status_code, "(expect 422)")