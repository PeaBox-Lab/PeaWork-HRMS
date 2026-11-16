import os, tempfile  
from fastapi.testclient import TestClient  
fd, path = tempfile.mkstemp(suffix='.db')  
os.close(fd)  
os.environ['PEAWORK_DATABASE'] = path  
os.environ['PEAWORK_SECRET'] = 'test-secret'
control_fd, control_path = tempfile.mkstemp(suffix='.db'); os.close(control_fd)
os.environ['PEAWORK_CONTROL_DATABASE'] = control_path
os.environ['PEAWORK_TENANT_DATABASE_ROOT'] = tempfile.mkdtemp(prefix='peawork-tenants-')  
from app.main import app, q, seed
from app.db import init_db
from app.cloud_control import init_control_db
class CSRFClient(TestClient):
    def post(self,url,*args,**kwargs):
        if not self.cookies.get('peawork_csrf'):self.get('/login')
        data=dict(kwargs.pop('data',{}) or {});data.setdefault('_csrf',self.cookies.get('peawork_csrf'));kwargs['data']=data
        return super().post(url,*args,**kwargs)
def client():
    init_control_db();init_db()
    if not q('SELECT id FROM users LIMIT 1', one=True):seed()
    return CSRFClient(app)
def login(c):
    return c.post('/login', data={'username':'admin','password':'ChangeMe123!'}, follow_redirects=False)  
def authenticated_client():  
    c=client(); r=login(c); assert r.status_code==303; c.cookies.update(r.cookies); return c  
def test_health():  
    with client() as c:  
        data=c.get('/health').json();assert data['status']=='ok' and data['version']=='1.0.0' and data['deployment']=='local'  
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
def test_workforce_attendance_timesheet_and_overtime():  
    with authenticated_client() as c:  
        shift=q('SELECT id FROM shifts ORDER BY id LIMIT 1',one=True)['id']  
        assert c.post('/workforce/attendance',data={'employee_id':'1','attendance_date':'2026-11-16','shift_id':shift,'check_in':'2026-11-16T09:30','check_out':'2026-11-16T19:00','status':'Present','notes':'Office'},follow_redirects=False).status_code==303  
        attendance=q('SELECT * FROM attendance_records WHERE employee_id=1 AND attendance_date=?',('2026-11-16',),True)  
        assert attendance['worked_hours']==8.5 and attendance['overtime_hours']==0.5  
        assert c.post('/workforce/overtime',data={'employee_id':'1','attendance_date':'2026-11-16','requested_hours':'0.5','reason':'Release'},follow_redirects=False).status_code==303  
        oid=q('SELECT MAX(id) id FROM overtime_requests',one=True)['id']  
        assert c.post(f'/workforce/overtime/{oid}/decision',data={'decision':'approve','comment':'Approved'},follow_redirects=False).status_code==303  
        assert c.post('/workforce/timesheets',data={'employee_id':'1','week_start':'2026-11-16'},follow_redirects=False).status_code==303  
        tid=q('SELECT MAX(id) id FROM timesheets',one=True)['id']  
        assert c.post(f'/workforce/timesheets/{tid}/entries',data={'work_date':'2026-11-16','project_code':'PW','activity':'Workforce release','hours':'8','notes':'Build'},follow_redirects=False).status_code==303  
        assert c.post(f'/workforce/timesheets/{tid}/submit',follow_redirects=False).status_code==303  
        assert c.post(f'/workforce/timesheets/{tid}/decision',data={'decision':'approve','comment':'Complete'},follow_redirects=False).status_code==303  
        assert q('SELECT status FROM timesheets WHERE id=?',(tid,),True)['status']=='Approved'  
