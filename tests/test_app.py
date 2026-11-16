import os, tempfile
from fastapi.testclient import TestClient

fd, path = tempfile.mkstemp(suffix='.db')
os.close(fd)
os.environ['PEAWORK_DATABASE'] = path
os.environ['PEAWORK_SECRET'] = 'test-secret'

from app.main import app, q, seed
from app.db import init_db


def client():
    init_db()
    if not q('SELECT id FROM users LIMIT 1', one=True):
        seed()
    return TestClient(app)


def login(c):
    return c.post('/login', data={'username':'admin','password':'ChangeMe123!'}, follow_redirects=False)


def authenticated_client():
    c=client(); r=login(c); assert r.status_code==303; c.cookies.update(r.cookies); return c


def test_health():
    with client() as c:
        assert c.get('/health').json()=={'status':'ok','version':'0.2.0','deployment':'local'}


def test_auth_and_dashboard():
    with authenticated_client() as c:
        assert c.get('/').status_code==200
        assert c.get('/recruitment').status_code==200
        assert c.get('/onboarding').status_code==200


def test_create_employee_and_api():
    with authenticated_client() as c:
        r=c.post('/employees/new',data={'employee_no':'T900','first_name':'Test','last_name':'User','work_email':'test@example.com','date_of_joining':'2026-01-01','employment_type':'Full-time','location':'Gurugram'},follow_redirects=False)
        assert r.status_code==303
        assert any(x['employee_no']=='T900' for x in c.get('/api/v1/employees').json())


def test_leave_workflow():
    with authenticated_client() as c:
        r=c.post('/leave/request',data={'employee_id':1,'leave_type_id':1,'start_date':'2026-12-01','end_date':'2026-12-01','days':'1','reason':'Personal'},follow_redirects=False)
        assert r.status_code==303
        lid=q('SELECT MAX(id) id FROM leave_requests',one=True)['id']
        assert c.post(f'/leave/{lid}/decision',data={'decision':'approve','comment':'OK'},follow_redirects=False).status_code==303


def test_complete_hire_to_onboard_workflow():
    with authenticated_client() as c:
        assert c.post('/recruitment/requisitions',data={'title':'Product Designer','openings':'1','employment_type':'Full-time','location':'Gurugram','target_date':'2027-01-31','description':'Design product experiences'},follow_redirects=False).status_code==303
        rid=q('SELECT MAX(id) id FROM requisitions',one=True)['id']
        assert c.post('/recruitment/candidates',data={'first_name':'Mira','last_name':'Kapoor','email':'mira@example.com','phone':'9999999999','current_company':'Studio','experience_years':'5','source':'Referral'},follow_redirects=False).status_code==303
        cid=q('SELECT MAX(id) id FROM candidates',one=True)['id']
        assert c.post('/recruitment/applications',data={'candidate_id':cid,'requisition_id':rid,'notes':'Strong portfolio'},follow_redirects=False).status_code==303
        aid=q('SELECT MAX(id) id FROM applications',one=True)['id']
        assert c.post('/recruitment/interviews',data={'application_id':aid,'interview_type':'Panel','scheduled_at':'2026-12-10T10:00'},follow_redirects=False).status_code==303
        iid=q('SELECT MAX(id) id FROM interviews',one=True)['id']
        assert c.post(f'/recruitment/interviews/{iid}/feedback',data={'rating':'5','scorecard':'Craft, systems thinking','feedback':'Strong evidence','recommendation':'Offer'},follow_redirects=False).status_code==303
        assert c.post('/recruitment/offers',data={'application_id':aid,'offered_title':'Product Designer','offered_salary':'1800000','currency':'INR','joining_date':'2027-02-01','notes':'Approved'},follow_redirects=False).status_code==303
        oid=q('SELECT MAX(id) id FROM offers',one=True)['id']
        assert c.post(f'/recruitment/offers/{oid}/status',data={'status':'Accepted'},follow_redirects=False).status_code==303
        journey=q('SELECT id FROM onboarding_journeys ORDER BY id LIMIT 1',one=True)['id']
        assert c.post('/onboarding/start',data={'offer_id':oid,'journey_id':journey,'probation_days':'90'},follow_redirects=False).status_code==303
        nid=q('SELECT MAX(id) id FROM onboardings',one=True)['id']
        assert q('SELECT COUNT(*) n FROM onboarding_tasks WHERE onboarding_id=?',(nid,),True)['n']>=1
        assert c.post(f'/onboarding/{nid}/convert',data={'employee_no':'PW9000','location':'Gurugram'},follow_redirects=False).status_code==303
        assert any(x['employee_no']=='PW9000' for x in c.get('/api/v1/employees').json())
        assert q('SELECT stage FROM applications WHERE id=?',(aid,),True)['stage']=='Hired'
        assert q('SELECT status FROM requisitions WHERE id=?',(rid,),True)['status']=='Closed'


def test_v02_apis_require_authentication():
    with client() as c:
        for path in ['/api/v1/requisitions','/api/v1/candidates','/api/v1/applications','/api/v1/onboardings']:
            assert c.get(path,follow_redirects=False).status_code in (303,307)
