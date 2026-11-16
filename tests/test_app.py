import os,tempfile
from fastapi.testclient import TestClient
fd,path=tempfile.mkstemp(suffix='.db');os.close(fd);os.environ['PEAWORK_DATABASE']=path;os.environ['PEAWORK_SECRET']='test-secret'
from app.main import app
from app.db import init_db
from app.main import seed

def client():
    init_db()
    from app.main import q
    if not q('SELECT id FROM users LIMIT 1',one=True): seed()
    return TestClient(app)
def login(c): return c.post('/login',data={'username':'admin','password':'ChangeMe123!'},follow_redirects=False)
def test_health():
    with client() as c: assert c.get('/health').json()['status']=='ok'
def test_auth_and_dashboard():
    with client() as c:
        r=login(c);assert r.status_code==303
        c.cookies.update(r.cookies);assert c.get('/').status_code==200

def test_create_employee_and_api():
    with client() as c:
        r=login(c);c.cookies.update(r.cookies)
        r=c.post('/employees/new',data={'employee_no':'T900','first_name':'Test','last_name':'User','work_email':'test@example.com','date_of_joining':'2026-01-01','employment_type':'Full-time','location':'Gurugram'},follow_redirects=False)
        assert r.status_code==303
        assert any(x['employee_no']=='T900' for x in c.get('/api/v1/employees').json())
def test_leave_workflow():
    with client() as c:
        r=login(c);c.cookies.update(r.cookies)
        r=c.post('/leave/request',data={'employee_id':1,'leave_type_id':1,'start_date':'2026-12-01','end_date':'2026-12-01','days':'1','reason':'Personal'},follow_redirects=False);assert r.status_code==303
        assert c.post('/leave/1/decision',data={'decision':'approve','comment':'OK'},follow_redirects=False).status_code==303