def test_biometric_api_and_leave_accrual():  
    with authenticated_client() as c:  
        r=c.post('/api/v1/biometric-punch',data={'employee_no':'PW0001','attendance_date':'2026-11-17','check_in':'09:30','check_out':'18:30','device_code':'TEST'})  
        assert r.status_code==200 and r.json()['worked_hours']==9.0  
        assert c.post('/workforce/leave-accruals/run',data={'period':'2026-11','leave_type_id':'1','amount_per_employee':'1'},follow_redirects=False).status_code==303  
        run=q('SELECT * FROM leave_accrual_runs WHERE period=? AND leave_type_id=1',('2026-11',),True)  
        assert run['employees_processed']>=2  
        assert c.post('/workforce/leave-accruals/run',data={'period':'2026-11','leave_type_id':'1','amount_per_employee':'1'},follow_redirects=False).status_code==400  
def test_workforce_apis_require_authentication():  
    with client() as c:  
        for path in ['/api/v1/attendance','/api/v1/shifts','/api/v1/timesheets']:  
            assert c.get(path,follow_redirects=False).status_code in (303,307)



def test_complete_talent_cycle_workflow():
    with authenticated_client() as c:
        assert c.post('/talent/cycles',data={'name':'FY27 Performance','cycle_type':'Annual','start_date':'2027-01-01','end_date':'2027-12-31','goal_due_date':'2027-01-31','self_review_due_date':'2027-11-30','manager_review_due_date':'2027-12-15','description':'Annual cycle'},follow_redirects=False).status_code==303
        cycle=q('SELECT id FROM performance_cycles WHERE name=?',('FY27 Performance',),True)['id']
        assert c.post(f'/talent/cycles/{cycle}/status',data={'status':'Active'},follow_redirects=False).status_code==303
        assert c.post(f'/talent/cycles/{cycle}/participants',data={'employee_id':'2','manager_employee_id':'1'},follow_redirects=False).status_code==303
        participant=q('SELECT id FROM cycle_participants WHERE cycle_id=? AND employee_id=2',(cycle,),True)['id']
        assert c.post('/talent/goals',data={'cycle_id':cycle,'employee_id':'2','title':'Improve delivery predictability','goal_type':'Individual','metric_name':'On-time delivery','baseline':'70','target_value':'90','unit':'percent','weight':'40','start_date':'2027-01-01','due_date':'2027-10-31'},follow_redirects=False).status_code==303
        goal=q('SELECT id FROM goals WHERE cycle_id=? AND employee_id=2',(cycle,),True)['id']
        assert c.post(f'/talent/goals/{goal}/update',data={'progress':'35','current_value':'78','status':'Active','update_text':'Milestone delivered'},follow_redirects=False).status_code==303
        assert c.post('/talent/check-ins',data={'cycle_id':cycle,'employee_id':'2','manager_employee_id':'1','check_in_date':'2027-03-31','accomplishments':'Improved planning','blockers':'Dependency delays','support_needed':'Earlier reviews','next_steps':'Automate metrics','employee_sentiment':'Positive','manager_notes':'Good progress'},follow_redirects=False).status_code==303
        assert c.post('/talent/feedback/requests',data={'cycle_id':cycle,'subject_employee_id':'2','requested_from_employee_id':'1','relationship':'Manager','due_date':'2027-10-31','message':'Provide evidence'},follow_redirects=False).status_code==303
        feedback=q('SELECT id FROM feedback_requests WHERE cycle_id=?',(cycle,),True)['id']
        assert c.post(f'/talent/feedback/{feedback}/respond',data={'strengths':'Reliable execution','improvements':'Delegate earlier','examples':'Delivered release','rating':'4.2','visibility':'Subject and Manager'},follow_redirects=False).status_code==303
        competency=q('SELECT id FROM competencies ORDER BY id LIMIT 1',one=True)['id']
        assert c.post('/talent/competencies/assess',data={'employee_id':'2','competency_id':competency,'self_level':'3','manager_level':'4','evidence':'Project delivery'},follow_redirects=False).status_code==303
        skill=q('SELECT id FROM skills ORDER BY id LIMIT 1',one=True)['id']
        assert c.post('/talent/skills/assess',data={'employee_id':'2','skill_id':skill,'proficiency':'4','interest':'1','years_experience':'3','evidence':'Production work','verify':'1'},follow_redirects=False).status_code==303
        assert c.post('/talent/development-plans',data={'employee_id':'2','cycle_id':cycle,'title':'Technical leadership','career_goal':'Lead a product area','target_date':'2027-12-31','owner_employee_id':'1'},follow_redirects=False).status_code==303
        plan=q('SELECT id FROM development_plans WHERE cycle_id=? AND employee_id=2',(cycle,),True)['id']
        assert c.post(f'/talent/development-plans/{plan}/actions',data={'action_type':'Experience','title':'Lead architecture review','description':'Own review','due_date':'2027-09-30','success_measure':'Approved design'},follow_redirects=False).status_code==303
        action=q('SELECT id FROM development_actions WHERE plan_id=?',(plan,),True)['id']
        assert c.post(f'/talent/development-actions/{action}/status',data={'status':'Completed','progress_notes':'Design approved'},follow_redirects=False).status_code==303
        assert c.post(f'/talent/participants/{participant}/review',data={'review_type':'self','rating':'4','summary':'Met outcomes'},follow_redirects=False).status_code==303
        assert c.post(f'/talent/participants/{participant}/review',data={'review_type':'manager','rating':'4.3','summary':'Strong year'},follow_redirects=False).status_code==303
        assert c.post('/talent/calibration/sessions',data={'cycle_id':cycle,'name':'Engineering calibration','department_id':'2','session_date':'2027-12-18','facilitator_employee_id':'1','notes':'Annual review'},follow_redirects=False).status_code==303
        session=q('SELECT id FROM calibration_sessions WHERE cycle_id=?',(cycle,),True)['id']
        assert c.post(f'/talent/calibration/{session}/entries',data={'participant_id':participant,'proposed_rating':'4.3','calibrated_rating':'4.1','performance_bucket':'High','potential_bucket':'High','rationale':'Consistent evidence','risk_flag':'Retention'},follow_redirects=False).status_code==303
        assert c.post(f'/talent/calibration/{session}/status',data={'status':'Completed'},follow_redirects=False).status_code==303
        assert c.post(f'/talent/participants/{participant}/review',data={'review_type':'final','rating':'4.1','summary':'Calibrated final review'},follow_redirects=False).status_code==303
        final=q('SELECT * FROM cycle_participants WHERE id=?',(participant,),True)
        assert final['status']=='Finalized' and final['final_rating']==4.1
        assert q('SELECT status FROM development_plans WHERE id=?',(plan,),True)['status']=='Completed'


