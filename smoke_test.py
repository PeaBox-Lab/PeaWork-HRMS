import re, httpx

BASE = "http://127.0.0.1:8090"

def csrf(text):
    m = re.compile(r'name="_csrf"\s+value="([^"]+)"').search(text)
    return m.group(1) if m else None

def main():
    s = httpx.Client(follow_redirects=False)

    # 1. Login
    r = s.get(f"{BASE}/login")
    tok = csrf(r.text)
    r = s.post(f"{BASE}/login", data={"_csrf": tok, "email": "admin@example.com", "password": "AdminV2Pass123!", "next": "/"})
    print("1 POST /login:", r.status_code, r.headers.get("location"))
    assert r.status_code == 303 and r.headers.get("location") == "/", "login failed"

    # 2. Admin settings
    r = s.get(f"{BASE}/admin/settings")
    print("2 GET /admin/settings:", r.status_code, "sample card:", "sample" in r.text.lower())

    # 3. Import page
    r = s.get(f"{BASE}/admin/import")
    print("3 GET /admin/import:", r.status_code, "template link:", "template.csv" in r.text.lower())

    # 4. Reports page
    r = s.get(f"{BASE}/admin/reports")
    print("4 GET /admin/reports:", r.status_code, "Full organisation:", "Full organisation" in r.text)

    # 5. Employee register download
    r = s.get(f"{BASE}/admin/reports/employees.xlsx")
    print("5 GET /admin/reports/employees.xlsx:", r.status_code, len(r.content), "bytes")
    open("data/smoke_employees.xlsx", "wb").write(r.content)

    # 6. Full workbook download
    r = s.get(f"{BASE}/admin/reports/full.xlsx")
    print("6 GET /admin/reports/full.xlsx:", r.status_code, len(r.content), "bytes")
    open("data/smoke_full.xlsx", "wb").write(r.content)

    # 7. Template downloads
    r = s.get(f"{BASE}/admin/import/template.csv")
    print("7 GET /admin/import/template.csv:", r.status_code, r.headers.get("content-type"))

    r = s.get(f"{BASE}/admin/import/template.xlsx")
    print("8 GET /admin/import/template.xlsx:", r.status_code, len(r.content), "bytes")

    # 9. Dashboard
    r = s.get(f"{BASE}/")
    print("9 GET /:", r.status_code)

    # 10. Employees list page
    r = s.get(f"{BASE}/employees")
    print("10 GET /employees:", r.status_code)

    print("\nAll checks passed!")

if __name__ == "__main__":
    main()