"""Live smoke test for R4 Talent: review cycles + goals/OKRs.

Runs against the dev server at http://127.0.0.1:8090.
Usage:
    .\.venv\Scripts\python.exe smoke_talent.py
"""
from __future__ import annotations

import json
import re
import sys

import httpx


BASE = "http://127.0.0.1:8090"


def main() -> int:
    c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=15)
    fails = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal fails
        status = "PASS" if cond else "FAIL"
        if not cond:
            fails += 1
        print(f"[{status}] {name} {extra}")

    # --- login admin ---
    page = c.get("/login")
    m = re.search(r'name="(_csrf)"\s+value="([^"]+)"', page.text)
    token = m.group(2) if m else None
    r = c.post(
        "/login",
        data={
            "_csrf": token,
            "email": "admin@example.com",
            "password": "AdminV2Pass123!",
            "next": "/",
        },
    )
    check("login", r.status_code == 303)
    csrf_token = c.cookies.get("pw_csrf")
    headers = {"X-CSRF-Token": csrf_token, "Content-Type": "application/json"}

    # --- review cycle: create ---
    r = c.post(
        "/api/v1/review-cycles",
        json={
            "name": "H1 Smoke",
            "code": "H1SMOKE",
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=headers,
    )
    check("create review cycle", r.status_code == 201, f"(got {r.status_code})")

    # --- review cycle: duplicate code ---
    r = c.post(
        "/api/v1/review-cycles",
        json={
            "name": "Dup",
            "code": "H1SMOKE",
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
        },
        headers=headers,
    )
    check("duplicate cycle code rejected", r.status_code == 422, f"(got {r.status_code})")

    # --- review cycle: list ---
    r = c.get("/api/v1/review-cycles", headers=headers)
    check("list review cycles", r.status_code == 200 and isinstance(r.json(), list),
          f"(got {r.status_code})")

    # --- review cycle: detail ---
    r = c.get("/api/v1/review-cycles/H1SMOKE", headers=headers)
    check("review cycle detail", r.status_code == 200 and r.json().get("code") == "H1SMOKE",
          f"(got {r.status_code})")

    # --- review cycle: close ---
    r = c.post("/api/v1/review-cycles/H1SMOKE/close", json={}, headers=headers)
    check("close review cycle", r.status_code == 200 and r.json().get("status") == "closed",
          f"(got {r.status_code})")

    # --- review cycle: close idempotent ---
    r = c.post("/api/v1/review-cycles/H1SMOKE/close", json={}, headers=headers)
    check("close review cycle idempotent", r.status_code == 200, f"(got {r.status_code})")

    # --- goal: create ---
    r = c.post(
        "/api/v1/goals",
        json={
            "code": "SMOKE_GOAL_1",
            "title": "Reduce ticket resolution time",
            "objective": "Improve SLA compliance by 20%",
            "due_date": "2026-12-31",
        },
        headers=headers,
    )
    check("create goal", r.status_code == 201, f"(got {r.status_code})")
    goal_id = r.json()["id"] if r.status_code == 201 else None

    # --- goal: duplicate code ---
    r = c.post(
        "/api/v1/goals",
        json={
            "code": "SMOKE_GOAL_1",
            "title": "Dup Goal",
            "objective": "Should fail",
        },
        headers=headers,
    )
    check("duplicate goal code rejected", r.status_code == 422, f"(got {r.status_code})")

    # --- goal: list ---
    r = c.get("/api/v1/goals", headers=headers)
    check("list goals", r.status_code == 200 and isinstance(r.json(), list),
          f"(got {r.status_code})")

    # --- goal: detail ---
    if goal_id:
        r = c.get(f"/api/v1/goals/{goal_id}", headers=headers)
        check("goal detail", r.status_code == 200 and r.json().get("id") == goal_id,
              f"(got {r.status_code})")

    # --- goal: progress update ---
    if goal_id:
        r = c.post(
            f"/api/v1/goals/{goal_id}/progress",
            json={"progress": 0.33, "note": "Initial progress"},
            headers=headers,
        )
        check("update goal progress", r.status_code == 200 and r.json().get("progress") == 0.33,
              f"(got {r.status_code})")

        # --- goal: progress history ---
        r = c.get(f"/api/v1/goals/{goal_id}/progress", headers=headers)
        check("goal progress history", r.status_code == 200 and isinstance(r.json(), list),
              f"(got {r.status_code})")

    # --- goal: invalid progress ---
    if goal_id:
        r = c.post(
            f"/api/v1/goals/{goal_id}/progress",
            json={"progress": 1.5},
            headers=headers,
        )
        check("invalid progress rejected", r.status_code == 422, f"(got {r.status_code})")

    print()
    if fails:
        print(f"SMOKE: {fails} failure(s)")
        return 1
    print("SMOKE: all talent checks PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