def test_talent_apis_require_authentication():
    with client() as c:
        for path in ['/api/v1/performance-cycles','/api/v1/goals','/api/v1/feedback-requests','/api/v1/skills','/api/v1/development-plans']:
            assert c.get(path,follow_redirects=False).status_code in (303,307)



def test_complete_payroll_beta_workflow():
    with authenticated_client() as c:
        basic=q("SELECT id FROM pay_components WHERE code='BASIC'",one=True)['id']
        hra=q("SELECT id FROM pay_components WHERE code='HRA'",one=True)['id']
        special=q("SELECT id FROM pay_components WHERE code='SPECIAL'",one=True)['id']
        arrear=q("SELECT id FROM pay_components WHERE code='ARREAR'",one=True)['id']
        reimb=q("SELECT id FROM pay_components WHERE code='REIMB'",one=True)['id']
        otherded=q("SELECT id FROM pay_components WHERE code='OTHERDED'",one=True)['id']
        for cid,gl,liab in [(basic,'6000-BASIC',''),(hra,'6001-HRA',''),(special,'6002-SPECIAL',''),(arrear,'6003-ARREAR',''),(reimb,'6004-REIMB',''),(otherded,'','2100-OTHERDED')]:
            assert c.post(f'/payroll/components/{cid}/mapping',data={'gl_account':gl or liab,'liability_account':liab,'statutory_rule_name':''},follow_redirects=False).status_code==303
        from app.main import execq
        execq("INSERT INTO settings(key,value) VALUES('payroll_net_pay_liability_account','2200-NET-PAY') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        assert c.post('/payroll/structures',data={'name':'Monthly CTC Structure','code':'MONTHLY','description':'Test structure'},follow_redirects=False).status_code==303
        sid=q("SELECT id FROM salary_structures WHERE code='MONTHLY'",one=True)['id']
        lines=[(basic,'Percent CTC','',50,''),(hra,'Percent Component','',40,'BASIC'),(special,'Percent CTC','',30,''),(arrear,'Input','','',''),(reimb,'Input','','',''),(otherded,'Input','','','')]
        for cid,calc,amount,rate,base in lines:
            data={'component_id':cid,'calculation_type':calc,'base_component_code':base}
            if amount!='': data['amount']=amount
            if rate!='': data['rate']=rate
            assert c.post(f'/payroll/structures/{sid}/lines',data=data,follow_redirects=False).status_code==303
        assert c.post('/payroll/compensation',data={'employee_id':'1','structure_id':sid,'annual_ctc':'1200000','effective_from':'2027-01-01'},follow_redirects=False).status_code==303
        assert c.post('/payroll/compensation',data={'employee_id':'2','structure_id':sid,'annual_ctc':'600000','effective_from':'2027-01-01'},follow_redirects=False).status_code==303
        assert c.post('/payroll/periods',data={'period_code':'2027-01','start_date':'2027-01-01','end_date':'2027-01-31','pay_date':'2027-01-31','working_days':'22'},follow_redirects=False).status_code==303
        period=q("SELECT id FROM payroll_periods WHERE period_code='2027-01'",one=True)['id']
        assert c.post('/payroll/attendance/sync',data={'period_id':period},follow_redirects=False).status_code==303
        assert c.post('/payroll/inputs',data={'period_id':period,'employee_id':'1','component_id':arrear,'input_type':'Arrear','amount':'5000','description':'Prior correction'},follow_redirects=False).status_code==303
        inp=q('SELECT MAX(id) id FROM payroll_inputs',one=True)['id']; assert c.post(f'/payroll/inputs/{inp}/approve',follow_redirects=False).status_code==303
        assert c.post('/payroll/inputs',data={'period_id':period,'employee_id':'1','component_id':otherded,'input_type':'Recovery','amount':'1000','description':'Advance recovery'},follow_redirects=False).status_code==303
        inp=q('SELECT MAX(id) id FROM payroll_inputs',one=True)['id']; assert c.post(f'/payroll/inputs/{inp}/approve',follow_redirects=False).status_code==303
        assert c.post('/payroll/reimbursements',data={'employee_id':'1','period_id':period,'category':'Travel','amount':'2000','description':'Client visit'},follow_redirects=False).status_code==303
        claim=q('SELECT MAX(id) id FROM reimbursement_claims',one=True)['id']; assert c.post(f'/payroll/reimbursements/{claim}/decision',data={'decision':'approve'},follow_redirects=False).status_code==303
        assert c.post('/payroll/runs',data={'period_id':period},follow_redirects=False).status_code==303
        run=q('SELECT MAX(id) id FROM payroll_runs',one=True)['id']
        summary=q('SELECT * FROM payroll_runs WHERE id=?',(run,),True)
        assert summary['employee_count']==2 and summary['gross_pay']>summary['net_pay']
        assert c.post(f'/payroll/runs/{run}/finalize',data={'variance_threshold':'100'},follow_redirects=False).status_code==303
        assert q('SELECT status FROM payroll_runs WHERE id=?',(run,),True)['status']=='Finalized'
        assert q('SELECT COUNT(*) n FROM payslips',one=True)['n']==2
        export=c.get(f'/payroll/runs/{run}/accounting.csv')
        assert export.status_code==200 and 'Net pay liability' in export.text
        check=q('SELECT * FROM payroll_accounting_exports WHERE run_id=?',(run,),True)
        assert check['balanced']==1 and check['total_debits']==check['total_credits']


def test_unvalidated_statutory_rule_blocks_finalization():
    with authenticated_client() as c:
        assert c.post('/payroll/components',data={'code':'TESTSTAT','name':'Test Statutory','component_type':'Deduction','calculation_type':'Percent Component','base_component_code':'BASIC','default_rate':'1','statutory':'1','statutory_rule_name':'TEST RULE','liability_account':'2100-TEST','display_order':'99'},follow_redirects=False).status_code==303
        stat=q("SELECT id FROM pay_components WHERE code='TESTSTAT'",one=True)['id']
        sid=q("SELECT id FROM salary_structures WHERE code='MONTHLY'",one=True)['id']
        assert c.post(f'/payroll/structures/{sid}/lines',data={'component_id':stat,'calculation_type':'Percent Component','rate':'1','base_component_code':'BASIC'},follow_redirects=False).status_code==303
        assert c.post('/payroll/periods',data={'period_code':'2027-02','start_date':'2027-02-01','end_date':'2027-02-28','pay_date':'2027-02-28','working_days':'20'},follow_redirects=False).status_code==303
        period=q("SELECT id FROM payroll_periods WHERE period_code='2027-02'",one=True)['id']
        assert c.post('/payroll/attendance/sync',data={'period_id':period},follow_redirects=False).status_code==303
        assert c.post('/payroll/runs',data={'period_id':period},follow_redirects=False).status_code==303
        run=q('SELECT MAX(id) id FROM payroll_runs',one=True)['id']
        assert 'lacks a validated effective rule' in q('SELECT validation_errors FROM payroll_runs WHERE id=?',(run,),True)['validation_errors']
        assert c.post(f'/payroll/runs/{run}/finalize',data={'variance_threshold':'100'},follow_redirects=False).status_code==400


def test_payroll_apis_require_authentication():
    with client() as c:
        for path in ['/api/v1/payroll-periods','/api/v1/payroll-runs','/api/v1/payslips']:
            assert c.get(path,follow_redirects=False).status_code in (303,307)



def test_cloud_tenant_isolation_and_controls():
    from app.cloud_control import init_control_db,control_conn,issue_scim_token
    from app.db import set_tenant,reset_tenant
    from app.main import tenant_employee_limit
    init_control_db()
    with control_conn() as db:
        plan=db.execute('SELECT id FROM subscription_plans LIMIT 1').fetchone()['id']
        alpha=db.execute("INSERT INTO tenants(slug,name) VALUES('alpha-test','Alpha Test')").lastrowid
        beta=db.execute("INSERT INTO tenants(slug,name) VALUES('beta-test','Beta Test')").lastrowid
        for tid,slug in [(alpha,'alpha-test'),(beta,'beta-test')]:
            db.execute("INSERT INTO tenant_subscriptions(tenant_id,plan_id,status,starts_on,employee_limit_override) VALUES(?,?,'Active','2026-11-16',3)",(tid,plan))
            db.execute("INSERT INTO storage_configurations(tenant_id,backend,prefix,status) VALUES(?,'Local',?,'Active')",(tid,slug))
    for slug in ['alpha-test','beta-test']:
        token=set_tenant(slug)
        try:
            init_db()
            if not q('SELECT id FROM users LIMIT 1',one=True):seed()
        finally:reset_tenant(token)
    token=set_tenant('alpha-test')
    try:
        from app.main import execq
        execq("INSERT INTO employees(employee_no,first_name,last_name,status) VALUES('A100','Alpha','Only','Active')")
        assert q("SELECT COUNT(*) n FROM employees WHERE employee_no='A100'",one=True)['n']==1
        assert tenant_employee_limit()==3
    finally:reset_tenant(token)
    token=set_tenant('beta-test')
    try:assert q("SELECT COUNT(*) n FROM employees WHERE employee_no='A100'",one=True)['n']==0
    finally:reset_tenant(token)


def test_tenant_bound_session_and_scim_provisioning():
    import asyncio
    from app.cloud_control import control_conn,issue_scim_token
    from app.db import set_tenant,reset_tenant
    from app.main import me,scim_user_create
    from app.security import token
    class SessionRequest: cookies={'peawork_session':token(1,'admin','alpha-test')}
    context=set_tenant('beta-test')
    try:assert me(SessionRequest()) is None
    finally:reset_tenant(context)
    plain,digest,prefix=issue_scim_token()
    with control_conn() as db:
        tenant=db.execute("SELECT id FROM tenants WHERE slug='alpha-test'").fetchone()
        db.execute("INSERT INTO scim_credentials(tenant_id,name,token_hash,token_prefix) VALUES(?,?,?,?)",(tenant['id'],'Automated Test',digest,prefix))
    class ScimRequest:
        headers={'authorization':'Bearer '+plain}
        async def json(self):return {'userName':'scim.test','role':'manager','active':True}
    result=asyncio.run(scim_user_create(ScimRequest()))
    assert result['userName']=='scim.test'
    context=set_tenant('alpha-test')
    try:assert q("SELECT role FROM users WHERE username='scim.test'",one=True)['role']=='manager'
    finally:reset_tenant(context)
    context=set_tenant('beta-test')
    try:assert q("SELECT id FROM users WHERE username='scim.test'",one=True) is None
    finally:reset_tenant(context)


def test_cloud_apis_require_control_admin():
    with client() as c:
        for endpoint in ['/api/v1/cloud/tenants','/api/v1/cloud/service-checks']:
            assert c.get(endpoint,follow_redirects=False).status_code in (303,307)


def test_security_headers_csrf_and_session_revocation():
    from app.cloud_control import control_conn
    with client() as c:
        landing=c.get('/login');assert landing.status_code==200 and c.cookies.get('peawork_csrf')
        rejected=TestClient(app).post('/login',data={'username':'admin','password':'ChangeMe123!'},follow_redirects=False)
        assert rejected.status_code==403
        r=login(c);c.cookies.update(r.cookies);assert r.status_code==303
        page=c.get('/');assert page.status_code==200
        assert page.headers['x-frame-options']=='DENY' and page.headers['x-content-type-options']=='nosniff' and 'frame-ancestors' in page.headers['content-security-policy']
        raw=c.cookies.get('peawork_session')
        assert c.post('/logout',follow_redirects=False).status_code==303
        with control_conn() as db:assert db.execute("SELECT COUNT(*) n FROM session_registry WHERE revoked_at IS NOT NULL").fetchone()['n']>=1
        c.cookies.set('peawork_session',raw);assert c.get('/',follow_redirects=False).status_code in (303,307)


def test_login_throttling_and_upload_validation():
    from app.main import record_login_attempt,login_throttled
    for _ in range(5):record_login_attempt('blocked-user','127.0.0.9',False)
    assert login_throttled('blocked-user','127.0.0.9')
    import io
    from app.storage import validated_stream
    try:validated_stream(io.BytesIO(b'bad'),'malware.exe','application/octet-stream');raise AssertionError('extension accepted')
    except ValueError:pass
    stream,size=validated_stream(io.BytesIO(b'clean'),'evidence.txt','text/plain');assert size==5;stream.close()


def test_privacy_and_readiness_evidence_gates():
    from app.cloud_control import control_conn,init_control_db
    init_control_db()
    with control_conn() as db:
        tenant=db.execute("SELECT id FROM tenants WHERE slug='local'").fetchone()['id']
        db.execute("INSERT INTO retention_policies(tenant_id,data_category,retention_days,status,approved_by) VALUES(?,'Notifications',30,'Approved','Privacy Officer') ON CONFLICT(tenant_id,data_category) DO UPDATE SET status='Approved',approved_by='Privacy Officer'",(tenant,))
        request_id=db.execute("INSERT INTO privacy_requests(tenant_id,request_type,data_subject,received_on) VALUES(?,'Access','subject-1','2026-11-16')",(tenant,)).lastrowid
        gate=db.execute("SELECT id FROM readiness_evidence WHERE control_code='SEC-PENTEST'").fetchone()['id']
    with authenticated_client() as c:
        assert c.post(f'/privacy/requests/{request_id}/update',data={'identity_status':'Pending','status':'Completed','decision':'Provide export','evidence':'case-1'},follow_redirects=False).status_code==400
        assert c.post(f'/privacy/requests/{request_id}/update',data={'identity_status':'Verified','status':'Completed','decision':'Provide export','evidence':'case-1'},follow_redirects=False).status_code==303
        assert c.post(f'/readiness/evidence/{gate}',data={'status':'Verified','evidence_reference':'','reviewed_by':'','notes':''},follow_redirects=False).status_code==400
        assert c.post(f'/readiness/evidence/{gate}',data={'status':'Verified','evidence_reference':'PEN-2026-01','reviewed_by':'Independent Tester','notes':'Scope attached'},follow_redirects=False).status_code==303


def test_password_hash_upgrade_and_locale():
    from app.security import hash_password,verify_password
    value=hash_password('StrongPassword!');assert value.startswith('pbkdf2_sha256$600000$') and verify_password('StrongPassword!',value)
    with authenticated_client() as c:
        assert c.post('/locale',data={'locale':'hi'},follow_redirects=False).status_code==303
        assert c.cookies.get('peawork_locale')=='hi'


def test_global_core_hr_transfer_salary_and_skill_gap():
    with authenticated_client() as c:
        assert c.post('/core-hr/legal-entities',data={'code':'PW-US','name':'PeaWork USA','country_code':'US','registration_no':'REG-US','tax_id_masked':'***1234','currency_code':'USD','timezone':'America/New_York'},follow_redirects=False).status_code==303
        entity=q("SELECT id FROM legal_entities WHERE code='PW-US'",one=True)['id']
        assert c.post('/core-hr/locations',data={'code':'NYC','name':'New York Office','legal_entity_id':entity,'country_code':'US','state_region':'NY','city':'New York','timezone':'America/New_York','address':'Test'},follow_redirects=False).status_code==303
        location=q("SELECT id FROM work_locations WHERE code='NYC'",one=True)['id']
        assert c.post('/core-hr/cost-centers',data={'code':'CC-US-ENG','name':'US Engineering','legal_entity_id':entity},follow_redirects=False).status_code==303
        cc=q("SELECT id FROM cost_centers WHERE code='CC-US-ENG'",one=True)['id']
        assert c.post('/core-hr/projects',data={'code':'P-US-1','name':'Global Platform','legal_entity_id':entity,'cost_center_id':cc,'manager_employee_id':'1','start_date':'2027-01-01','client_name':'Internal'},follow_redirects=False).status_code==303
        project=q("SELECT id FROM projects WHERE code='P-US-1'",one=True)['id']
        assert c.post('/core-hr/job-families',data={'code':'ENG','name':'Engineering','description':'Engineering jobs'},follow_redirects=False).status_code==303
        family=q("SELECT id FROM job_families WHERE code='ENG'",one=True)['id']
        assert c.post('/core-hr/job-profiles',data={'code':'ENG-SENIOR','title':'Senior Engineer','family_id':family,'level_code':'P3','grade':'G7','description':'Senior role','responsibilities':'Delivery'},follow_redirects=False).status_code==303
        job=q("SELECT id FROM job_profiles WHERE code='ENG-SENIOR'",one=True)['id'];skill=q("SELECT id FROM skills WHERE name='Python'",one=True)['id']
        assert c.post(f'/core-hr/job-profiles/{job}/skills',data={'skill_id':skill,'required_proficiency':'5','critical':'1'},follow_redirects=False).status_code==303
        source=q("SELECT id FROM employee_assignments WHERE employee_id=2 AND assignment_status='Active'",one=True)['id']
        assert c.post('/core-hr/transfers',data={'employee_id':'2','effective_date':'2027-04-01','target_legal_entity_id':entity,'target_location_id':location,'target_cost_center_id':cc,'target_project_id':project,'target_job_profile_id':job,'target_manager_id':'1','reason':'Global mobility'},follow_redirects=False).status_code==303
        transfer=q('SELECT MAX(id) id FROM employee_transfers',one=True)['id'];assert c.post(f'/core-hr/transfers/{transfer}/decision',data={'decision':'approve'},follow_redirects=False).status_code==303
        old=q('SELECT * FROM employee_assignments WHERE id=?',(source,),True);new=q("SELECT * FROM employee_assignments WHERE employee_id=2 AND effective_from='2027-04-01'",one=True)
        assert old['effective_to']=='2027-03-31' and old['assignment_status']=='Transferred' and new['legal_entity_id']==entity and new['job_profile_id']==job
        assert c.post('/core-hr/salary',data={'employee_id':'2','currency_code':'USD','annual_base':'120000','annual_variable_target':'12000','effective_from':'2027-04-01','reason':'US transfer'},follow_redirects=False).status_code==303
        salary=q('SELECT MAX(id) id FROM employee_salary_history',one=True)['id'];assert c.post(f'/core-hr/salary/{salary}/decision',data={'decision':'approve'},follow_redirects=False).status_code==303
        assert q('SELECT approval_status FROM employee_salary_history WHERE id=?',(salary,),True)['approval_status']=='Approved'
        assert any(x['skill']=='Python' and x['employee_id']==2 for x in c.get('/core-hr').context['skill_gaps'])


def test_journey_and_offboarding_controls():
    with authenticated_client() as c:
        journey=q("SELECT id FROM journey_definitions WHERE journey_type='Onboarding' ORDER BY id LIMIT 1",one=True)['id']
        assert c.post('/lifecycle/journeys/start',data={'employee_id':'2','journey_id':journey,'start_date':'2027-01-15','target_date':'2027-04-15','source_entity_type':'Manual'},follow_redirects=False).status_code==303
        ej=q('SELECT MAX(id) id FROM employee_journeys',one=True)['id'];tasks=q('SELECT * FROM employee_journey_tasks WHERE employee_journey_id=?',(ej,));assert tasks
        required=next(x for x in tasks if q('SELECT completion_evidence_required v FROM journey_task_templates WHERE id=?',(x['template_id'],),True)['v'])
        assert c.post(f"/lifecycle/journey-tasks/{required['id']}/status",data={'status':'Completed','evidence':'','notes':'Done'},follow_redirects=False).status_code==400
        for task in tasks:
            template=q('SELECT completion_evidence_required v FROM journey_task_templates WHERE id=?',(task['template_id'],),True)
            assert c.post(f"/lifecycle/journey-tasks/{task['id']}/status",data={'status':'Completed','evidence':'proof' if template['v'] else '','notes':'Done'},follow_redirects=False).status_code==303
        assert q('SELECT status FROM employee_journeys WHERE id=?',(ej,),True)['status']=='Completed'
        assert c.post('/lifecycle/offboarding',data={'employee_id':'2','separation_type':'Resignation','notice_date':'2027-05-01','last_working_date':'2027-05-31','reason':'Personal','eligible_for_rehire':'Yes'},follow_redirects=False).status_code==303
        case=q('SELECT MAX(id) id FROM offboarding_cases',one=True)['id'];assert c.post(f'/lifecycle/offboarding/{case}/decision',data={'decision':'approve'},follow_redirects=False).status_code==303
        assert c.post(f'/lifecycle/offboarding/{case}/complete',follow_redirects=False).status_code==400
        for task in q('SELECT * FROM offboarding_tasks WHERE case_id=?',(case,)):
            tmpl=q('SELECT evidence_required v FROM offboarding_task_templates WHERE id=?',(task['template_id'],),True)
            assert c.post(f"/lifecycle/offboarding-tasks/{task['id']}/status",data={'status':'Completed','evidence':'clearance' if tmpl['v'] else '','notes':'Done'},follow_redirects=False).status_code==303
        assert c.post(f'/lifecycle/offboarding/{case}/complete',follow_redirects=False).status_code==303
        assert q('SELECT status FROM employees WHERE id=2',one=True)['status']=='Inactive'
        assert q("SELECT COUNT(*) n FROM employee_events e JOIN lifecycle_event_types t ON t.id=e.event_type_id WHERE e.employee_id=2 AND t.code='SEPARATION'",one=True)['n']==1


def test_core_hr_apis_require_authentication():
    with client() as c:
        for path in ['/api/v1/core-hr/assignments','/api/v1/core-hr/events','/api/v1/core-hr/journeys','/api/v1/core-hr/offboarding']:
            assert c.get(path,follow_redirects=False).status_code in (303,307)
