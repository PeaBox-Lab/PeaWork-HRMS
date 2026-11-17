"""End-to-end smoke test for document acknowledgements via the v2 web UI.

Run against a live server:
    python -m uvicorn peawork.main:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import re
import sys

import httpx

BASE = "http://127.0.0.1:8090"
failures: list[str] = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def csrf(text):
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', text)
    return m.group(1) if m else None


def new_client() -> httpx.Client:
    return httpx.Client(base_url=BASE, follow_redirects=False, timeout=30)


def login(client, email, password):
    r = client.get("/login")
    check(f"GET /login ({email})", r.status_code in (200, 303), str(r.status_code))
    if r.status_code != 200:
        return False
    r = client.post(
        "/login",
        data={"_csrf": csrf(r.text), "email": email, "password": password, "next": "/"},
    )
    ok = r.status_code == 303 and "error" not in r.headers.get("location", "")
    check(f"POST /login ({email})", ok, r.headers.get("location", ""))
    return ok


def row_for(html, title):
    """Return the <tr> fragment containing a document title."""
    segments = html.split("<tr>")
    for segment in segments:
        if title in segment:
            return segment.split("</tr>")[0]
    return ""


def doc_id_in(row):
    m = re.search(r'/documents/([0-9a-f-]+)/download', row)
    return m.group(1) if m else None


def employee_id_for(client, employee_no):
    """Find the employee uuid for an SMP- employee number via the people directory."""
    r = client.get("/employees")
    check(f"GET /employees looking for {employee_no}", r.status_code == 200, str(r.status_code))
    rows = re.findall(r"<tr[^>]*>.*?</tr>", r.text, re.DOTALL)
    for row in rows:
        if employee_no in row:
            m = re.search(r'href="/employees/([0-9a-f-]+)"', row)
            if m:
                return m.group(1)
    return None


# --- 1: employee logs in and uploads a document for themself -----------------
emp_client = new_client()
assert login(emp_client, "employee@example.com", "EmployeeDemo123!")

emp_id = employee_id_for(emp_client, "SMP-0002")
check("Found employee id for SMP-0002", bool(emp_id), str(emp_id))

with open("data/smoke_document.txt", "w") as fh:
    fh.write("Employee handbook excerpt — acknowledgment smoke test.\n")

csrf_tok = csrf(emp_client.get("/documents").text)
handle = open("data/smoke_document.txt", "rb")
r = emp_client.post(
    f"/employees/{emp_id}/documents",
    data={"_csrf": csrf_tok, "title": "Employee Handbook", "category": "other"},
    files={"file": ("handbook.txt", handle, "text/plain")},
)
handle.close()
check("Employee uploads document", r.status_code == 303, str(r.status_code))

# --- 2: employee sees Pending + Acknowledge button --------------------------
r = emp_client.get("/documents")
check("GET /documents (employee)", r.status_code == 200, str(r.status_code))
row = row_for(r.text, "Employee Handbook")
check("Employee sees own document row", bool(row), "")
check("Pending badge shown", "Pending" in row, "")
check("Acknowledge button present", 'type="submit">Acknowledge<' in row, "")

doc_id = doc_id_in(row)
check("Document id parsed from row", bool(doc_id), str(doc_id))

# --- 3: employee acknowledges ------------------------------------------------
csrf_tok = csrf(emp_client.get("/documents").text)
r = emp_client.post(f"/documents/{doc_id}/acknowledge", data={"_csrf": csrf_tok})
check("POST acknowledge redirects", r.status_code == 303, str(r.status_code))

r = emp_client.get("/documents")
row = row_for(r.text, "Employee Handbook")
check('Yes badge shown after acknowledge', "Yes ·" in row, "")
check("Acknowledge button gone from row", 'type="submit">Acknowledge<' not in row, "")

# --- 4: duplicate acknowledge rejected ---------------------------------------
csrf_tok = csrf(emp_client.get("/documents").text)
r = emp_client.post(f"/documents/{doc_id}/acknowledge", data={"_csrf": csrf_tok})
check(
    "Duplicate acknowledge rejected (error redirect)",
    r.status_code == 303 and "acknowledg" in r.headers.get("location", "").lower(),
    r.headers.get("location", ""),
)

# --- 5: manager can view but NOT acknowledge ---------------------------------
mgr_client = new_client()
assert login(mgr_client, "manager@example.com", "ManagerDemo123!")
r = mgr_client.get("/documents")
check("GET /documents (manager)", r.status_code == 200, str(r.status_code))
row = row_for(r.text, "Employee Handbook")
check("Manager sees report's document row", bool(row), "")
check("Manager has no Acknowledge button", 'type="submit">Acknowledge<' not in row, "")

# --- 6: admin acknowledges on behalf -----------------------------------------
admin_client = new_client()
assert login(admin_client, "admin@example.com", "AdminV2Pass123!")

admin_emp_id = employee_id_for(admin_client, "SMP-0002")
check("Admin found employee id for SMP-0002", bool(admin_emp_id), str(admin_emp_id))

csrf_tok = csrf(admin_client.get("/documents").text)
r = admin_client.post(
    f"/employees/{admin_emp_id}/documents",
    data={"_csrf": csrf_tok, "title": "Confidentiality Policy", "category": "other"},
    files={
        "file": (
            "policy.pdf",
            (b"%PDF-1.4 fake policy document"),
            "application/pdf",
        )
    },
)
check("Admin uploads document for employee", r.status_code == 303, str(r.status_code))

r = admin_client.get("/documents")
row = row_for(r.text, "Confidentiality Policy")
check("Admin sees uploaded document row", bool(row), "")
check(
    '"Acknowledge for employee" button present',
    'type="submit">Acknowledge for employee<' in row,
    "",
)

on_behalf_id = doc_id_in(row)
csrf_tok = csrf(admin_client.get("/documents").text)
r = admin_client.post(f"/documents/{on_behalf_id}/acknowledge", data={"_csrf": csrf_tok})
check("Admin on-behalf acknowledge redirects", r.status_code == 303, str(r.status_code))

r = admin_client.get("/documents")
row = row_for(r.text, "Confidentiality Policy")
check('Badge shows "Yes" after on-behalf acknowledge', "Yes ·" in row, "")
check("Acknowledge button gone from row", 'type="submit">Acknowledge' not in row, "")

# --- 7: audit trail -----------------------------------------------------------
r = admin_client.get("/admin/audit?limit=50")
check("GET /admin/audit 200", r.status_code == 200, str(r.status_code))
check(
    "Document acknowledgement audit event recorded",
    "document_acknowledgement" in r.text,
    "",
)

print()
if failures:
    print(f"FAILURES: {failures}")
    sys.exit(1)
print("All document acknowledgement smoke checks passed.")
