"""End-to-end smoke test for Phase 2 Global Payroll endpoints via the live v2 API.

Run against a live server:
    python -m uvicorn peawork.main:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import re
import sys

import httpx

BASE = "http://127.0.0.1:8090"
CSRF_PATTERN = re.compile(r'name="_csrf"\s+value="([^"]+)"')
failures: list[str] = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def main() -> int:
    c = httpx.Client(base_url=BASE, follow_redirects=False, timeout=30)

    page = c.get("/login")
    m = CSRF_PATTERN.search(page.text)
    token = m.group(1) if m else None
    r = c.post(
        "/login",
        data={"_csrf": token, "email": "admin@example.com", "password": "AdminV2Pass123!", "next": "/"},
    )
    check("POST /login admin -> 303", r.status_code == 303)
    csrf = c.cookies.get("pw_csrf")
    check("pw_csrf cookie present", bool(csrf))
    headers = {"X-CSRF-Token": csrf}

    # Statutory rule CRUD
    r = c.post(
        "/api/v1/statutory-rule-versions",
        json={
            "rule_type": "tds",
            "country_code": "IN",
            "effective_from": "2024-04-01",
            "version": 1,
            "rates": {"employee_rate": "10"},
            "active": True,
            "notes": "TDS smoke",
        },
        headers=headers,
    )
    check("POST /api/v1/statutory-rule-versions (tds) -> 201", r.status_code == 201, r.text[:120])
    tds_id = r.json().get("id") if r.status_code == 201 else None
    r = c.get("/api/v1/statutory-rule-versions", headers=headers)
    check("GET /api/v1/statutory-rule-versions -> 200 with TDS", r.status_code == 200 and any(v["rule_type"] == "tds" for v in r.json()))
    if tds_id:
        r = c.delete(f"/api/v1/statutory-rule-versions/{tds_id}", headers=headers)
        check("DELETE /api/v1/statutory-rule-versions/{id} -> 200", r.status_code == 200, r.text[:120])

    # Payroll run workflow
    r = c.post(
        "/api/v1/payroll-runs",
        json={
            "name": "PAY-SMOKE-2026-11",
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "pay_date": "2026-11-30",
            "frequency": "monthly",
            "currency": "INR",
        },
        headers=headers,
    )
    check("POST /api/v1/payroll-runs -> 201", r.status_code == 201, r.text[:120])
    run_id = r.json()["id"]

    r = c.post(f"/api/v1/payroll-runs/{run_id}/preview", headers=headers)
    check("POST preview -> 200", r.status_code == 200, r.text[:120])
    if r.status_code == 200:
        preview = r.json()
        check("preview returns total_gross/total_net", "total_gross" in preview and "total_net" in preview)

    r = c.post(f"/api/v1/payroll-runs/{run_id}/process", headers=headers)
    check("POST process -> 200", r.status_code == 200, r.text[:120])

    r = c.post(f"/api/v1/payroll-runs/{run_id}/freeze", headers=headers)
    check("POST freeze -> 200", r.status_code == 200)
    r = c.post(f"/api/v1/payroll-runs/{run_id}/review", headers=headers)
    check("frozen run rejects review -> 422", r.status_code == 422, str(r.status_code))
    r = c.post(f"/api/v1/payroll-runs/{run_id}/unfreeze", headers=headers)
    check("POST unfreeze -> 200", r.status_code == 200)

    r = c.post(f"/api/v1/payroll-runs/{run_id}/review", headers=headers)
    check("POST review -> 200 (status review)", r.status_code == 200 and r.json().get("status") == "review", r.text[:120])
    r = c.post(f"/api/v1/payroll-runs/{run_id}/approve", headers=headers)
    check("POST approve -> 200 (status approved)", r.status_code == 200 and r.json().get("status") == "approved", r.text[:120])
    r = c.post(f"/api/v1/payroll-runs/{run_id}/finalize", headers=headers)
    check("POST finalize -> 200 (status finalized)", r.status_code == 200 and r.json().get("status") == "finalized", r.text[:120])

    r = c.get(f"/api/v1/payroll-runs/{run_id}/variance", headers=headers)
    check("GET variance -> 200", r.status_code == 200, r.text[:120])

    r = c.get(f"/api/v1/payroll-runs/{run_id}/bank-export", headers=headers)
    check("GET bank-export -> 200 CSV", r.status_code == 200 and "text/csv" in r.headers.get("content-type", ""), r.text[:120])

    r = c.get(f"/api/v1/payroll-runs/{run_id}/accounting-export", headers=headers)
    ok = r.status_code == 200 and r.json().get("balanced") is True
    check("GET accounting-export -> 200 balanced", ok, r.text[:160])

    r = c.post(f"/api/v1/payroll-runs/{run_id}/mark-paid", headers=headers)
    check("POST mark-paid -> 200 (status paid)", r.status_code == 200 and r.json().get("status") == "paid", r.text[:120])

    r = c.get("/api/v1/payroll-runs", headers=headers)
    check("GET /api/v1/payroll-runs includes frozen/finalized_at fields", r.status_code == 200 and all("frozen" in run for run in r.json()))

    if failures:
        print(f"\nPayroll smoke checks FAILED: {', '.join(failures)}")
        return 1
    print("\nAll payroll workflow smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
