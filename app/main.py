from fastapi import FastAPI,Request,Form,UploadFile,File,HTTPException,Depends    
from fastapi.responses import HTMLResponse,RedirectResponse,StreamingResponse,JSONResponse    
from fastapi.templating import Jinja2Templates    
from fastapi.staticfiles import StaticFiles    
from pathlib import Path    
from datetime import date,datetime,timedelta
from decimal import Decimal, ROUND_HALF_UP    
import csv,io,json,os,shutil,secrets,sqlite3,time    
from .db import conn,init_db,set_tenant,reset_tenant,current_tenant,tenant_db_path    
from .security import hash_password,verify_password,token,untoken,csrf_token,verify_csrf
from .cloud_control import control_conn,init_control_db,clean_slug,issue_scim_token,hash_token,cloud_audit,resolve_tenant
from .storage import store_upload
from .i18n import translate    
BASE=Path(__file__).resolve().parent.parent; UPLOADS=BASE/'uploads'; UPLOADS.mkdir(exist_ok=True)    
app=FastAPI(title='PeaWork HRIS',version='1.2.0',description='India-first production-readiness candidate HRIS')    
app.mount('/static',StaticFiles(directory=BASE/'app'/'static'),name='static'); templates=Jinja2Templates(directory=BASE/'app'/'templates')
SAFE_METHODS={'GET','HEAD','OPTIONS'}
CSRF_EXEMPT={'/scim/v2/Users','/metrics','/health'}
@app.middleware('http')
async def security_middleware(request,call_next):
    raw_session=request.cookies.get('peawork_session');payload=untoken(raw_session) if raw_session else None
    session_id=payload.get('j') if payload else f"anon:{request.cookies.get('peawork_tenant','local')}"
    existing=request.cookies.get('peawork_csrf')
    request.state.csrf=existing if existing and verify_csrf(existing,session_id) else csrf_token(session_id)
    if request.method not in SAFE_METHODS and request.url.path not in CSRF_EXEMPT:
        submitted=request.headers.get('x-csrf-token')
        if not submitted:
            # Read/cache the raw body instead of calling request.form(): in a
            # BaseHTTPMiddleware, form() consumes the stream and downstream
            # routes receive an empty body (every form POST becomes a 422).
            try:
                raw=await request.body()
                content_type=request.headers.get('content-type','')
                if 'application/x-www-form-urlencoded' in content_type:
                    from urllib.parse import parse_qs
                    submitted=(parse_qs(raw.decode('utf-8')).get('_csrf') or [None])[0]
                elif 'multipart/form-data' in content_type:
                    submitted=(await request.form()).get('_csrf')
            except Exception:submitted=None
        if not submitted or not verify_csrf(submitted,session_id):
            return JSONResponse({'error':'CSRF validation failed'},status_code=403)
    response=await call_next(request)
    if not any(b'peawork_csrf=' in value for key,value in response.raw_headers if key.lower()==b'set-cookie'):
        response.set_cookie('peawork_csrf',request.state.csrf,httponly=False,samesite='strict',secure=os.getenv('PEAWORK_ENV','local').startswith('cloud'),max_age=7200)
    response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Frame-Options']='DENY';response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    if os.getenv('PEAWORK_ENV','local').startswith('cloud'):response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    return response
@app.middleware('http')
async def tenant_context_middleware(request,call_next):
    slug=request.cookies.get('peawork_tenant') or request.headers.get('x-peawork-tenant')
    tenant=resolve_tenant(request.headers.get('host'),slug)
    resolved=tenant['slug'] if tenant else (slug or os.getenv('PEAWORK_DEFAULT_TENANT','local'))
    context_token=set_tenant(resolved)
    try:
        request.state.tenant=tenant or {'slug':resolved,'name':'Local','id':None}
        return await call_next(request)
    finally:
        reset_tenant(context_token)
def q(sql,args=(),one=False):    
    with conn() as c:    
        r=c.execute(sql,args).fetchall(); return (dict(r[0]) if r and one else None) if one else [dict(x) for x in r]    
def execq(sql,args=()):    
    with conn() as c:    
        cur=c.execute(sql,args); return cur.lastrowid    
def audit(actor,action,etype,eid='',details='',ip=''):
    execq('INSERT INTO audit_log(actor,action,entity_type,entity_id,details,ip) VALUES(?,?,?,?,?,?)',(actor,action,etype,str(eid),details,ip))
def tenant_employee_limit():
    tenant=resolve_tenant(slug=current_tenant())
    if not tenant:return None
    with control_conn() as c:
        row=c.execute("SELECT COALESCE(s.employee_limit_override,p.employee_limit) employee_limit FROM tenant_subscriptions s JOIN subscription_plans p ON p.id=s.plan_id WHERE s.tenant_id=? AND s.status IN ('Trial','Active')",(tenant['id'],)).fetchone()
        return row['employee_limit'] if row else None
def enforce_employee_limit():
    limit=tenant_employee_limit()
    if limit is not None and q("SELECT COUNT(*) n FROM employees WHERE status='Active'",one=True)['n']>=limit:
        raise HTTPException(403,'Tenant employee subscription limit reached')
def record_security_event(event_type,severity='Medium',actor='',ip='',details='',tenant_id=None):
    with control_conn() as c:c.execute('INSERT INTO security_events(tenant_id,severity,event_type,actor,ip,details) VALUES(?,?,?,?,?,?)',(tenant_id,severity,event_type,actor,ip,details))
def session_active(payload):
    if not payload or not payload.get('j'):return False
    with control_conn() as c:
        row=c.execute('SELECT id FROM session_registry WHERE jti=? AND tenant_slug=? AND user_id=? AND revoked_at IS NULL AND expires_at>?',(payload['j'],payload.get('t','local'),payload['u'],int(time.time()))).fetchone()
        return bool(row)
def register_session(payload,ip='',user_agent=''):
    with control_conn() as c:c.execute('INSERT INTO session_registry(tenant_slug,user_id,jti,expires_at,ip,user_agent) VALUES(?,?,?,?,?,?)',(payload.get('t','local'),payload['u'],payload['j'],payload['e'],ip,user_agent[:250]))
def revoke_session(payload):
    if payload and payload.get('j'):
        with control_conn() as c:c.execute('UPDATE session_registry SET revoked_at=CURRENT_TIMESTAMP WHERE jti=?',(payload['j'],))
def login_throttled(username,ip,window=900,max_failures=5):
    cutoff=int(time.time())-window
    with control_conn() as c:
        failures=c.execute('SELECT COUNT(*) n FROM login_attempts WHERE tenant_slug=? AND username=? AND ip=? AND success=0 AND attempted_at>=?',(current_tenant(),username,ip,cutoff)).fetchone()['n']
        return failures>=max_failures
def record_login_attempt(username,ip,success):
    with control_conn() as c:c.execute('INSERT INTO login_attempts(tenant_slug,username,ip,success,attempted_at) VALUES(?,?,?,?,?)',(current_tenant(),username,ip,int(bool(success)),int(time.time())))
def me(request):
    t=request.cookies.get('peawork_session'); x=untoken(t) if t else None
    if not x or x.get('t','local')!=current_tenant() or not session_active(x): return None
    return q('SELECT id,username,role,employee_id FROM users WHERE id=? AND active=1',(x['u'],),True)    
def require(request:Request,roles=None):    
    u=me(request)    
    if not u: raise HTTPException(401,'Login required')    
    if roles and u['role'] not in roles: raise HTTPException(403,'Insufficient permissions')    
    return u    
def page(request,name,**ctx):
    u=me(request); notes=q('SELECT * FROM notifications WHERE user_id=? AND read_at IS NULL ORDER BY id DESC LIMIT 5',(u['id'],)) if u else []
    locale=request.cookies.get('peawork_locale','en')
    return templates.TemplateResponse(request,name,{'user':u,'notifications':notes,'today':date.today().isoformat(),'tenant':getattr(request.state,'tenant',{'slug':current_tenant(),'name':'Local'}),'csrf_token':request.state.csrf,'locale':locale,'tr':translate(locale),**ctx})
@app.on_event('startup')
def startup():
    environment=os.getenv('PEAWORK_ENV','local')
    if environment.startswith(('cloud','production')) and os.getenv('PEAWORK_SECRET','dev-only-change-me')=='dev-only-change-me':raise RuntimeError('PEAWORK_SECRET must be replaced before cloud or production startup')
    init_control_db();init_db()
    if not q('SELECT id FROM users LIMIT 1',one=True):seed()
def seed():
    bootstrap_password=os.getenv('PEAWORK_BOOTSTRAP_ADMIN_PASSWORD','ChangeMe123!')
    if os.getenv('PEAWORK_ENV','local').startswith(('cloud','production')) and bootstrap_password=='ChangeMe123!':raise RuntimeError('Set PEAWORK_BOOTSTRAP_ADMIN_PASSWORD before cloud or production bootstrap')
    admin=execq('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',('admin',hash_password(bootstrap_password),'admin'))    
    hr=execq('INSERT INTO departments(name,code) VALUES(?,?)',('People & Culture','HR'))    
    eng=execq('INSERT INTO departments(name,code) VALUES(?,?)',('Engineering','ENG'))    
    p1=execq('INSERT INTO positions(title,code,department_id,grade) VALUES(?,?,?,?)',('HR Manager','HR-MGR',hr,'M2'))    
    p2=execq('INSERT INTO positions(title,code,department_id,grade) VALUES(?,?,?,?)',('Software Engineer','ENG-SE',eng,'P2'))    
    e1=execq('INSERT INTO employees(employee_no,first_name,last_name,work_email,date_of_joining,department_id,position_id) VALUES(?,?,?,?,?,?,?)',('PW0001','Ananya','Sharma','ananya@peawork.local','2024-04-01',hr,p1))    
    e2=execq('INSERT INTO employees(employee_no,first_name,last_name,work_email,date_of_joining,department_id,position_id,manager_id) VALUES(?,?,?,?,?,?,?,?)',('PW0002','Arjun','Mehta','arjun@peawork.local','2025-01-15',eng,p2,e1))    
    execq('UPDATE users SET employee_id=? WHERE id=?',(e1,admin))    
    for x in [('Casual Leave','CL',12,3),('Sick Leave','SL',12,0),('Earned Leave','EL',18,15)]: execq('INSERT INTO leave_types(name,code,annual_entitlement,carry_forward_limit) VALUES(?,?,?,?)',x)    
    for d,n in [('2026-01-26','Republic Day'),('2026-08-15','Independence Day'),('2026-10-02','Gandhi Jayanti')]: execq('INSERT INTO holidays(holiday_date,name) VALUES(?,?)',(d,n))    
    execq('INSERT INTO settings(key,value) VALUES(?,?)',('company_name','PeaWork Demo India'))  
    journey=execq('INSERT INTO onboarding_journeys(name,employment_type) VALUES(?,?)',('Standard India Onboarding','Any'))  
    for title,category,owner,offset in [  
        ('Collect identity and tax documents','Compliance','hr',-7),  
        ('Prepare laptop and account access','IT','admin',-3),  
        ('Share joining instructions','Pre-boarding','hr',-2),  
        ('Complete workplace orientation','Orientation','hr',0),  
        ('Manager welcome and role goals','Manager','manager',1),  
        ('30-day check-in','Probation','manager',30),  
        ('Probation confirmation review','Probation','manager',85),  
    ]:  
        execq('INSERT INTO onboarding_task_templates(journey_id,title,category,owner_role,due_offset_days) VALUES(?,?,?,?,?)',(journey,title,category,owner,offset))  
    shift=execq('INSERT INTO shifts(name,code,start_time,end_time,break_minutes,grace_minutes,minimum_hours) VALUES(?,?,?,?,?,?,?)',('General Shift','GEN','09:30','18:30',60,10,8))  
    weekly=execq('INSERT INTO weekly_offs(name,saturday,sunday) VALUES(?,?,?)',('Saturday and Sunday',1,1))  
    execq('INSERT INTO employee_work_patterns(employee_id,shift_id,weekly_off_id,effective_from) VALUES(?,?,?,?)',(e1,shift,weekly,'2024-04-01'))  
    execq('INSERT INTO employee_work_patterns(employee_id,shift_id,weekly_off_id,effective_from) VALUES(?,?,?,?)',(e2,shift,weekly,'2025-01-15'))  
    for name,category,description in [
        ('Collaboration','Core','Works effectively across teams and builds trust.'),
        ('Customer Focus','Core','Understands and delivers value for internal and external customers.'),
        ('Execution','Core','Turns priorities into reliable, measurable outcomes.'),
        ('People Leadership','Leadership','Develops people, sets direction and creates accountability.'),
    ]:
        execq('INSERT INTO competencies(name,category,description) VALUES(?,?,?)',(name,category,description))
    for name,category in [('Python','Technical'),('FastAPI','Technical'),('Data Analysis','Technical'),('Stakeholder Management','Business'),('Coaching','Leadership')]:
        execq('INSERT INTO skills(name,category) VALUES(?,?)',(name,category))
    for code,name,ctype,calc,taxable,order in [
        ('BASIC','Basic Pay','Earning','Percent CTC',1,10),
        ('HRA','Housing Allowance','Earning','Percent Component',1,20),
        ('SPECIAL','Special Allowance','Earning','Fixed',1,30),
        ('ARREAR','Arrears','Earning','Input',1,40),
        ('REIMB','Approved Reimbursement','Reimbursement','Input',0,50),
        ('OTHERDED','Other Deduction','Deduction','Input',0,60),
    ]:
        execq('INSERT INTO pay_components(code,name,component_type,calculation_type,taxable,display_order) VALUES(?,?,?,?,?,?)',(code,name,ctype,calc,taxable,order))
    entity=execq('INSERT INTO legal_entities(code,name,country_code,currency_code,timezone) VALUES(?,?,?,?,?)',('PW-IN','PeaWork Demo India','IN','INR','Asia/Kolkata'))
    location=execq('INSERT INTO work_locations(code,name,legal_entity_id,country_code,state_region,city,timezone) VALUES(?,?,?,?,?,?,?)',('GGN','Gurugram Office',entity,'IN','Haryana','Gurugram','Asia/Kolkata'))
    cost_center=execq('INSERT INTO cost_centers(code,name,legal_entity_id) VALUES(?,?,?)',('CC-HR','People & Culture',entity))
    family=execq('INSERT INTO job_families(code,name,description) VALUES(?,?,?)',('CORP','Corporate Functions','Corporate and enabling functions'))
    job=execq('INSERT INTO job_profiles(code,title,family_id,level_code,grade) VALUES(?,?,?,?,?)',('HR-MGR','HR Manager',family,'M2','M2'))
    for employee_id,employee_no,department_id,position_id,manager_id,join_date in [(e1,'PW0001',hr,p1,None,'2024-04-01'),(e2,'PW0002',eng,p2,e1,'2025-01-15')]:
        execq('INSERT INTO employee_global_profiles(employee_id,global_employee_id,home_country_code,primary_language,data_residency_region) VALUES(?,?,?,?,?)',(employee_id,'G-'+employee_no,'IN','en','india-central'))
        execq('INSERT INTO employee_assignments(employee_id,assignment_no,legal_entity_id,location_id,department_id,cost_center_id,job_profile_id,position_id,manager_id,effective_from,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(employee_id,'A1',entity,location,department_id,cost_center,job if employee_id==e1 else None,position_id,manager_id,join_date,'Initial migration'))
    for code,name,category in [('HIRE','Hire','Onboarding'),('TRANSFER','Transfer','Employment'),('PROMOTION','Promotion','Employment'),('SALARY_CHANGE','Salary Change','Compensation'),('SEPARATION','Separation','Offboarding')]: execq('INSERT INTO lifecycle_event_types(code,name,category) VALUES(?,?,?)',(code,name,category))
    journey=execq('INSERT INTO journey_definitions(code,name,journey_type,country_code) VALUES(?,?,?,?)',('ONBOARD-IN','India Standard Onboarding','Onboarding','IN'))
    for title,category,owner,offset,evidence in [('Collect identity and statutory documents','Compliance','hr',-7,1),('Prepare equipment and access','IT','admin',-3,1),('Manager welcome and 30-day goals','Manager','manager',1,0)]: execq('INSERT INTO journey_task_templates(journey_id,title,category,owner_role,due_offset_days,completion_evidence_required) VALUES(?,?,?,?,?,?)',(journey,title,category,owner,offset,evidence))
    for title,category,owner,offset,evidence in [('Disable application access','Access','admin',0,1),('Recover company assets','Assets','hr',0,1),('Complete knowledge transfer','Handover','manager',-3,1),('Confirm final payroll inputs','Payroll','payroll',0,1),('Conduct exit interview','Exit Interview','hr',-2,0)]: execq('INSERT INTO offboarding_task_templates(title,category,owner_role,due_offset_days,evidence_required) VALUES(?,?,?,?,?)',(title,category,owner,offset,evidence))
    audit('system','SEED','database','','Demo organization initialized through v1.2 employee experience')    
@app.exception_handler(401)    
async def unauthorized(request,exc): return RedirectResponse('/login?error=Please+sign+in',303)    
@app.exception_handler(403)    
async def forbidden(request,exc): return page(request,'error.html',code=403,message='You do not have access to this action.')    
@app.get('/health')
def health(): return {'status':'ok','version':'1.2.0','deployment':os.getenv('PEAWORK_ENV','local'),'tenant':current_tenant(),'database':'postgresql' if os.getenv('PEAWORK_DATABASE_URL','').startswith('postgresql') else 'sqlite','storage':os.getenv('PEAWORK_STORAGE_BACKEND','configured')}
@app.get('/login',response_class=HTMLResponse)    
def login_page(request:Request,error:str=''): return page(request,'login.html',error=error)    
@app.post('/login')
def login(request:Request,username:str=Form(...),password:str=Form(...)):
    ip=request.client.host
    if login_throttled(username,ip):
        record_security_event('LOGIN_THROTTLED','High',username,ip,'Too many failed login attempts')
        return RedirectResponse('/login?error=Too+many+attempts.+Try+later',303)
    u=q('SELECT * FROM users WHERE username=? AND active=1',(username,),True)
    if not u or not verify_password(password,u['password_hash']):
        record_login_attempt(username,ip,False);audit(username,'LOGIN_FAILED','session','','',ip);return RedirectResponse('/login?error=Invalid+credentials',303)
    if not u['password_hash'].startswith('pbkdf2_sha256$'):execq('UPDATE users SET password_hash=? WHERE id=?',(hash_password(password),u['id']))
    record_login_attempt(username,ip,True);secure_cookie=os.getenv('PEAWORK_ENV','local').startswith('cloud');jti=secrets.token_urlsafe(24);signed=token(u['id'],u['role'],current_tenant(),jti=jti);payload=untoken(signed)
    register_session(payload,ip,request.headers.get('user-agent',''))
    r=RedirectResponse('/',303);r.set_cookie('peawork_session',signed,httponly=True,samesite='strict',secure=secure_cookie,max_age=28800);r.set_cookie('peawork_csrf',csrf_token(jti),httponly=False,samesite='strict',secure=secure_cookie,max_age=7200)
    audit(username,'LOGIN','session',u['id'],'',ip);return r
@app.post('/logout')
def logout(request:Request):
    raw=request.cookies.get('peawork_session');payload=untoken(raw) if raw else None;u=me(request);revoke_session(payload)
    if u:audit(u['username'],'LOGOUT','session',u['id'])
    r=RedirectResponse('/login',303);r.delete_cookie('peawork_session');r.delete_cookie('peawork_csrf');return r    
@app.get('/',response_class=HTMLResponse)    
def dashboard(request:Request):    
    u=require(request)    
    stats={'employees':q("SELECT COUNT(*) n FROM employees WHERE status='Active'",one=True)['n'],'departments':q('SELECT COUNT(*) n FROM departments WHERE active=1',one=True)['n'],'pending':q("SELECT COUNT(*) n FROM leave_requests WHERE status='Pending'",one=True)['n'],'documents':q('SELECT COUNT(*) n FROM documents',one=True)['n'],'open_requisitions':q("SELECT COUNT(*) n FROM requisitions WHERE status='Open'",one=True)['n'],'active_candidates':q("SELECT COUNT(*) n FROM applications WHERE status='Active'",one=True)['n'],'onboarding':q("SELECT COUNT(*) n FROM onboardings WHERE status IN ('Planned','In Progress')",one=True)['n'],'present_today':q("SELECT COUNT(*) n FROM attendance_records WHERE attendance_date=? AND status IN ('Present','Late','Half Day')",(date.today().isoformat(),),True)['n'],'pending_timesheets':q("SELECT COUNT(*) n FROM timesheets WHERE status='Submitted'",one=True)['n'],'active_cycles':q("SELECT COUNT(*) n FROM performance_cycles WHERE status='Active'",one=True)['n'],'goals_at_risk':q("SELECT COUNT(*) n FROM goals WHERE status='At Risk'",one=True)['n'],'feedback_due':q("SELECT COUNT(*) n FROM feedback_requests WHERE status='Requested'",one=True)['n'],'open_payroll':q("SELECT COUNT(*) n FROM payroll_periods WHERE status IN ('Open','Processing')",one=True)['n'],'payroll_errors':q("SELECT COUNT(*) n FROM payroll_runs WHERE validation_errors IS NOT NULL AND validation_errors!=''",one=True)['n'],'pending_transfers':q("SELECT COUNT(*) n FROM employee_transfers WHERE status='Pending Approval'",one=True)['n'],'active_journeys':q("SELECT COUNT(*) n FROM employee_journeys WHERE status IN ('Planned','In Progress')",one=True)['n'],'offboarding_open':q("SELECT COUNT(*) n FROM offboarding_cases WHERE status NOT IN ('Completed','Cancelled')",one=True)['n'],'my_inbox':q("SELECT COUNT(*) n FROM inbox_items WHERE user_id=? AND status='Open'",(u['id'],),True)['n'],'profile_changes':q("SELECT COUNT(*) n FROM employee_change_requests WHERE status='Pending'",one=True)['n']}    
    recent=q('SELECT e.employee_no,e.first_name,e.last_name,d.name department,e.date_of_joining FROM employees e LEFT JOIN departments d ON d.id=e.department_id ORDER BY e.id DESC LIMIT 6')    
    leaves=q("SELECT l.id,e.first_name||' '||e.last_name employee,t.name type,l.start_date,l.end_date,l.days,l.status FROM leave_requests l JOIN employees e ON e.id=l.employee_id JOIN leave_types t ON t.id=l.leave_type_id ORDER BY l.id DESC LIMIT 6")    
    return page(request,'dashboard.html',stats=stats,recent=recent,leaves=leaves)    
@app.get('/employees',response_class=HTMLResponse)    
def employees(request:Request,search:str=''):    
    require(request)    
    like=f'%{search}%'; rows=q("""SELECT e.*,d.name department,p.title position,m.first_name||' '||m.last_name manager FROM employees e LEFT JOIN departments d ON d.id=e.department_id LEFT JOIN positions p ON p.id=e.position_id LEFT JOIN employees m ON m.id=e.manager_id WHERE e.first_name||' '||e.last_name LIKE ? OR e.employee_no LIKE ? OR e.work_email LIKE ? ORDER BY e.first_name""",(like,like,like))    
    return page(request,'employees.html',employees=rows,search=search)    
@app.get('/employees/new',response_class=HTMLResponse)    
def employee_new(request:Request): require(request,['admin','hr']);return page(request,'employee_form.html',employee=None,departments=q('SELECT * FROM departments WHERE active=1'),positions=q('SELECT * FROM positions WHERE active=1'),managers=q("SELECT id,first_name||' '||last_name name FROM employees WHERE status='Active'"))    
@app.post('/employees/new')    
def employee_create(request:Request,employee_no:str=Form(...),first_name:str=Form(...),last_name:str=Form(...),work_email:str=Form(''),phone:str=Form(''),date_of_joining:str=Form(''),employment_type:str=Form('Full-time'),department_id:int|None=Form(None),position_id:int|None=Form(None),manager_id:int|None=Form(None),location:str=Form('Gurugram')):
    u=require(request,['admin','hr'])
    enforce_employee_limit()
    eid=execq("""INSERT INTO employees(employee_no,first_name,last_name,work_email,phone,date_of_joining,employment_type,department_id,position_id,manager_id,location) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(employee_no,first_name,last_name,work_email or None,phone,date_of_joining,employment_type,department_id,position_id,manager_id,location))    
    execq('INSERT INTO employee_history(employee_id,effective_date,change_type,new_value) VALUES(?,?,?,?)',(eid,date_of_joining or date.today().isoformat(),'HIRE',json.dumps({'department_id':department_id,'position_id':position_id})))    
    audit(u['username'],'CREATE','employee',eid,employee_no,request.client.host); return RedirectResponse(f'/employees/{eid}',303)    
@app.get('/employees/{eid}',response_class=HTMLResponse)    
def employee_view(eid:int,request:Request):    
    require(request); e=q("""SELECT e.*,d.name department,p.title position,p.grade,m.first_name||' '||m.last_name manager FROM employees e LEFT JOIN departments d ON d.id=e.department_id LEFT JOIN positions p ON p.id=e.position_id LEFT JOIN employees m ON m.id=e.manager_id WHERE e.id=?""",(eid,),True)    
    if not e: raise HTTPException(404)    
    return page(request,'employee_view.html',employee=e,history=q('SELECT * FROM employee_history WHERE employee_id=? ORDER BY effective_date DESC,id DESC',(eid,)),documents=q('SELECT * FROM documents WHERE employee_id=? ORDER BY id DESC',(eid,)),leaves=q("""SELECT l.*,t.name type FROM leave_requests l JOIN leave_types t ON t.id=l.leave_type_id WHERE employee_id=? ORDER BY id DESC""",(eid,)))    
@app.get('/organization',response_class=HTMLResponse)    
def organization(request:Request): require(request);return page(request,'organization.html',departments=q("SELECT d.*,p.name parent,e.first_name||' '||e.last_name manager,(SELECT COUNT(*) FROM employees x WHERE x.department_id=d.id AND x.status='Active') headcount FROM departments d LEFT JOIN departments p ON p.id=d.parent_id LEFT JOIN employees e ON e.id=d.manager_employee_id ORDER BY d.name"),positions=q("SELECT p.*,d.name department FROM positions p LEFT JOIN departments d ON d.id=p.department_id ORDER BY d.name,p.title"))    
@app.post('/organization/departments')    
def add_department(request:Request,name:str=Form(...),code:str=Form(...)):    
    u=require(request,['admin','hr']); i=execq('INSERT INTO departments(name,code) VALUES(?,?)',(name,code.upper()));audit(u['username'],'CREATE','department',i,name);return RedirectResponse('/organization',303)    
@app.post('/organization/positions')    
def add_position(request:Request,title:str=Form(...),code:str=Form(...),department_id:int=Form(...),grade:str=Form('')):    
    u=require(request,['admin','hr']);i=execq('INSERT INTO positions(title,code,department_id,grade) VALUES(?,?,?,?)',(title,code.upper(),department_id,grade));audit(u['username'],'CREATE','position',i,title);return RedirectResponse('/organization',303)    
@app.get('/leave',response_class=HTMLResponse)    
def leave(request:Request):    
    u=require(request); emp=u.get('employee_id')    
    rows=q("""SELECT l.*,e.first_name||' '||e.last_name employee,t.name type FROM leave_requests l JOIN employees e ON e.id=l.employee_id JOIN leave_types t ON t.id=l.leave_type_id WHERE (? IN ('admin','hr','manager') OR l.employee_id=?) ORDER BY l.id DESC""",(u['role'],emp))    
    return page(request,'leave.html',requests=rows,types=q('SELECT * FROM leave_types WHERE active=1'),employees=q("SELECT id,first_name||' '||last_name name FROM employees WHERE status='Active'"),holidays=q('SELECT * FROM holidays ORDER BY holiday_date'))    
@app.post('/leave/request')    
def leave_request(request:Request,employee_id:int=Form(...),leave_type_id:int=Form(...),start_date:str=Form(...),end_date:str=Form(...),days:float=Form(...),reason:str=Form('')):    
    u=require(request);     
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)    
    i=execq('INSERT INTO leave_requests(employee_id,leave_type_id,start_date,end_date,days,reason) VALUES(?,?,?,?,?,?)',(employee_id,leave_type_id,start_date,end_date,days,reason));audit(u['username'],'CREATE','leave_request',i,reason);return RedirectResponse('/leave',303)    
@app.post('/leave/{lid}/decision')    
def leave_decision(lid:int,request:Request,decision:str=Form(...),comment:str=Form('')):    
    u=require(request,['admin','hr','manager']); status='Approved' if decision=='approve' else 'Rejected';execq('UPDATE leave_requests SET status=?,approver_id=?,approver_comment=?,decided_at=CURRENT_TIMESTAMP WHERE id=? AND status=?',(status,u['employee_id'],comment,lid,'Pending'));audit(u['username'],status.upper(),'leave_request',lid,comment);return RedirectResponse('/leave',303)    
@app.get('/documents',response_class=HTMLResponse)    
def documents(request:Request): require(request);return page(request,'documents.html',documents=q("SELECT x.*,e.employee_no,e.first_name||' '||e.last_name employee FROM documents x LEFT JOIN employees e ON e.id=x.employee_id ORDER BY x.id DESC"),employees=q("SELECT id,employee_no||' - '||first_name||' '||last_name name FROM employees ORDER BY first_name"))    
@app.post('/documents/upload')    
def upload_document(request:Request,employee_id:int|None=Form(None),category:str=Form(...),title:str=Form(...),expires_on:str=Form(''),file:UploadFile=File(...)):    
    u=require(request,['admin','hr']); storage_path,_=store_upload(file.file,file.filename,'employee-documents',file.content_type)
    i=execq('INSERT INTO documents(employee_id,category,title,filename,storage_path,expires_on) VALUES(?,?,?,?,?,?)',(employee_id,category,title,file.filename,storage_path,expires_on or None));audit(u['username'],'UPLOAD','document',i,file.filename);return RedirectResponse('/documents',303)    
@app.get('/reports',response_class=HTMLResponse)    
def reports(request:Request):    
    require(request,['admin','hr','manager']); bydept=q("SELECT COALESCE(d.name,'Unassigned') label,COUNT(*) value FROM employees e LEFT JOIN departments d ON d.id=e.department_id WHERE e.status='Active' GROUP BY d.name ORDER BY value DESC");bystatus=q('SELECT status label,COUNT(*) value FROM employees GROUP BY status');leavestat=q('SELECT status label,COUNT(*) value FROM leave_requests GROUP BY status');return page(request,'reports.html',bydept=bydept,bystatus=bystatus,leavestat=leavestat)    
@app.get('/reports/employees.csv')    
def employees_csv(request:Request):    
    require(request,['admin','hr']); rows=q("""SELECT employee_no,first_name,last_name,work_email,date_of_joining,employment_type,status,location FROM employees ORDER BY employee_no"""); out=io.StringIO();w=csv.DictWriter(out,fieldnames=rows[0].keys() if rows else ['employee_no']);w.writeheader();w.writerows(rows);return StreamingResponse(iter([out.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=peawork-employees.csv'})    
@app.get('/audit',response_class=HTMLResponse)    
def audit_view(request:Request): require(request,['admin']);return page(request,'audit.html',rows=q('SELECT * FROM audit_log ORDER BY id DESC LIMIT 250'))    
@app.get('/settings',response_class=HTMLResponse)    
def settings(request:Request): require(request,['admin']);return page(request,'settings.html',settings=q('SELECT * FROM settings ORDER BY key'),users=q('SELECT id,username,role,active FROM users ORDER BY username'))    
@app.post('/settings')    
def update_setting(request:Request,key:str=Form(...),value:str=Form(...)):    
    u=require(request,['admin']);execq('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value));audit(u['username'],'UPDATE','setting',key,value);return RedirectResponse('/settings',303)    
@app.post('/settings/users')    
def add_user(request:Request,username:str=Form(...),password:str=Form(...),role:str=Form(...)):    
    u=require(request,['admin']);i=execq('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(username,hash_password(password),role));audit(u['username'],'CREATE','user',i,username);return RedirectResponse('/settings',303)    
@app.get('/api/v1/employees')    
def api_employees(request:Request): require(request);return q('SELECT id,employee_no,first_name,last_name,work_email,status,department_id,position_id FROM employees ORDER BY employee_no')    
@app.get('/api/v1/departments')    
def api_departments(request:Request): require(request);return q('SELECT * FROM departments WHERE active=1 ORDER BY name')    
@app.get('/api/v1/leave-requests')    
def api_leave(request:Request): require(request);return q('SELECT * FROM leave_requests ORDER BY id DESC')  
# --------------------------- v0.2 Hire-to-onboard ---------------------------  
RECRUITMENT_ROLES = ['admin', 'hr', 'recruiter']  
PIPELINE_STAGES = ['Applied', 'Screening', 'Interview', 'Assessment', 'Offer', 'Offer Accepted', 'Hired', 'Rejected', 'Withdrawn']  
def next_number(table, prefix):  
    row = q(f'SELECT MAX(id) n FROM {table}', one=True)  
    return f"{prefix}{(row['n'] or 0) + 1:05d}"  
def owner_for_role(role):  
    row = q('SELECT id FROM users WHERE role=? AND active=1 ORDER BY id LIMIT 1', (role,), True)  
    if not row and role == 'manager':  
        row = q("SELECT id FROM users WHERE role IN ('hr','admin') AND active=1 ORDER BY CASE role WHEN 'hr' THEN 1 ELSE 2 END,id LIMIT 1", one=True)  
    return row['id'] if row else None  
def recruitment_context():  
    return {  
        'requisitions': q("""SELECT r.*,d.name department,p.title position,e.first_name||' '||e.last_name hiring_manager,  
          (SELECT COUNT(*) FROM applications a WHERE a.requisition_id=r.id AND a.status='Active') active_applications  
          FROM requisitions r LEFT JOIN departments d ON d.id=r.department_id LEFT JOIN positions p ON p.id=r.position_id  
          LEFT JOIN employees e ON e.id=r.hiring_manager_id ORDER BY r.id DESC"""),  
        'candidates': q("SELECT * FROM candidates ORDER BY id DESC"),  
        'applications': q("""SELECT a.*,c.candidate_no,c.first_name||' '||c.last_name candidate,c.email,r.req_no,r.title requisition  
          FROM applications a JOIN candidates c ON c.id=a.candidate_id JOIN requisitions r ON r.id=a.requisition_id ORDER BY a.id DESC"""),  
        'interviews': q("""SELECT i.*,c.first_name||' '||c.last_name candidate,r.title requisition,e.first_name||' '||e.last_name interviewer  
          FROM interviews i JOIN applications a ON a.id=i.application_id JOIN candidates c ON c.id=a.candidate_id  
          JOIN requisitions r ON r.id=a.requisition_id LEFT JOIN employees e ON e.id=i.interviewer_employee_id ORDER BY i.scheduled_at DESC"""),  
        'offers': q("""SELECT o.*,c.first_name||' '||c.last_name candidate,r.title requisition  
          FROM offers o JOIN applications a ON a.id=o.application_id JOIN candidates c ON c.id=a.candidate_id  
          JOIN requisitions r ON r.id=a.requisition_id ORDER BY o.id DESC"""),  
        'departments': q('SELECT * FROM departments WHERE active=1 ORDER BY name'),  
        'positions': q('SELECT * FROM positions WHERE active=1 ORDER BY title'),  
        'employees': q("SELECT id,first_name||' '||last_name name FROM employees WHERE status='Active' ORDER BY first_name"),  
        'stages': PIPELINE_STAGES,  
    }  
@app.get('/recruitment', response_class=HTMLResponse)  
def recruitment(request: Request):  
    require(request, ['admin','hr','recruiter','manager'])  
    return page(request, 'recruitment.html', **recruitment_context())  
@app.post('/recruitment/requisitions')  
def requisition_create(request: Request, title: str=Form(...), department_id: int|None=Form(None), position_id: int|None=Form(None), hiring_manager_id: int|None=Form(None), openings: int=Form(1), employment_type: str=Form('Full-time'), location: str=Form('Gurugram'), target_date: str=Form(''), description: str=Form('')):  
    u=require(request, RECRUITMENT_ROLES)  
    req_no=next_number('requisitions','REQ-')  
    rid=execq("""INSERT INTO requisitions(req_no,title,department_id,position_id,hiring_manager_id,openings,employment_type,location,target_date,status,description,created_by)  
      VALUES(?,?,?,?,?,?,?,?,?,'Open',?,?)""",(req_no,title,department_id,position_id,hiring_manager_id,max(1,openings),employment_type,location,target_date or None,description,u['id']))  
    audit(u['username'],'CREATE','requisition',rid,req_no,request.client.host)  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/requisitions/{rid}/status')  
def requisition_status(rid:int, request:Request, status:str=Form(...)):  
    u=require(request, RECRUITMENT_ROLES)  
    if status not in ['Draft','Open','On Hold','Closed','Cancelled']: raise HTTPException(400,'Invalid requisition status')  
    execq('UPDATE requisitions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,rid))  
    audit(u['username'],'STATUS','requisition',rid,status)  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/candidates')  
def candidate_create(request:Request, first_name:str=Form(...), last_name:str=Form(...), email:str=Form(...), phone:str=Form(''), current_company:str=Form(''), experience_years:float=Form(0), source:str=Form('Direct'), resume:UploadFile|None=File(None)):  
    u=require(request, RECRUITMENT_ROLES)  
    filename=path=None  
    if resume and resume.filename:  
        filename=resume.filename  
        path,_=store_upload(resume.file,filename,'candidate-resumes',resume.content_type)  
    candidate_no=next_number('candidates','CAN-')  
    cid=execq("""INSERT INTO candidates(candidate_no,first_name,last_name,email,phone,current_company,experience_years,source,resume_filename,resume_path)  
      VALUES(?,?,?,?,?,?,?,?,?,?)""",(candidate_no,first_name,last_name,email.lower(),phone,current_company,max(0,experience_years),source,filename,path))  
    audit(u['username'],'CREATE','candidate',cid,candidate_no,request.client.host)  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/applications')  
def application_create(request:Request, candidate_id:int=Form(...), requisition_id:int=Form(...), owner_employee_id:int|None=Form(None), notes:str=Form('')):  
    u=require(request, RECRUITMENT_ROLES)  
    req=q("SELECT status FROM requisitions WHERE id=?",(requisition_id,),True)  
    if not req or req['status']!='Open': raise HTTPException(400,'Applications require an open requisition')  
    aid=execq("""INSERT INTO applications(candidate_id,requisition_id,stage,status,applied_on,owner_employee_id,notes)  
      VALUES(?,?,'Applied','Active',?,?,?)""",(candidate_id,requisition_id,date.today().isoformat(),owner_employee_id,notes))  
    audit(u['username'],'CREATE','application',aid,f'candidate={candidate_id}; requisition={requisition_id}')  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/applications/{aid}/stage')  
def application_stage(aid:int, request:Request, stage:str=Form(...), notes:str=Form('')):  
    u=require(request, RECRUITMENT_ROLES)  
    if stage not in PIPELINE_STAGES: raise HTTPException(400,'Invalid pipeline stage')  
    status='Closed' if stage in ['Hired','Rejected','Withdrawn'] else 'Active'  
    execq("UPDATE applications SET stage=?,status=?,notes=CASE WHEN ?='' THEN notes ELSE ? END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(stage,status,notes,notes,aid))  
    audit(u['username'],'STAGE','application',aid,stage)  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/interviews')  
def interview_create(request:Request, application_id:int=Form(...), interview_type:str=Form(...), scheduled_at:str=Form(...), interviewer_employee_id:int|None=Form(None)):  
    u=require(request, RECRUITMENT_ROLES)  
    iid=execq('INSERT INTO interviews(application_id,interview_type,scheduled_at,interviewer_employee_id) VALUES(?,?,?,?)',(application_id,interview_type,scheduled_at,interviewer_employee_id))  
    execq("UPDATE applications SET stage='Interview',updated_at=CURRENT_TIMESTAMP WHERE id=? AND stage IN ('Applied','Screening','Assessment')",(application_id,))  
    audit(u['username'],'SCHEDULE','interview',iid,scheduled_at)  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/interviews/{iid}/feedback')  
def interview_feedback(iid:int, request:Request, rating:int=Form(...), scorecard:str=Form(''), feedback:str=Form(''), recommendation:str=Form('Hold')):  
    u=require(request, ['admin','hr','recruiter','manager'])  
    rating=max(1,min(5,rating))  
    execq("UPDATE interviews SET rating=?,scorecard=?,feedback=?,status='Completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",(rating,scorecard,feedback,iid))  
    row=q('SELECT application_id FROM interviews WHERE id=?',(iid,),True)  
    if row and recommendation in ['Reject','Advance','Offer']:  
        stage={'Reject':'Rejected','Advance':'Assessment','Offer':'Offer'}[recommendation]  
        status='Closed' if stage=='Rejected' else 'Active'  
        execq('UPDATE applications SET stage=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(stage,status,row['application_id']))  
    audit(u['username'],'COMPLETE','interview',iid,f'rating={rating}; recommendation={recommendation}')  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/offers')  
def offer_create(request:Request, application_id:int=Form(...), offered_title:str=Form(...), offered_salary:float|None=Form(None), currency:str=Form('INR'), joining_date:str=Form(...), notes:str=Form('')):  
    u=require(request, RECRUITMENT_ROLES)  
    oid=execq("""INSERT INTO offers(application_id,offered_title,offered_salary,currency,joining_date,status,issued_at,notes)  
      VALUES(?,?,?,?,?,'Issued',CURRENT_TIMESTAMP,?)""",(application_id,offered_title,offered_salary,currency,joining_date,notes))  
    execq("UPDATE applications SET stage='Offer',updated_at=CURRENT_TIMESTAMP WHERE id=?",(application_id,))  
    audit(u['username'],'ISSUE','offer',oid,offered_title)  
    return RedirectResponse('/recruitment',303)  
@app.post('/recruitment/offers/{oid}/status')  
def offer_status(oid:int, request:Request, status:str=Form(...)):  
    u=require(request, RECRUITMENT_ROLES)  
    if status not in ['Draft','Issued','Accepted','Declined','Withdrawn']: raise HTTPException(400,'Invalid offer status')  
    execq("UPDATE offers SET status=?,responded_at=CASE WHEN ? IN ('Accepted','Declined') THEN CURRENT_TIMESTAMP ELSE responded_at END WHERE id=?",(status,status,oid))  
    row=q('SELECT application_id FROM offers WHERE id=?',(oid,),True)  
    if row:  
        stage='Offer Accepted' if status=='Accepted' else ('Rejected' if status in ['Declined','Withdrawn'] else 'Offer')  
        app_status='Closed' if status in ['Declined','Withdrawn'] else 'Active'  
        execq('UPDATE applications SET stage=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(stage,app_status,row['application_id']))  
    audit(u['username'],'STATUS','offer',oid,status)  
    return RedirectResponse('/recruitment',303)  
def onboarding_context():  
    return {  
        'journeys': q("""SELECT j.*,d.name department,(SELECT COUNT(*) FROM onboarding_task_templates t WHERE t.journey_id=j.id) task_count  
          FROM onboarding_journeys j LEFT JOIN departments d ON d.id=j.department_id ORDER BY j.name"""),  
        'templates': q("""SELECT t.*,j.name journey FROM onboarding_task_templates t JOIN onboarding_journeys j ON j.id=t.journey_id ORDER BY j.name,t.due_offset_days,t.id"""),  
        'accepted_offers': q("""SELECT o.id,o.joining_date,o.offered_title,c.first_name||' '||c.last_name candidate  
          FROM offers o JOIN applications a ON a.id=o.application_id JOIN candidates c ON c.id=a.candidate_id  
          LEFT JOIN onboardings n ON n.offer_id=o.id WHERE o.status='Accepted' AND n.id IS NULL ORDER BY o.joining_date"""),  
        'onboardings': q("""SELECT n.*,c.first_name||' '||c.last_name candidate,c.email,o.offered_title,j.name journey,  
          (SELECT COUNT(*) FROM onboarding_tasks t WHERE t.onboarding_id=n.id AND t.status!='Completed') open_tasks,  
          (SELECT COUNT(*) FROM onboarding_tasks t WHERE t.onboarding_id=n.id) total_tasks  
          FROM onboardings n JOIN candidates c ON c.id=n.candidate_id JOIN offers o ON o.id=n.offer_id  
          LEFT JOIN onboarding_journeys j ON j.id=n.journey_id ORDER BY n.start_date DESC"""),  
        'tasks': q("""SELECT t.*,c.first_name||' '||c.last_name candidate,u.username owner  
          FROM onboarding_tasks t JOIN onboardings n ON n.id=t.onboarding_id JOIN candidates c ON c.id=n.candidate_id  
          LEFT JOIN users u ON u.id=t.owner_user_id ORDER BY CASE t.status WHEN 'Open' THEN 1 WHEN 'In Progress' THEN 2 ELSE 3 END,t.due_date"""),  
        'reviews': q("""SELECT p.*,c.first_name||' '||c.last_name candidate,e.first_name||' '||e.last_name reviewer  
          FROM probation_reviews p JOIN onboardings n ON n.id=p.onboarding_id JOIN candidates c ON c.id=n.candidate_id  
          LEFT JOIN employees e ON e.id=p.reviewer_employee_id ORDER BY p.review_date DESC"""),  
        'departments': q('SELECT * FROM departments WHERE active=1 ORDER BY name'),  
        'employees': q("SELECT id,first_name||' '||last_name name FROM employees WHERE status='Active' ORDER BY first_name"),  
    }  
@app.get('/onboarding', response_class=HTMLResponse)  
def onboarding(request:Request):  
    require(request, ['admin','hr','recruiter','manager'])  
    return page(request,'onboarding.html',**onboarding_context())  
@app.post('/onboarding/journeys')  
def journey_create(request:Request, name:str=Form(...), department_id:int|None=Form(None), employment_type:str=Form('Any')):  
    u=require(request,['admin','hr'])  
    jid=execq('INSERT INTO onboarding_journeys(name,department_id,employment_type) VALUES(?,?,?)',(name,department_id,employment_type))  
    audit(u['username'],'CREATE','onboarding_journey',jid,name)  
    return RedirectResponse('/onboarding',303)  
@app.post('/onboarding/journeys/{jid}/tasks')  
def journey_task_create(jid:int, request:Request, title:str=Form(...), category:str=Form('General'), owner_role:str=Form('hr'), due_offset_days:int=Form(0), required:int=Form(1)):  
    u=require(request,['admin','hr'])  
    tid=execq('INSERT INTO onboarding_task_templates(journey_id,title,category,owner_role,due_offset_days,required) VALUES(?,?,?,?,?,?)',(jid,title,category,owner_role,due_offset_days,1 if required else 0))  
    audit(u['username'],'CREATE','onboarding_task_template',tid,title)  
    return RedirectResponse('/onboarding',303)  
@app.post('/onboarding/start')  
def onboarding_start(request:Request, offer_id:int=Form(...), journey_id:int=Form(...), probation_days:int=Form(90)):  
    u=require(request,['admin','hr','recruiter'])  
    offer=q("""SELECT o.*,a.candidate_id FROM offers o JOIN applications a ON a.id=o.application_id WHERE o.id=? AND o.status='Accepted'""",(offer_id,),True)  
    if not offer: raise HTTPException(400,'Onboarding requires an accepted offer')  
    start=datetime.fromisoformat(offer['joining_date']).date()  
    probation_end=(start+timedelta(days=max(0,probation_days))).isoformat()  
    nid=execq("INSERT INTO onboardings(candidate_id,offer_id,journey_id,status,start_date,probation_end_date) VALUES(?,?,?,'Planned',?,?)",(offer['candidate_id'],offer_id,journey_id,start.isoformat(),probation_end))  
    for task in q('SELECT * FROM onboarding_task_templates WHERE journey_id=? ORDER BY due_offset_days,id',(journey_id,)):  
        due=(start+timedelta(days=task['due_offset_days'])).isoformat()  
        execq('INSERT INTO onboarding_tasks(onboarding_id,template_id,title,category,owner_user_id,due_date) VALUES(?,?,?,?,?,?)',(nid,task['id'],task['title'],task['category'],owner_for_role(task['owner_role']),due))  
    execq("INSERT INTO probation_reviews(onboarding_id,review_date,status) VALUES(?,?,'Planned')",(nid,probation_end))  
    audit(u['username'],'START','onboarding',nid,f'offer={offer_id}; journey={journey_id}')  
    return RedirectResponse('/onboarding',303)  
@app.post('/onboarding/tasks/{tid}/status')  
def onboarding_task_status(tid:int, request:Request, status:str=Form(...), notes:str=Form('')):  
    u=require(request,['admin','hr','recruiter','manager'])  
    if status not in ['Open','In Progress','Blocked','Completed','Waived']: raise HTTPException(400,'Invalid task status')  
    execq("UPDATE onboarding_tasks SET status=?,notes=?,completed_at=CASE WHEN ? IN ('Completed','Waived') THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",(status,notes,status,tid))  
    row=q('SELECT onboarding_id FROM onboarding_tasks WHERE id=?',(tid,),True)  
    if row:  
        remaining=q("SELECT COUNT(*) n FROM onboarding_tasks WHERE onboarding_id=? AND status NOT IN ('Completed','Waived')",(row['onboarding_id'],),True)['n']  
        new_status='Completed' if remaining==0 else 'In Progress'  
        execq("UPDATE onboardings SET status=?,completed_at=CASE WHEN ?='Completed' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=? AND employee_id IS NULL",(new_status,new_status,row['onboarding_id']))  
    audit(u['username'],'STATUS','onboarding_task',tid,status)  
    return RedirectResponse('/onboarding',303)  
@app.post('/onboarding/probation/{pid}')  
def probation_review(pid:int, request:Request, reviewer_employee_id:int|None=Form(None), rating:int|None=Form(None), recommendation:str=Form(...), notes:str=Form('')):  
    u=require(request,['admin','hr','manager'])  
    if recommendation not in ['Confirm','Extend','Exit','Pending']: raise HTTPException(400,'Invalid recommendation')  
    status='Completed' if recommendation!='Pending' else 'Planned'  
    execq('UPDATE probation_reviews SET reviewer_employee_id=?,rating=?,recommendation=?,notes=?,status=? WHERE id=?',(reviewer_employee_id,rating,recommendation,notes,status,pid))  
    audit(u['username'],'REVIEW','probation',pid,recommendation)  
    return RedirectResponse('/onboarding',303)  
@app.post('/onboarding/{nid}/convert')  
def onboarding_convert(nid:int, request:Request, employee_no:str=Form(''), department_id:int|None=Form(None), position_id:int|None=Form(None), manager_id:int|None=Form(None), location:str=Form('Gurugram')):  
    u=require(request,['admin','hr'])  
    record=q("""SELECT n.*,c.first_name,c.last_name,c.email,c.phone,o.application_id,o.offered_title,o.joining_date,r.requisition_id  
      FROM onboardings n JOIN candidates c ON c.id=n.candidate_id JOIN offers o ON o.id=n.offer_id  
      JOIN applications r ON r.id=o.application_id WHERE n.id=?""",(nid,),True)  
    if not record: raise HTTPException(404,'Onboarding not found')  
    if record['employee_id']: raise HTTPException(400,'Candidate already converted')
    enforce_employee_limit()
    employee_no=employee_no or next_number('employees','PW')  
    with conn() as c:  
        cur=c.execute("""INSERT INTO employees(employee_no,first_name,last_name,work_email,phone,date_of_joining,employment_type,status,department_id,position_id,manager_id,location)  
          VALUES(?,?,?,?,?,?,'Full-time','Active',?,?,?,?)""",(employee_no,record['first_name'],record['last_name'],record['email'],record['phone'],record['joining_date'],department_id,position_id,manager_id,location))  
        eid=cur.lastrowid  
        c.execute('INSERT INTO employee_history(employee_id,effective_date,change_type,new_value,notes) VALUES(?,?,?,?,?)',(eid,record['joining_date'],'HIRE',json.dumps({'department_id':department_id,'position_id':position_id,'source':'recruitment'}),f"Converted from onboarding {nid}"))  
        c.execute("UPDATE onboardings SET employee_id=?,status='Converted',completed_at=CURRENT_TIMESTAMP WHERE id=?",(eid,nid))  
        c.execute("UPDATE applications SET stage='Hired',status='Closed',updated_at=CURRENT_TIMESTAMP WHERE id=?",(record['application_id'],))  
        c.execute("UPDATE candidates SET status='Hired',updated_at=CURRENT_TIMESTAMP WHERE id=?",(record['candidate_id'],))  
        hires=c.execute("""SELECT COUNT(*) FROM applications WHERE requisition_id=? AND stage='Hired'""",(record['requisition_id'],)).fetchone()[0]  
        openings=c.execute('SELECT openings FROM requisitions WHERE id=?',(record['requisition_id'],)).fetchone()[0]  
        if hires>=openings: c.execute("UPDATE requisitions SET status='Closed',updated_at=CURRENT_TIMESTAMP WHERE id=?",(record['requisition_id'],))  
    audit(u['username'],'CONVERT','onboarding',nid,f'employee={employee_no}')  
    return RedirectResponse(f'/employees/{eid}',303)  
@app.get('/api/v1/requisitions')  
def api_requisitions(request:Request): require(request); return q('SELECT * FROM requisitions ORDER BY id DESC')  
@app.get('/api/v1/candidates')  
def api_candidates(request:Request): require(request, ['admin','hr','recruiter','manager']); return q('SELECT id,candidate_no,first_name,last_name,email,source,status FROM candidates ORDER BY id DESC')  
@app.get('/api/v1/applications')  
def api_applications(request:Request): require(request, ['admin','hr','recruiter','manager']); return q('SELECT * FROM applications ORDER BY id DESC')  
@app.get('/api/v1/onboardings')  
def api_onboardings(request:Request): require(request, ['admin','hr','recruiter','manager']); return q('SELECT * FROM onboardings ORDER BY id DESC')  
# ----------------------------- v0.3 Workforce ------------------------------  
WORKFORCE_MANAGER_ROLES = ['admin','hr','manager']  
def normalize_punch(day, value):  
    if value and len(value)<=5: return f'{day}T{value}'  
    return value  
def worked_hours(check_in, check_out, break_minutes=0):  
    if not check_in or not check_out:  
        return 0  
    start=datetime.fromisoformat(check_in)  
    end=datetime.fromisoformat(check_out)  
    if end < start: end += timedelta(days=1)  
    return round(max(0,(end-start).total_seconds()/3600-(break_minutes or 0)/60),2)  
def workforce_context(u, month):  
    try:  
        first=datetime.strptime(month+'-01','%Y-%m-%d').date()  
    except ValueError:  
        first=date.today().replace(day=1); month=first.strftime('%Y-%m')  
    next_month=(first.replace(day=28)+timedelta(days=4)).replace(day=1)  
    start,end=first.isoformat(),next_month.isoformat()  
    broad=u['role'] in WORKFORCE_MANAGER_ROLES  
    emp_filter='' if broad else ' AND a.employee_id=?'  
    emp_args=() if broad else (u['employee_id'],)  
    attendance=q("""SELECT a.*,e.employee_no,e.first_name||' '||e.last_name employee,s.name shift  
      FROM attendance_records a JOIN employees e ON e.id=a.employee_id LEFT JOIN shifts s ON s.id=a.shift_id  
      WHERE a.attendance_date>=? AND a.attendance_date<?"""+emp_filter+" ORDER BY a.attendance_date DESC,e.first_name",(start,end)+emp_args)  
    timesheet_filter='' if broad else ' WHERE t.employee_id=?'  
    timesheet_args=() if broad else (u['employee_id'],)  
    return {  
      'month':month,  
      'employees':q("SELECT id,employee_no,first_name||' '||last_name name FROM employees WHERE status='Active' ORDER BY first_name"),  
      'shifts':q('SELECT * FROM shifts WHERE active=1 ORDER BY name'),  
      'weekly_offs':q('SELECT * FROM weekly_offs ORDER BY name'),  
      'patterns':q("""SELECT w.*,e.employee_no,e.first_name||' '||e.last_name employee,s.name shift,o.name weekly_off  
        FROM employee_work_patterns w JOIN employees e ON e.id=w.employee_id JOIN shifts s ON s.id=w.shift_id  
        LEFT JOIN weekly_offs o ON o.id=w.weekly_off_id ORDER BY w.effective_from DESC"""),  
      'attendance':attendance,  
      'overtime':q("""SELECT o.*,e.first_name||' '||e.last_name employee,a.first_name||' '||a.last_name approver  
        FROM overtime_requests o JOIN employees e ON e.id=o.employee_id LEFT JOIN employees a ON a.id=o.approver_employee_id  
        WHERE (? IN ('admin','hr','manager') OR o.employee_id=?) ORDER BY o.id DESC""",(u['role'],u['employee_id'])),  
      'timesheets':q("""SELECT t.*,e.first_name||' '||e.last_name employee,  
        COALESCE((SELECT SUM(x.hours) FROM timesheet_entries x WHERE x.timesheet_id=t.id),0) total_hours  
        FROM timesheets t JOIN employees e ON e.id=t.employee_id"""+timesheet_filter+' ORDER BY t.week_start DESC,t.id DESC',timesheet_args),  
      'entries':q("""SELECT x.*,e.first_name||' '||e.last_name employee FROM timesheet_entries x  
        JOIN timesheets t ON t.id=x.timesheet_id JOIN employees e ON e.id=t.employee_id  
        WHERE (? IN ('admin','hr','manager') OR t.employee_id=?) ORDER BY x.work_date DESC,x.id DESC""",(u['role'],u['employee_id'])),  
      'devices':q('SELECT * FROM biometric_devices ORDER BY name'),  
      'imports':q('SELECT * FROM biometric_imports ORDER BY id DESC LIMIT 20'),  
      'accruals':q("""SELECT r.*,l.name leave_type,u.username run_by_name FROM leave_accrual_runs r  
        JOIN leave_types l ON l.id=r.leave_type_id LEFT JOIN users u ON u.id=r.run_by ORDER BY r.id DESC"""),  
      'leave_types':q('SELECT * FROM leave_types WHERE active=1 ORDER BY name'),  
      'calendar':q("""SELECT a.attendance_date event_date,e.first_name||' '||e.last_name employee,  
        'Attendance: '||a.status event_type,a.check_in detail FROM attendance_records a JOIN employees e ON e.id=a.employee_id  
        WHERE a.attendance_date>=? AND a.attendance_date<?  
        UNION ALL SELECT l.start_date,e.first_name||' '||e.last_name,'Leave: '||l.status,t.name  
        FROM leave_requests l JOIN employees e ON e.id=l.employee_id JOIN leave_types t ON t.id=l.leave_type_id  
        WHERE l.start_date<? AND l.end_date>=? ORDER BY event_date,employee""",(start,end,end,start)),  
    }  
@app.get('/workforce',response_class=HTMLResponse)  
def workforce(request:Request,month:str=''):  
    u=require(request); month=month or date.today().strftime('%Y-%m')  
    return page(request,'workforce.html',**workforce_context(u,month))  
@app.post('/workforce/shifts')  
def shift_create(request:Request,name:str=Form(...),code:str=Form(...),start_time:str=Form(...),end_time:str=Form(...),break_minutes:int=Form(60),grace_minutes:int=Form(10),minimum_hours:float=Form(8)):  
    u=require(request,['admin','hr'])  
    sid=execq('INSERT INTO shifts(name,code,start_time,end_time,break_minutes,grace_minutes,minimum_hours) VALUES(?,?,?,?,?,?,?)',(name,code.upper(),start_time,end_time,max(0,break_minutes),max(0,grace_minutes),max(0,minimum_hours)))  
    audit(u['username'],'CREATE','shift',sid,code)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/weekly-offs')  
def weekly_off_create(request:Request,name:str=Form(...),monday:int=Form(0),tuesday:int=Form(0),wednesday:int=Form(0),thursday:int=Form(0),friday:int=Form(0),saturday:int=Form(0),sunday:int=Form(0)):  
    u=require(request,['admin','hr'])  
    wid=execq('INSERT INTO weekly_offs(name,monday,tuesday,wednesday,thursday,friday,saturday,sunday) VALUES(?,?,?,?,?,?,?,?)',(name,int(bool(monday)),int(bool(tuesday)),int(bool(wednesday)),int(bool(thursday)),int(bool(friday)),int(bool(saturday)),int(bool(sunday))))  
    audit(u['username'],'CREATE','weekly_off',wid,name)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/patterns')  
def work_pattern_create(request:Request,employee_id:int=Form(...),shift_id:int=Form(...),weekly_off_id:int|None=Form(None),effective_from:str=Form(...),effective_to:str=Form('')):  
    u=require(request,['admin','hr'])  
    pid=execq('INSERT INTO employee_work_patterns(employee_id,shift_id,weekly_off_id,effective_from,effective_to) VALUES(?,?,?,?,?)',(employee_id,shift_id,weekly_off_id,effective_from,effective_to or None))  
    audit(u['username'],'ASSIGN','work_pattern',pid,f'employee={employee_id}')  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/attendance')  
def attendance_upsert(request:Request,employee_id:int=Form(...),attendance_date:str=Form(...),shift_id:int|None=Form(None),check_in:str=Form(''),check_out:str=Form(''),status:str=Form('Present'),notes:str=Form('')):  
    u=require(request)  
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)  
    shift=q('SELECT * FROM shifts WHERE id=?',(shift_id,),True) if shift_id else None  
    hours=worked_hours(check_in,check_out,shift['break_minutes'] if shift else 0)  
    overtime=max(0,round(hours-(shift['minimum_hours'] if shift else 8),2))  
    execq("""INSERT INTO attendance_records(employee_id,attendance_date,shift_id,check_in,check_out,worked_hours,overtime_hours,status,source,notes)  
      VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(employee_id,attendance_date) DO UPDATE SET shift_id=excluded.shift_id,check_in=excluded.check_in,  
      check_out=excluded.check_out,worked_hours=excluded.worked_hours,overtime_hours=excluded.overtime_hours,status=excluded.status,  
      source=excluded.source,notes=excluded.notes,updated_at=CURRENT_TIMESTAMP""",(employee_id,attendance_date,shift_id,check_in or None,check_out or None,hours,overtime,status,'Manual',notes))  
    row=q('SELECT id FROM attendance_records WHERE employee_id=? AND attendance_date=?',(employee_id,attendance_date),True)  
    audit(u['username'],'UPSERT','attendance',row['id'],f'{attendance_date}; hours={hours}')  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/overtime')  
def overtime_request(request:Request,employee_id:int=Form(...),attendance_date:str=Form(...),requested_hours:float=Form(...),reason:str=Form('')):  
    u=require(request)  
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)  
    oid=execq('INSERT INTO overtime_requests(employee_id,attendance_date,requested_hours,reason) VALUES(?,?,?,?)',(employee_id,attendance_date,max(0,requested_hours),reason))  
    audit(u['username'],'CREATE','overtime_request',oid,reason)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/overtime/{oid}/decision')  
def overtime_decision(oid:int,request:Request,decision:str=Form(...),comment:str=Form('')):  
    u=require(request,WORKFORCE_MANAGER_ROLES)  
    status='Approved' if decision=='approve' else 'Rejected'  
    execq('UPDATE overtime_requests SET status=?,approver_employee_id=?,approver_comment=?,decided_at=CURRENT_TIMESTAMP WHERE id=? AND status=?',(status,u['employee_id'],comment,oid,'Pending'))  
    audit(u['username'],status.upper(),'overtime_request',oid,comment)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/timesheets')  
def timesheet_create(request:Request,employee_id:int=Form(...),week_start:str=Form(...)):  
    u=require(request)  
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)  
    tid=execq("INSERT INTO timesheets(employee_id,week_start,status) VALUES(?,?,'Draft')",(employee_id,week_start))  
    audit(u['username'],'CREATE','timesheet',tid,week_start)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/timesheets/{tid}/entries')  
def timesheet_entry(tid:int,request:Request,work_date:str=Form(...),project_code:str=Form(''),activity:str=Form(...),hours:float=Form(...),notes:str=Form('')):  
    u=require(request)  
    ts=q('SELECT * FROM timesheets WHERE id=?',(tid,),True)  
    if not ts or ts['status'] not in ['Draft','Rejected']: raise HTTPException(400,'Only draft or rejected timesheets can be edited')  
    if u['role']=='employee' and ts['employee_id']!=u['employee_id']: raise HTTPException(403)  
    eid=execq('INSERT INTO timesheet_entries(timesheet_id,work_date,project_code,activity,hours,notes) VALUES(?,?,?,?,?,?)',(tid,work_date,project_code or None,activity,max(0,min(24,hours)),notes))  
    audit(u['username'],'CREATE','timesheet_entry',eid,activity)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/timesheets/{tid}/submit')  
def timesheet_submit(tid:int,request:Request):  
    u=require(request)  
    ts=q('SELECT * FROM timesheets WHERE id=?',(tid,),True)  
    if not ts or (u['role']=='employee' and ts['employee_id']!=u['employee_id']): raise HTTPException(403)  
    total=q('SELECT COALESCE(SUM(hours),0) n FROM timesheet_entries WHERE timesheet_id=?',(tid,),True)['n']  
    if total<=0: raise HTTPException(400,'Add at least one entry before submitting')  
    execq("UPDATE timesheets SET status='Submitted',submitted_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('Draft','Rejected')",(tid,))  
    audit(u['username'],'SUBMIT','timesheet',tid,f'hours={total}')  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/timesheets/{tid}/decision')  
def timesheet_decision(tid:int,request:Request,decision:str=Form(...),comment:str=Form('')):  
    u=require(request,WORKFORCE_MANAGER_ROLES)  
    status='Approved' if decision=='approve' else 'Rejected'  
    execq('UPDATE timesheets SET status=?,approver_employee_id=?,approver_comment=?,decided_at=CURRENT_TIMESTAMP WHERE id=? AND status=?',(status,u['employee_id'],comment,tid,'Submitted'))  
    audit(u['username'],status.upper(),'timesheet',tid,comment)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/biometric/devices')  
def biometric_device(request:Request,name:str=Form(...),device_code:str=Form(...),location:str=Form('')):  
    u=require(request,['admin','hr'])  
    did=execq('INSERT INTO biometric_devices(name,device_code,location) VALUES(?,?,?)',(name,device_code.upper(),location))  
    audit(u['username'],'CREATE','biometric_device',did,device_code)  
    return RedirectResponse('/workforce',303)  
@app.post('/workforce/biometric/import')  
def biometric_import(request:Request,device_id:int|None=Form(None),file:UploadFile=File(...)):  
    u=require(request,['admin','hr'])  
    raw=file.file.read().decode('utf-8-sig'); rows=list(csv.DictReader(io.StringIO(raw)))  
    imported=0; errors=[]  
    for line,row in enumerate(rows,2):  
        try:  
            emp=q('SELECT id FROM employees WHERE employee_no=?',(row.get('employee_no','').strip(),),True)  
            if not emp: raise ValueError('employee not found')  
            day=row.get('attendance_date','').strip(); cin=normalize_punch(day,row.get('check_in','').strip()); cout=normalize_punch(day,row.get('check_out','').strip())  
            if not day: raise ValueError('attendance_date missing')  
            hours=worked_hours(cin,cout,0)  
            execq("""INSERT INTO attendance_records(employee_id,attendance_date,check_in,check_out,worked_hours,overtime_hours,status,source)  
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(employee_id,attendance_date) DO UPDATE SET check_in=excluded.check_in,check_out=excluded.check_out,  
              worked_hours=excluded.worked_hours,overtime_hours=excluded.overtime_hours,status=excluded.status,source=excluded.source,updated_at=CURRENT_TIMESTAMP""",  
              (emp['id'],day,cin or None,cout or None,hours,max(0,round(hours-8,2)),row.get('status','Present') or 'Present','Biometric'))  
            imported+=1  
        except Exception as e: errors.append(f'row {line}: {e}')  
    iid=execq('INSERT INTO biometric_imports(filename,device_id,imported_by,rows_received,rows_imported,rows_rejected,errors) VALUES(?,?,?,?,?,?,?)',(file.filename,device_id,u['id'],len(rows),imported,len(errors),'\\n'.join(errors[:50])))  
    if device_id: execq('UPDATE biometric_devices SET last_sync_at=CURRENT_TIMESTAMP WHERE id=?',(device_id,))  
    audit(u['username'],'IMPORT','biometric_import',iid,f'{imported}/{len(rows)} rows')  
    return RedirectResponse('/workforce',303)  
@app.post('/api/v1/biometric-punch')  
def api_biometric_punch(request:Request,employee_no:str=Form(...),attendance_date:str=Form(...),check_in:str=Form(''),check_out:str=Form(''),device_code:str=Form('API')):  
    u=require(request,['admin','hr'])  
    emp=q('SELECT id FROM employees WHERE employee_no=?',(employee_no,),True)  
    if not emp: raise HTTPException(404,'Employee not found')  
    check_in=normalize_punch(attendance_date,check_in); check_out=normalize_punch(attendance_date,check_out)  
    hours=worked_hours(check_in,check_out,0)  
    execq("""INSERT INTO attendance_records(employee_id,attendance_date,check_in,check_out,worked_hours,overtime_hours,status,source)  
      VALUES(?,?,?,?,?,?,'Present',?) ON CONFLICT(employee_id,attendance_date) DO UPDATE SET check_in=excluded.check_in,check_out=excluded.check_out,  
      worked_hours=excluded.worked_hours,overtime_hours=excluded.overtime_hours,source=excluded.source,updated_at=CURRENT_TIMESTAMP""",  
      (emp['id'],attendance_date,check_in or None,check_out or None,hours,max(0,round(hours-8,2)),f'API:{device_code}'))  
    audit(u['username'],'PUNCH','attendance',employee_no,attendance_date)  
    return {'status':'ok','employee_no':employee_no,'attendance_date':attendance_date,'worked_hours':hours}  
@app.post('/workforce/leave-accruals/run')  
def leave_accrual_run(request:Request,period:str=Form(...),leave_type_id:int=Form(...),amount_per_employee:float|None=Form(None)):  
    u=require(request,['admin','hr'])  
    lt=q('SELECT * FROM leave_types WHERE id=? AND active=1',(leave_type_id,),True)  
    if not lt: raise HTTPException(404,'Leave type not found')  
    amount=round(amount_per_employee if amount_per_employee is not None else lt['annual_entitlement']/12,4)  
    if q('SELECT id FROM leave_accrual_runs WHERE period=? AND leave_type_id=?',(period,leave_type_id),True):  
        raise HTTPException(400,'This leave type has already been accrued for the selected period')  
    year=int(period[:4])  
    with conn() as c:  
        run=c.execute("INSERT INTO leave_accrual_runs(period,leave_type_id,amount_per_employee,run_by,status) VALUES(?,?,?,?,'Running')",(period,leave_type_id,amount,u['id'])).lastrowid  
        employees=c.execute("SELECT id FROM employees WHERE status='Active'").fetchall(); total=0  
        for emp in employees:  
            c.execute("INSERT INTO leave_balances(employee_id,leave_type_id,year,opening,accrued,used) VALUES(?,?,?,0,0,0) ON CONFLICT(employee_id,leave_type_id,year) DO NOTHING",(emp['id'],leave_type_id,year))  
            bal=c.execute('SELECT id FROM leave_balances WHERE employee_id=? AND leave_type_id=? AND year=?',(emp['id'],leave_type_id,year)).fetchone()[0]  
            c.execute('UPDATE leave_balances SET accrued=accrued+? WHERE id=?',(amount,bal))  
            c.execute('INSERT INTO leave_accrual_entries(run_id,employee_id,leave_balance_id,amount) VALUES(?,?,?,?)',(run,emp['id'],bal,amount)); total+=amount  
        c.execute("UPDATE leave_accrual_runs SET employees_processed=?,total_accrued=?,status='Completed' WHERE id=?",(len(employees),round(total,4),run))  
    audit(u['username'],'RUN','leave_accrual',run,f'{period}; amount={amount}')  
    return RedirectResponse('/workforce',303)  
@app.get('/api/v1/attendance')  
def api_attendance(request:Request):  
    u=require(request); where='' if u['role'] in WORKFORCE_MANAGER_ROLES else ' WHERE employee_id=?'; args=() if not where else (u['employee_id'],)  
    return q('SELECT * FROM attendance_records'+where+' ORDER BY attendance_date DESC,id DESC',args)  
@app.get('/api/v1/shifts')  
def api_shifts(request:Request): require(request); return q('SELECT * FROM shifts WHERE active=1 ORDER BY name')  
@app.get('/api/v1/timesheets')  
def api_timesheets(request:Request):  
    u=require(request); where='' if u['role'] in WORKFORCE_MANAGER_ROLES else ' WHERE employee_id=?'; args=() if not where else (u['employee_id'],)  
    return q('SELECT * FROM timesheets'+where+' ORDER BY week_start DESC,id DESC',args)

# ------------------------------- v0.4 Talent -------------------------------
TALENT_MANAGER_ROLES = ['admin','hr','manager']
GOAL_STATUSES = ['Draft','Active','At Risk','Completed','Cancelled']

def talent_context(u, cycle_id=None):
    broad=u['role'] in TALENT_MANAGER_ROLES
    employee_id=u.get('employee_id')
    cycles=q('SELECT * FROM performance_cycles ORDER BY start_date DESC,id DESC')
    if not cycle_id and cycles: cycle_id=next((x['id'] for x in cycles if x['status']=='Active'),cycles[0]['id'])
    goal_where='' if broad else ' WHERE g.employee_id=?'
    goal_args=() if broad else (employee_id,)
    participant_where='' if broad else ' WHERE p.employee_id=?'
    participant_args=() if broad else (employee_id,)
    return {
      'selected_cycle_id':cycle_id,
      'cycles':cycles,
      'employees':q("SELECT id,employee_no,first_name||' '||last_name name,manager_id FROM employees WHERE status='Active' ORDER BY first_name"),
      'participants':q("""SELECT p.*,c.name cycle,e.employee_no,e.first_name||' '||e.last_name employee,
        m.first_name||' '||m.last_name manager FROM cycle_participants p JOIN performance_cycles c ON c.id=p.cycle_id
        JOIN employees e ON e.id=p.employee_id LEFT JOIN employees m ON m.id=p.manager_employee_id"""+participant_where+' ORDER BY c.start_date DESC,e.first_name',participant_args),
      'goals':q("""SELECT g.*,c.name cycle,e.first_name||' '||e.last_name employee,p.title parent_goal
        FROM goals g LEFT JOIN performance_cycles c ON c.id=g.cycle_id JOIN employees e ON e.id=g.employee_id
        LEFT JOIN goals p ON p.id=g.parent_goal_id"""+goal_where+' ORDER BY g.due_date,g.id DESC',goal_args),
      'goal_updates':q("""SELECT u.*,g.title goal,e.first_name||' '||e.last_name employee FROM goal_updates u
        JOIN goals g ON g.id=u.goal_id JOIN employees e ON e.id=g.employee_id
        WHERE (? IN ('admin','hr','manager') OR g.employee_id=?) ORDER BY u.id DESC LIMIT 100""",(u['role'],employee_id)),
      'checkins':q("""SELECT x.*,c.name cycle,e.first_name||' '||e.last_name employee,m.first_name||' '||m.last_name manager
        FROM check_ins x LEFT JOIN performance_cycles c ON c.id=x.cycle_id JOIN employees e ON e.id=x.employee_id
        LEFT JOIN employees m ON m.id=x.manager_employee_id WHERE (? IN ('admin','hr','manager') OR x.employee_id=?)
        ORDER BY x.check_in_date DESC,x.id DESC""",(u['role'],employee_id)),
      'feedback_requests':q("""SELECT r.*,c.name cycle,s.first_name||' '||s.last_name subject,
        f.first_name||' '||f.last_name requested_from FROM feedback_requests r LEFT JOIN performance_cycles c ON c.id=r.cycle_id
        JOIN employees s ON s.id=r.subject_employee_id JOIN employees f ON f.id=r.requested_from_employee_id
        WHERE (? IN ('admin','hr') OR r.subject_employee_id=? OR r.requested_from_employee_id=? OR s.manager_id=?) ORDER BY r.id DESC""",(u['role'],employee_id,employee_id,employee_id)),
      'feedback_responses':q("""SELECT x.*,s.first_name||' '||s.last_name subject,
        CASE WHEN x.visibility='Anonymous Summary' THEN 'Anonymous' ELSE f.first_name||' '||f.last_name END author,r.relationship
        FROM feedback_responses x JOIN feedback_requests r ON r.id=x.request_id JOIN employees s ON s.id=r.subject_employee_id
        JOIN employees f ON f.id=r.requested_from_employee_id
        WHERE (? IN ('admin','hr') OR r.requested_from_employee_id=? OR s.manager_id=? OR (r.subject_employee_id=? AND x.visibility!='Manager Only'))
        ORDER BY x.id DESC""",(u['role'],employee_id,employee_id,employee_id)),
      'competencies':q('SELECT * FROM competencies WHERE active=1 ORDER BY category,name'),
      'employee_competencies':q("""SELECT x.*,e.first_name||' '||e.last_name employee,c.name competency,c.category
        FROM employee_competencies x JOIN employees e ON e.id=x.employee_id JOIN competencies c ON c.id=x.competency_id
        WHERE (? IN ('admin','hr','manager') OR x.employee_id=?) ORDER BY e.first_name,c.category,c.name""",(u['role'],employee_id)),
      'skills':q('SELECT * FROM skills WHERE active=1 ORDER BY category,name'),
      'employee_skills':q("""SELECT x.*,e.first_name||' '||e.last_name employee,s.name skill,s.category,
        v.first_name||' '||v.last_name verified_by FROM employee_skills x JOIN employees e ON e.id=x.employee_id
        JOIN skills s ON s.id=x.skill_id LEFT JOIN employees v ON v.id=x.verified_by_employee_id
        WHERE (? IN ('admin','hr','manager') OR x.employee_id=?) ORDER BY e.first_name,s.category,s.name""",(u['role'],employee_id)),
      'development_plans':q("""SELECT p.*,e.first_name||' '||e.last_name employee,c.name cycle,o.first_name||' '||o.last_name owner,
        (SELECT COUNT(*) FROM development_actions a WHERE a.plan_id=p.id) action_count,
        (SELECT COUNT(*) FROM development_actions a WHERE a.plan_id=p.id AND a.status='Completed') completed_actions
        FROM development_plans p JOIN employees e ON e.id=p.employee_id LEFT JOIN performance_cycles c ON c.id=p.cycle_id
        LEFT JOIN employees o ON o.id=p.owner_employee_id WHERE (? IN ('admin','hr','manager') OR p.employee_id=?) ORDER BY p.id DESC""",(u['role'],employee_id)),
      'development_actions':q("""SELECT a.*,p.title plan,e.first_name||' '||e.last_name employee FROM development_actions a
        JOIN development_plans p ON p.id=a.plan_id JOIN employees e ON e.id=p.employee_id
        WHERE (? IN ('admin','hr','manager') OR p.employee_id=?) ORDER BY a.due_date,a.id""",(u['role'],employee_id)),
      'calibration_sessions':q("""SELECT s.*,c.name cycle,d.name department,f.first_name||' '||f.last_name facilitator,
        (SELECT COUNT(*) FROM calibration_entries x WHERE x.session_id=s.id) entry_count
        FROM calibration_sessions s JOIN performance_cycles c ON c.id=s.cycle_id LEFT JOIN departments d ON d.id=s.department_id
        LEFT JOIN employees f ON f.id=s.facilitator_employee_id ORDER BY s.session_date DESC,s.id DESC"""),
      'calibration_entries':q("""SELECT x.*,s.name session,e.first_name||' '||e.last_name employee,c.name cycle
        FROM calibration_entries x JOIN calibration_sessions s ON s.id=x.session_id JOIN cycle_participants p ON p.id=x.participant_id
        JOIN employees e ON e.id=p.employee_id JOIN performance_cycles c ON c.id=p.cycle_id ORDER BY s.session_date DESC,e.first_name"""),
      'departments':q('SELECT * FROM departments WHERE active=1 ORDER BY name'),
      'goal_statuses':GOAL_STATUSES,
    }

@app.get('/talent',response_class=HTMLResponse)
def talent(request:Request,cycle_id:int|None=None):
    u=require(request)
    return page(request,'talent.html',**talent_context(u,cycle_id))

@app.post('/talent/cycles')
def cycle_create(request:Request,name:str=Form(...),cycle_type:str=Form('Annual'),start_date:str=Form(...),end_date:str=Form(...),goal_due_date:str=Form(''),self_review_due_date:str=Form(''),manager_review_due_date:str=Form(''),description:str=Form('')):
    u=require(request,['admin','hr'])
    cid=execq("""INSERT INTO performance_cycles(name,cycle_type,start_date,end_date,goal_due_date,self_review_due_date,manager_review_due_date,status,description,created_by)
      VALUES(?,?,?,?,?,?,?,'Draft',?,?)""",(name,cycle_type,start_date,end_date,goal_due_date or None,self_review_due_date or None,manager_review_due_date or None,description,u['id']))
    audit(u['username'],'CREATE','performance_cycle',cid,name)
    return RedirectResponse(f'/talent?cycle_id={cid}',303)

@app.post('/talent/cycles/{cid}/status')
def cycle_status(cid:int,request:Request,status:str=Form(...)):
    u=require(request,['admin','hr'])
    if status not in ['Draft','Active','Review','Calibration','Closed']: raise HTTPException(400,'Invalid cycle status')
    execq('UPDATE performance_cycles SET status=? WHERE id=?',(status,cid))
    audit(u['username'],'STATUS','performance_cycle',cid,status)
    return RedirectResponse(f'/talent?cycle_id={cid}',303)

@app.post('/talent/cycles/{cid}/participants')
def cycle_participant(cid:int,request:Request,employee_id:int=Form(...),manager_employee_id:int|None=Form(None)):
    u=require(request,['admin','hr'])
    if manager_employee_id is None:
        employee=q('SELECT manager_id FROM employees WHERE id=?',(employee_id,),True)
        manager_employee_id=employee['manager_id'] if employee else None
    pid=execq('INSERT INTO cycle_participants(cycle_id,employee_id,manager_employee_id) VALUES(?,?,?)',(cid,employee_id,manager_employee_id))
    audit(u['username'],'ADD','cycle_participant',pid,f'employee={employee_id}')
    return RedirectResponse(f'/talent?cycle_id={cid}',303)

@app.post('/talent/participants/{pid}/review')
def participant_review(pid:int,request:Request,review_type:str=Form(...),rating:float|None=Form(None),summary:str=Form('')):
    u=require(request)
    participant=q('SELECT * FROM cycle_participants WHERE id=?',(pid,),True)
    if not participant: raise HTTPException(404,'Participant not found')
    if review_type=='self':
        if u['role']=='employee' and participant['employee_id']!=u['employee_id']: raise HTTPException(403)
        execq("UPDATE cycle_participants SET overall_self_rating=?,self_summary=?,status='Self Review Submitted',submitted_at=CURRENT_TIMESTAMP WHERE id=?",(rating,summary,pid))
    elif review_type=='manager':
        require(request,TALENT_MANAGER_ROLES)
        execq("UPDATE cycle_participants SET overall_manager_rating=?,manager_summary=?,status='Manager Review Submitted',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(rating,summary,pid))
    elif review_type=='final':
        require(request,['admin','hr'])
        execq("UPDATE cycle_participants SET final_rating=?,final_summary=?,status='Finalized',finalized_at=CURRENT_TIMESTAMP WHERE id=?",(rating,summary,pid))
    else: raise HTTPException(400,'Invalid review type')
    audit(u['username'],'REVIEW','cycle_participant',pid,review_type)
    return RedirectResponse(f"/talent?cycle_id={participant['cycle_id']}",303)

@app.post('/talent/goals')
def goal_create(request:Request,cycle_id:int|None=Form(None),employee_id:int=Form(...),parent_goal_id:int|None=Form(None),title:str=Form(...),description:str=Form(''),goal_type:str=Form('Individual'),metric_name:str=Form(''),baseline:float|None=Form(None),target_value:float|None=Form(None),unit:str=Form(''),weight:float=Form(0),start_date:str=Form(''),due_date:str=Form('')):
    u=require(request)
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)
    gid=execq("""INSERT INTO goals(cycle_id,employee_id,parent_goal_id,title,description,goal_type,metric_name,baseline,target_value,unit,weight,start_date,due_date,status,created_by)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'Draft',?)""",(cycle_id,employee_id,parent_goal_id,title,description,goal_type,metric_name,baseline,target_value,unit,weight,start_date or None,due_date or None,u['id']))
    audit(u['username'],'CREATE','goal',gid,title)
    return RedirectResponse(f'/talent?cycle_id={cycle_id or ""}',303)

@app.post('/talent/goals/{gid}/update')
def goal_update(gid:int,request:Request,progress:float=Form(...),current_value:float|None=Form(None),status:str=Form('Active'),update_text:str=Form('')):
    u=require(request)
    goal=q('SELECT * FROM goals WHERE id=?',(gid,),True)
    if not goal: raise HTTPException(404,'Goal not found')
    if u['role']=='employee' and goal['employee_id']!=u['employee_id']: raise HTTPException(403)
    if status not in GOAL_STATUSES: raise HTTPException(400,'Invalid goal status')
    progress=max(0,min(100,progress))
    execq('UPDATE goals SET progress=?,current_value=COALESCE(?,current_value),status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(progress,current_value,status,gid))
    uid=execq('INSERT INTO goal_updates(goal_id,progress,current_value,status,update_text,updated_by) VALUES(?,?,?,?,?,?)',(gid,progress,current_value,status,update_text,u['id']))
    audit(u['username'],'UPDATE','goal',gid,f'{progress}%; {status}')
    return RedirectResponse(f"/talent?cycle_id={goal['cycle_id'] or ''}",303)

@app.post('/talent/check-ins')
def checkin_create(request:Request,cycle_id:int|None=Form(None),employee_id:int=Form(...),manager_employee_id:int|None=Form(None),check_in_date:str=Form(...),accomplishments:str=Form(''),blockers:str=Form(''),support_needed:str=Form(''),next_steps:str=Form(''),employee_sentiment:str=Form('Neutral'),manager_notes:str=Form('')):
    u=require(request)
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)
    status='Completed' if accomplishments or manager_notes else 'Planned'
    cid=execq("""INSERT INTO check_ins(cycle_id,employee_id,manager_employee_id,check_in_date,accomplishments,blockers,support_needed,next_steps,employee_sentiment,manager_notes,status,completed_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ?='Completed' THEN CURRENT_TIMESTAMP ELSE NULL END)""",(cycle_id,employee_id,manager_employee_id,check_in_date,accomplishments,blockers,support_needed,next_steps,employee_sentiment,manager_notes,status,status))
    audit(u['username'],'CREATE','check_in',cid,check_in_date)
    return RedirectResponse(f'/talent?cycle_id={cycle_id or ""}',303)

@app.post('/talent/feedback/requests')
def feedback_request(request:Request,cycle_id:int|None=Form(None),subject_employee_id:int=Form(...),requested_from_employee_id:int=Form(...),relationship:str=Form('Peer'),due_date:str=Form(''),message:str=Form('')):
    u=require(request)
    if u['role']=='employee' and subject_employee_id!=u['employee_id']: raise HTTPException(403)
    rid=execq('INSERT INTO feedback_requests(cycle_id,subject_employee_id,requested_from_employee_id,requested_by,relationship,due_date,message) VALUES(?,?,?,?,?,?,?)',(cycle_id,subject_employee_id,requested_from_employee_id,u['id'],relationship,due_date or None,message))
    audit(u['username'],'REQUEST','feedback',rid,relationship)
    return RedirectResponse(f'/talent?cycle_id={cycle_id or ""}',303)

@app.post('/talent/feedback/{rid}/respond')
def feedback_respond(rid:int,request:Request,strengths:str=Form(...),improvements:str=Form(...),examples:str=Form(''),rating:float|None=Form(None),visibility:str=Form('Manager Only')):
    u=require(request)
    req=q('SELECT * FROM feedback_requests WHERE id=?',(rid,),True)
    if not req: raise HTTPException(404,'Feedback request not found')
    if u['role']=='employee' and req['requested_from_employee_id']!=u['employee_id']: raise HTTPException(403)
    fid=execq('INSERT INTO feedback_responses(request_id,strengths,improvements,examples,rating,visibility) VALUES(?,?,?,?,?,?)',(rid,strengths,improvements,examples,rating,visibility))
    execq("UPDATE feedback_requests SET status='Completed',responded_at=CURRENT_TIMESTAMP WHERE id=?",(rid,))
    audit(u['username'],'RESPOND','feedback',rid,visibility)
    return RedirectResponse(f"/talent?cycle_id={req['cycle_id'] or ''}",303)

@app.post('/talent/competencies')
def competency_create(request:Request,name:str=Form(...),category:str=Form('Core'),description:str=Form('')):
    u=require(request,['admin','hr'])
    cid=execq('INSERT INTO competencies(name,category,description) VALUES(?,?,?)',(name,category,description))
    audit(u['username'],'CREATE','competency',cid,name)
    return RedirectResponse('/talent',303)

@app.post('/talent/competencies/assess')
def competency_assess(request:Request,employee_id:int=Form(...),competency_id:int=Form(...),self_level:int|None=Form(None),manager_level:int|None=Form(None),evidence:str=Form('')):
    u=require(request)
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)
    if u['role']=='employee': manager_level=None
    execq("""INSERT INTO employee_competencies(employee_id,competency_id,self_level,manager_level,assessed_on,evidence) VALUES(?,?,?,?,?,?)
      ON CONFLICT(employee_id,competency_id) DO UPDATE SET self_level=COALESCE(excluded.self_level,self_level),manager_level=COALESCE(excluded.manager_level,manager_level),assessed_on=excluded.assessed_on,evidence=excluded.evidence""",(employee_id,competency_id,self_level,manager_level,date.today().isoformat(),evidence))
    audit(u['username'],'ASSESS','competency',competency_id,f'employee={employee_id}')
    return RedirectResponse('/talent',303)

@app.post('/talent/skills')
def skill_create(request:Request,name:str=Form(...),category:str=Form('Technical'),description:str=Form('')):
    u=require(request,['admin','hr'])
    sid=execq('INSERT INTO skills(name,category,description) VALUES(?,?,?)',(name,category,description))
    audit(u['username'],'CREATE','skill',sid,name)
    return RedirectResponse('/talent',303)

@app.post('/talent/skills/assess')
def skill_assess(request:Request,employee_id:int=Form(...),skill_id:int=Form(...),proficiency:int=Form(1),interest:int=Form(0),years_experience:float=Form(0),evidence:str=Form(''),verify:int=Form(0)):
    u=require(request)
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)
    verifier=u['employee_id'] if verify and u['role'] in TALENT_MANAGER_ROLES else None
    execq("""INSERT INTO employee_skills(employee_id,skill_id,proficiency,interest,years_experience,evidence,verified_by_employee_id,verified_at) VALUES(?,?,?,?,?,?,?,CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE NULL END)
      ON CONFLICT(employee_id,skill_id) DO UPDATE SET proficiency=excluded.proficiency,interest=excluded.interest,years_experience=excluded.years_experience,evidence=excluded.evidence,
      verified_by_employee_id=COALESCE(excluded.verified_by_employee_id,verified_by_employee_id),verified_at=CASE WHEN excluded.verified_by_employee_id IS NOT NULL THEN CURRENT_TIMESTAMP ELSE verified_at END""",
      (employee_id,skill_id,max(1,min(5,proficiency)),int(bool(interest)),max(0,years_experience),evidence,verifier,verifier))
    audit(u['username'],'ASSESS','skill',skill_id,f'employee={employee_id}')
    return RedirectResponse('/talent',303)

@app.post('/talent/development-plans')
def development_plan_create(request:Request,employee_id:int=Form(...),cycle_id:int|None=Form(None),title:str=Form(...),career_goal:str=Form(''),target_date:str=Form(''),owner_employee_id:int|None=Form(None)):
    u=require(request)
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)
    pid=execq('INSERT INTO development_plans(employee_id,cycle_id,title,career_goal,target_date,status,owner_employee_id) VALUES(?,?,?,?,?,\'Active\',?)',(employee_id,cycle_id,title,career_goal,target_date or None,owner_employee_id))
    audit(u['username'],'CREATE','development_plan',pid,title)
    return RedirectResponse(f'/talent?cycle_id={cycle_id or ""}',303)

@app.post('/talent/development-plans/{pid}/actions')
def development_action_create(pid:int,request:Request,action_type:str=Form('Learning'),title:str=Form(...),description:str=Form(''),due_date:str=Form(''),success_measure:str=Form('')):
    u=require(request)
    plan=q('SELECT * FROM development_plans WHERE id=?',(pid,),True)
    if not plan: raise HTTPException(404,'Development plan not found')
    if u['role']=='employee' and plan['employee_id']!=u['employee_id']: raise HTTPException(403)
    aid=execq('INSERT INTO development_actions(plan_id,action_type,title,description,due_date,success_measure) VALUES(?,?,?,?,?,?)',(pid,action_type,title,description,due_date or None,success_measure))
    audit(u['username'],'CREATE','development_action',aid,title)
    return RedirectResponse(f"/talent?cycle_id={plan['cycle_id'] or ''}",303)

@app.post('/talent/development-actions/{aid}/status')
def development_action_status(aid:int,request:Request,status:str=Form(...),progress_notes:str=Form('')):
    u=require(request)
    action=q('SELECT a.*,p.employee_id,p.cycle_id FROM development_actions a JOIN development_plans p ON p.id=a.plan_id WHERE a.id=?',(aid,),True)
    if not action: raise HTTPException(404,'Development action not found')
    if u['role']=='employee' and action['employee_id']!=u['employee_id']: raise HTTPException(403)
    if status not in ['Not Started','In Progress','Blocked','Completed','Cancelled']: raise HTTPException(400,'Invalid action status')
    execq("UPDATE development_actions SET status=?,progress_notes=?,completed_at=CASE WHEN ?='Completed' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",(status,progress_notes,status,aid))
    remaining=q("SELECT COUNT(*) n FROM development_actions WHERE plan_id=? AND status!='Completed'",(action['plan_id'],),True)['n']
    if remaining==0: execq("UPDATE development_plans SET status='Completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",(action['plan_id'],))
    audit(u['username'],'STATUS','development_action',aid,status)
    return RedirectResponse(f"/talent?cycle_id={action['cycle_id'] or ''}",303)

@app.post('/talent/calibration/sessions')
def calibration_session_create(request:Request,cycle_id:int=Form(...),name:str=Form(...),department_id:int|None=Form(None),session_date:str=Form(...),facilitator_employee_id:int|None=Form(None),notes:str=Form('')):
    u=require(request,['admin','hr'])
    sid=execq('INSERT INTO calibration_sessions(cycle_id,name,department_id,session_date,facilitator_employee_id,status,notes) VALUES(?,?,?,?,?,\'Planned\',?)',(cycle_id,name,department_id,session_date,facilitator_employee_id,notes))
    audit(u['username'],'CREATE','calibration_session',sid,name)
    return RedirectResponse(f'/talent?cycle_id={cycle_id}',303)

@app.post('/talent/calibration/{sid}/entries')
def calibration_entry(sid:int,request:Request,participant_id:int=Form(...),proposed_rating:float|None=Form(None),calibrated_rating:float|None=Form(None),performance_bucket:str=Form(''),potential_bucket:str=Form(''),rationale:str=Form(''),risk_flag:str=Form('')):
    u=require(request,['admin','hr'])
    session=q('SELECT * FROM calibration_sessions WHERE id=?',(sid,),True)
    if not session: raise HTTPException(404,'Calibration session not found')
    execq("""INSERT INTO calibration_entries(session_id,participant_id,proposed_rating,calibrated_rating,performance_bucket,potential_bucket,rationale,risk_flag)
      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(session_id,participant_id) DO UPDATE SET proposed_rating=excluded.proposed_rating,
      calibrated_rating=excluded.calibrated_rating,performance_bucket=excluded.performance_bucket,potential_bucket=excluded.potential_bucket,
      rationale=excluded.rationale,risk_flag=excluded.risk_flag,updated_at=CURRENT_TIMESTAMP""",(sid,participant_id,proposed_rating,calibrated_rating,performance_bucket,potential_bucket,rationale,risk_flag))
    if calibrated_rating is not None:
        execq('UPDATE cycle_participants SET final_rating=?,status=CASE WHEN status=\'Finalized\' THEN status ELSE \'Calibrated\' END WHERE id=?',(calibrated_rating,participant_id))
    execq("UPDATE calibration_sessions SET status='In Progress' WHERE id=? AND status='Planned'",(sid,))
    audit(u['username'],'CALIBRATE','cycle_participant',participant_id,f'rating={calibrated_rating}')
    return RedirectResponse(f"/talent?cycle_id={session['cycle_id']}",303)

@app.post('/talent/calibration/{sid}/status')
def calibration_status(sid:int,request:Request,status:str=Form(...)):
    u=require(request,['admin','hr'])
    if status not in ['Planned','In Progress','Completed','Cancelled']: raise HTTPException(400,'Invalid calibration status')
    execq('UPDATE calibration_sessions SET status=? WHERE id=?',(status,sid))
    session=q('SELECT cycle_id FROM calibration_sessions WHERE id=?',(sid,),True)
    audit(u['username'],'STATUS','calibration_session',sid,status)
    return RedirectResponse(f"/talent?cycle_id={session['cycle_id'] if session else ''}",303)

@app.get('/api/v1/performance-cycles')
def api_performance_cycles(request:Request): require(request); return q('SELECT * FROM performance_cycles ORDER BY start_date DESC,id DESC')

@app.get('/api/v1/goals')
def api_goals(request:Request):
    u=require(request); where='' if u['role'] in TALENT_MANAGER_ROLES else ' WHERE employee_id=?'; args=() if not where else (u['employee_id'],)
    return q('SELECT * FROM goals'+where+' ORDER BY due_date,id DESC',args)

@app.get('/api/v1/feedback-requests')
def api_feedback_requests(request:Request):
    u=require(request); where='' if u['role'] in ['admin','hr'] else ' WHERE subject_employee_id=? OR requested_from_employee_id=?'; args=() if not where else (u['employee_id'],u['employee_id'])
    return q('SELECT * FROM feedback_requests'+where+' ORDER BY id DESC',args)

@app.get('/api/v1/skills')
def api_skills(request:Request): require(request); return q('SELECT * FROM skills WHERE active=1 ORDER BY category,name')

@app.get('/api/v1/development-plans')
def api_development_plans(request:Request):
    u=require(request); where='' if u['role'] in TALENT_MANAGER_ROLES else ' WHERE employee_id=?'; args=() if not where else (u['employee_id'],)
    return q('SELECT * FROM development_plans'+where+' ORDER BY id DESC',args)

# -------------------------- v0.5 India payroll beta -------------------------
PAYROLL_ROLES = ['admin','hr','payroll']

def money(value):
    return float(Decimal(str(value or 0)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))

def validated_statutory_rule(rule_name, on_date):
    return q("""SELECT * FROM statutory_rule_versions WHERE rule_name=? AND validation_status='Validated'
      AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) ORDER BY effective_from DESC LIMIT 1""",(rule_name,on_date,on_date),True)

def payroll_context(u):
    own_filter='' if u['role'] in PAYROLL_ROLES else ' WHERE e.id=?'
    own_args=() if not own_filter else (u['employee_id'],)
    return {
      'components':q('SELECT * FROM pay_components WHERE active=1 ORDER BY display_order,code'),
      'statutory_rules':q('SELECT * FROM statutory_rule_versions ORDER BY rule_name,effective_from DESC'),
      'structures':q("""SELECT s.*,(SELECT COUNT(*) FROM salary_structure_lines l WHERE l.structure_id=s.id) line_count
        FROM salary_structures s ORDER BY s.name"""),
      'structure_lines':q("""SELECT l.*,s.name structure,c.code,c.name component,c.component_type,c.statutory
        FROM salary_structure_lines l JOIN salary_structures s ON s.id=l.structure_id JOIN pay_components c ON c.id=l.component_id
        ORDER BY s.name,c.display_order,c.code"""),
      'employees':q("SELECT id,employee_no,first_name||' '||last_name name FROM employees WHERE status='Active' ORDER BY first_name"),
      'compensations':q("""SELECT x.*,e.employee_no,e.first_name||' '||e.last_name employee,s.name structure
        FROM employee_compensation x JOIN employees e ON e.id=x.employee_id JOIN salary_structures s ON s.id=x.structure_id"""+own_filter+' ORDER BY x.effective_from DESC,e.first_name',own_args),
      'periods':q('SELECT * FROM payroll_periods ORDER BY start_date DESC,id DESC'),
      'inputs':q("""SELECT i.*,p.period_code,e.first_name||' '||e.last_name employee,c.code,c.name component,u.username approved_by_name
        FROM payroll_inputs i JOIN payroll_periods p ON p.id=i.period_id JOIN employees e ON e.id=i.employee_id
        JOIN pay_components c ON c.id=i.component_id LEFT JOIN users u ON u.id=i.approved_by ORDER BY i.id DESC"""),
      'attendance_inputs':q("""SELECT a.*,p.period_code,e.first_name||' '||e.last_name employee FROM payroll_attendance_inputs a
        JOIN payroll_periods p ON p.id=a.period_id JOIN employees e ON e.id=a.employee_id ORDER BY p.start_date DESC,e.first_name"""),
      'claims':q("""SELECT r.*,e.first_name||' '||e.last_name employee,p.period_code,u.username approved_by_name
        FROM reimbursement_claims r JOIN employees e ON e.id=r.employee_id LEFT JOIN payroll_periods p ON p.id=r.period_id
        LEFT JOIN users u ON u.id=r.approved_by WHERE (? IN ('admin','hr','payroll') OR r.employee_id=?) ORDER BY r.id DESC""",(u['role'],u['employee_id'])),
      'runs':q("""SELECT r.*,p.period_code,p.pay_date,u.username created_by_name FROM payroll_runs r JOIN payroll_periods p ON p.id=r.period_id
        LEFT JOIN users u ON u.id=r.created_by ORDER BY p.start_date DESC,r.run_number DESC"""),
      'run_employees':q("""SELECT x.*,p.period_code,e.employee_no,e.first_name||' '||e.last_name employee,r.status run_status
        FROM payroll_run_employees x JOIN payroll_runs r ON r.id=x.run_id JOIN payroll_periods p ON p.id=r.period_id
        JOIN employees e ON e.id=x.employee_id WHERE (? IN ('admin','hr','payroll') OR x.employee_id=?) ORDER BY r.id DESC,e.first_name""",(u['role'],u['employee_id'])),
      'exports':q("""SELECT x.*,p.period_code,r.run_number,u.username exported_by_name FROM payroll_accounting_exports x
        JOIN payroll_runs r ON r.id=x.run_id JOIN payroll_periods p ON p.id=r.period_id LEFT JOIN users u ON u.id=x.exported_by ORDER BY x.id DESC"""),
    }

@app.get('/payroll',response_class=HTMLResponse)
def payroll(request:Request):
    u=require(request)
    return page(request,'payroll.html',**payroll_context(u))

@app.post('/payroll/components')
def payroll_component_create(request:Request,code:str=Form(...),name:str=Form(...),component_type:str=Form(...),calculation_type:str=Form('Fixed'),base_component_code:str=Form(''),default_rate:float|None=Form(None),taxable:int=Form(0),statutory:int=Form(0),statutory_rule_name:str=Form(''),gl_account:str=Form(''),liability_account:str=Form(''),display_order:int=Form(0)):
    u=require(request,PAYROLL_ROLES)
    cid=execq('INSERT INTO pay_components(code,name,component_type,calculation_type,base_component_code,default_rate,taxable,statutory,statutory_rule_name,gl_account,liability_account,display_order) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(code.upper(),name,component_type,calculation_type,base_component_code.upper() or None,default_rate,int(bool(taxable)),int(bool(statutory)),statutory_rule_name or None,gl_account or None,liability_account or None,display_order))
    audit(u['username'],'CREATE','pay_component',cid,code)
    return RedirectResponse('/payroll',303)

@app.post('/payroll/statutory-rules')
def statutory_rule_create(request:Request,rule_name:str=Form(...),jurisdiction:str=Form('India'),effective_from:str=Form(...),effective_to:str=Form(''),parameters_json:str=Form(...),validation_status:str=Form('Draft'),validated_by:str=Form(''),notes:str=Form('')):
    u=require(request,PAYROLL_ROLES)
    try: json.loads(parameters_json)
    except json.JSONDecodeError: raise HTTPException(400,'Parameters must be valid JSON')
    if validation_status=='Validated' and not validated_by: raise HTTPException(400,'Validated rules require a qualified validator name')
    rid=execq('INSERT INTO statutory_rule_versions(rule_name,jurisdiction,effective_from,effective_to,parameters_json,validation_status,validated_by,validated_at,notes) VALUES(?,?,?,?,?,?,?,CASE WHEN ?=\'Validated\' THEN CURRENT_TIMESTAMP ELSE NULL END,?)',(rule_name,jurisdiction,effective_from,effective_to or None,parameters_json,validation_status,validated_by or None,validation_status,notes))
    audit(u['username'],'CREATE','statutory_rule',rid,f'{rule_name}; {validation_status}')
    return RedirectResponse('/payroll',303)

@app.post('/payroll/structures')
def salary_structure_create(request:Request,name:str=Form(...),code:str=Form(...),description:str=Form('')):
    u=require(request,PAYROLL_ROLES)
    sid=execq('INSERT INTO salary_structures(name,code,description) VALUES(?,?,?)',(name,code.upper(),description))
    audit(u['username'],'CREATE','salary_structure',sid,code)
    return RedirectResponse('/payroll',303)

@app.post('/payroll/structures/{sid}/lines')
def salary_structure_line(sid:int,request:Request,component_id:int=Form(...),calculation_type:str=Form('Fixed'),amount:float|None=Form(None),rate:float|None=Form(None),base_component_code:str=Form(''),employer_cost:int=Form(0)):
    u=require(request,PAYROLL_ROLES)
    lid=execq('INSERT INTO salary_structure_lines(structure_id,component_id,calculation_type,amount,rate,base_component_code,employer_cost) VALUES(?,?,?,?,?,?,?)',(sid,component_id,calculation_type,amount,rate,base_component_code.upper() or None,int(bool(employer_cost))))
    audit(u['username'],'CREATE','salary_structure_line',lid,f'structure={sid}; component={component_id}')
    return RedirectResponse('/payroll',303)

@app.post('/payroll/compensation')
def compensation_assign(request:Request,employee_id:int=Form(...),structure_id:int=Form(...),annual_ctc:float=Form(...),effective_from:str=Form(...),effective_to:str=Form('')):
    u=require(request,PAYROLL_ROLES)
    cid=execq('INSERT INTO employee_compensation(employee_id,structure_id,annual_ctc,effective_from,effective_to) VALUES(?,?,?,?,?)',(employee_id,structure_id,money(annual_ctc),effective_from,effective_to or None))
    audit(u['username'],'ASSIGN','employee_compensation',cid,f'employee={employee_id}; annual_ctc={annual_ctc}')
    return RedirectResponse('/payroll',303)

@app.post('/payroll/periods')
def payroll_period_create(request:Request,period_code:str=Form(...),start_date:str=Form(...),end_date:str=Form(...),pay_date:str=Form(...),working_days:float=Form(...)):
    u=require(request,PAYROLL_ROLES)
    pid=execq('INSERT INTO payroll_periods(period_code,start_date,end_date,pay_date,working_days) VALUES(?,?,?,?,?)',(period_code,start_date,end_date,pay_date,working_days))
    audit(u['username'],'CREATE','payroll_period',pid,period_code)
    return RedirectResponse('/payroll',303)

@app.post('/payroll/inputs')
def payroll_input_create(request:Request,period_id:int=Form(...),employee_id:int=Form(...),component_id:int=Form(...),input_type:str=Form('Variable'),amount:float=Form(...),description:str=Form('')):
    u=require(request,PAYROLL_ROLES)
    iid=execq('INSERT INTO payroll_inputs(period_id,employee_id,component_id,input_type,amount,description) VALUES(?,?,?,?,?,?)',(period_id,employee_id,component_id,input_type,money(amount),description))
    audit(u['username'],'CREATE','payroll_input',iid,input_type)
    return RedirectResponse('/payroll',303)

@app.post('/payroll/inputs/{iid}/approve')
def payroll_input_approve(iid:int,request:Request):
    u=require(request,PAYROLL_ROLES)
    execq('UPDATE payroll_inputs SET approved=1,approved_by=? WHERE id=?',(u['id'],iid))
    audit(u['username'],'APPROVE','payroll_input',iid)
    return RedirectResponse('/payroll',303)

@app.post('/payroll/attendance/sync')
def payroll_attendance_sync(request:Request,period_id:int=Form(...)):
    u=require(request,PAYROLL_ROLES)
    period=q('SELECT * FROM payroll_periods WHERE id=?',(period_id,),True)
    if not period: raise HTTPException(404,'Payroll period not found')
    employees=q("SELECT id FROM employees WHERE status='Active'")
    for emp in employees:
        stats=q("""SELECT COUNT(*) recorded_days,
          COALESCE(SUM(CASE WHEN status IN ('Present','Late','Remote') THEN 1 WHEN status='Half Day' THEN .5 ELSE 0 END),0) payable_days,
          COALESCE(SUM(overtime_hours),0) overtime_hours FROM attendance_records
          WHERE employee_id=? AND attendance_date>=? AND attendance_date<=?""",(emp['id'],period['start_date'],period['end_date']),True)
        payable=stats['payable_days'] if stats['recorded_days'] else period['working_days']
        lop=max(0,money(period['working_days']-payable))
        execq("""INSERT INTO payroll_attendance_inputs(period_id,employee_id,payable_days,loss_of_pay_days,overtime_hours,source)
          VALUES(?,?,?,?,?,'Workforce') ON CONFLICT(period_id,employee_id) DO UPDATE SET payable_days=excluded.payable_days,
          loss_of_pay_days=excluded.loss_of_pay_days,overtime_hours=excluded.overtime_hours,source=excluded.source""",(period_id,emp['id'],payable,lop,stats['overtime_hours']))
    audit(u['username'],'SYNC','payroll_attendance',period_id,f'{len(employees)} employees')
    return RedirectResponse('/payroll',303)

@app.post('/payroll/reimbursements')
def reimbursement_create(request:Request,employee_id:int=Form(...),period_id:int|None=Form(None),category:str=Form(...),amount:float=Form(...),description:str=Form('')):
    u=require(request)
    if u['role']=='employee' and employee_id!=u['employee_id']: raise HTTPException(403)
    rid=execq('INSERT INTO reimbursement_claims(employee_id,period_id,category,amount,description) VALUES(?,?,?,?,?)',(employee_id,period_id,category,money(amount),description))
    audit(u['username'],'CREATE','reimbursement',rid,category)
    return RedirectResponse('/payroll',303)

@app.post('/payroll/reimbursements/{rid}/decision')
def reimbursement_decision(rid:int,request:Request,decision:str=Form(...)):
    u=require(request,PAYROLL_ROLES)
    status='Approved' if decision=='approve' else 'Rejected'
    execq('UPDATE reimbursement_claims SET status=?,approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=? AND status=?',(status,u['id'],rid,'Pending'))
    audit(u['username'],status.upper(),'reimbursement',rid)
    return RedirectResponse('/payroll',303)

def calculate_employee_payroll(period, employee_id):
    compensation=q("""SELECT * FROM employee_compensation WHERE employee_id=? AND status='Active' AND effective_from<=?
      AND (effective_to IS NULL OR effective_to>=?) ORDER BY effective_from DESC LIMIT 1""",(employee_id,period['end_date'],period['start_date']),True)
    if not compensation: return None,[],['No active compensation assignment']
    attendance=q('SELECT * FROM payroll_attendance_inputs WHERE period_id=? AND employee_id=?',(period['id'],employee_id),True)
    payable_days=attendance['payable_days'] if attendance else period['working_days']
    ratio=Decimal(str(payable_days or 0))/Decimal(str(period['working_days'] or 1))
    structure_lines=q("""SELECT l.*,c.code,c.name component_name,c.component_type,c.taxable,c.statutory,c.statutory_rule_name,c.gl_account,c.liability_account
      FROM salary_structure_lines l JOIN pay_components c ON c.id=l.component_id WHERE l.structure_id=? AND c.active=1
      ORDER BY c.display_order,c.id""",(compensation['structure_id'],))
    overrides={x['component_id']:x for x in q('SELECT * FROM employee_component_overrides WHERE compensation_id=?',(compensation['id'],))}
    calculated={}; lines=[]; errors=[]
    annual_ctc=Decimal(str(compensation['annual_ctc'] or 0)); monthly_ctc=annual_ctc/Decimal('12')
    for line in structure_lines:
        rule=None
        if line['statutory']:
            rule=validated_statutory_rule(line['statutory_rule_name'],period['end_date'])
            if not rule:
                errors.append(f"Statutory component {line['code']} lacks a validated effective rule")
                continue
        ov=overrides.get(line['component_id']); calc=line['calculation_type']; amount=Decimal('0')
        configured_amount=ov['amount'] if ov and ov['amount'] is not None else line['amount']
        configured_rate=ov['rate'] if ov and ov['rate'] is not None else line['rate']
        base=line['base_component_code']
        if rule:
            try:
                params=json.loads(rule['parameters_json'])
                calc=params.get('calculation_type',calc)
                configured_amount=params.get('amount',configured_amount)
                configured_rate=params.get('rate',configured_rate)
                base=params.get('base_component_code',base)
            except (json.JSONDecodeError,TypeError,AttributeError):
                errors.append(f"Validated statutory rule {rule['rule_name']} contains invalid parameters")
                continue
        if calc=='Fixed': amount=Decimal(str(configured_amount or 0))
        elif calc=='Percent CTC': amount=monthly_ctc*Decimal(str(configured_rate or 0))/Decimal('100')
        elif calc=='Percent Component':
            amount=calculated.get(base,Decimal('0'))*Decimal(str(configured_rate or 0))/Decimal('100')
            if base not in calculated: errors.append(f"{line['code']} base component {base or 'not set'} was unavailable")
        elif calc=='Input': amount=Decimal('0')
        else: errors.append(f"Unsupported calculation type {calc} for {line['code']}")
        if line['component_type'] in ('Earning','Employer Cost') and calc!='Input': amount*=ratio
        amount=amount.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP); calculated[line['code']]=amount
        if amount: lines.append({**line,'amount':float(amount),'source':'Structure'})
    for inp in q("""SELECT i.*,c.code,c.name component_name,c.component_type,c.taxable,c.statutory,c.statutory_rule_name,c.gl_account,c.liability_account
      FROM payroll_inputs i JOIN pay_components c ON c.id=i.component_id WHERE i.period_id=? AND i.employee_id=? AND i.approved=1""",(period['id'],employee_id)):
        if inp['statutory'] and not validated_statutory_rule(inp['statutory_rule_name'],period['end_date']):
            errors.append(f"Input {inp['code']} lacks a validated effective statutory rule"); continue
        lines.append({**inp,'amount':money(inp['amount']),'source':inp['input_type']})
    reimb_component=q("SELECT * FROM pay_components WHERE code='REIMB' AND active=1",one=True)
    approved_claims=q("SELECT COALESCE(SUM(amount),0) amount FROM reimbursement_claims WHERE employee_id=? AND period_id=? AND status='Approved'",(employee_id,period['id']),True)['amount']
    if approved_claims and reimb_component: lines.append({**reimb_component,'component_name':reimb_component['name'],'amount':money(approved_claims),'source':'Approved reimbursement'})
    gross=sum(Decimal(str(x['amount'])) for x in lines if x['component_type'] in ('Earning','Reimbursement'))
    deductions=sum(Decimal(str(x['amount'])) for x in lines if x['component_type']=='Deduction')
    employer=sum(Decimal(str(x['amount'])) for x in lines if x['component_type']=='Employer Cost' or x.get('employer_cost'))
    net=gross-deductions
    previous=q("""SELECT x.net_pay FROM payroll_run_employees x JOIN payroll_runs r ON r.id=x.run_id JOIN payroll_periods p ON p.id=r.period_id
      WHERE x.employee_id=? AND r.status='Finalized' AND p.end_date<? ORDER BY p.end_date DESC,r.run_number DESC LIMIT 1""",(employee_id,period['start_date']),True)
    previous_net=Decimal(str(previous['net_pay'] if previous else 0)); variance=net-previous_net
    variance_pct=(variance/previous_net*Decimal('100')) if previous_net else Decimal('0')
    summary={'compensation_id':compensation['id'],'payable_days':float(payable_days),'loss_of_pay_days':float(attendance['loss_of_pay_days'] if attendance else 0),'gross_pay':money(gross),'deductions':money(deductions),'net_pay':money(net),'employer_cost':money(employer),'previous_net_pay':money(previous_net),'net_variance':money(variance),'variance_percent':money(variance_pct)}
    return summary,lines,errors

@app.post('/payroll/runs')
def payroll_run_create(request:Request,period_id:int=Form(...)):
    u=require(request,PAYROLL_ROLES)
    period=q('SELECT * FROM payroll_periods WHERE id=?',(period_id,),True)
    if not period: raise HTTPException(404,'Payroll period not found')
    run_no=(q('SELECT COALESCE(MAX(run_number),0)+1 n FROM payroll_runs WHERE period_id=?',(period_id,),True)['n'])
    employees=q("SELECT id FROM employees WHERE status='Active' ORDER BY id")
    all_results=[]; run_errors=[]
    for emp in employees:
        summary,lines,errors=calculate_employee_payroll(period,emp['id'])
        if summary is None: run_errors.extend([f"Employee {emp['id']}: {x}" for x in errors]); continue
        if errors: run_errors.extend([f"Employee {emp['id']}: {x}" for x in errors])
        all_results.append((emp['id'],summary,lines,errors))
    with conn() as c:
        cur=c.execute("INSERT INTO payroll_runs(period_id,run_number,status,created_by,validation_errors) VALUES(?,?,'Preview',?,?)",(period_id,run_no,u['id'],'\n'.join(run_errors) or None)); run_id=cur.lastrowid
        totals={'gross':0,'deductions':0,'net':0,'employer':0}
        for employee_id,summary,lines,errors in all_results:
            cur=c.execute("""INSERT INTO payroll_run_employees(run_id,employee_id,compensation_id,payable_days,loss_of_pay_days,gross_pay,deductions,net_pay,employer_cost,previous_net_pay,net_variance,variance_percent,status,errors)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(run_id,employee_id,summary['compensation_id'],summary['payable_days'],summary['loss_of_pay_days'],summary['gross_pay'],summary['deductions'],summary['net_pay'],summary['employer_cost'],summary['previous_net_pay'],summary['net_variance'],summary['variance_percent'],'Error' if errors else 'Calculated','; '.join(errors) or None)); reid=cur.lastrowid
            for line in lines:
                c.execute("""INSERT INTO payroll_run_lines(run_employee_id,component_id,component_code,component_name,component_type,amount,taxable,statutory,source,gl_account,liability_account)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(reid,line['component_id'] if 'component_id' in line else line['id'],line['code'],line.get('component_name') or line.get('name'),line['component_type'],line['amount'],line['taxable'],line['statutory'],line['source'],line.get('gl_account'),line.get('liability_account')))
            totals['gross']+=summary['gross_pay'];totals['deductions']+=summary['deductions'];totals['net']+=summary['net_pay'];totals['employer']+=summary['employer_cost']
        c.execute('UPDATE payroll_runs SET employee_count=?,gross_pay=?,total_deductions=?,net_pay=?,employer_cost=? WHERE id=?',(len(all_results),money(totals['gross']),money(totals['deductions']),money(totals['net']),money(totals['employer']),run_id))
        c.execute("UPDATE payroll_periods SET status='Processing' WHERE id=?",(period_id,))
    audit(u['username'],'PREVIEW','payroll_run',run_id,f'period={period_id}; employees={len(all_results)}')
    return RedirectResponse('/payroll',303)

@app.post('/payroll/runs/{run_id}/finalize')
def payroll_run_finalize(run_id:int,request:Request,variance_threshold:float=Form(20)):
    u=require(request,PAYROLL_ROLES)
    run=q('SELECT r.*,p.period_code,p.end_date FROM payroll_runs r JOIN payroll_periods p ON p.id=r.period_id WHERE r.id=?',(run_id,),True)
    if not run: raise HTTPException(404,'Payroll run not found')
    errors=[]
    if run['validation_errors']: errors.append(run['validation_errors'])
    large=q('SELECT COUNT(*) n FROM payroll_run_employees WHERE run_id=? AND previous_net_pay>0 AND ABS(variance_percent)>?',(run_id,variance_threshold),True)['n']
    if large: errors.append(f'{large} employee(s) exceed the {variance_threshold}% variance threshold')
    unmapped=q("""SELECT COUNT(*) n FROM payroll_run_lines l JOIN payroll_run_employees e ON e.id=l.run_employee_id WHERE e.run_id=? AND (
      (l.component_type IN ('Earning','Reimbursement') AND (l.gl_account IS NULL OR l.gl_account='')) OR
      (l.component_type='Deduction' AND COALESCE(l.liability_account,l.gl_account,'')='') OR
      (l.component_type='Employer Cost' AND ((l.gl_account IS NULL OR l.gl_account='') OR (l.liability_account IS NULL OR l.liability_account='')))
      )""",(run_id,),True)['n']
    if unmapped: errors.append(f'{unmapped} payroll line(s) lack a GL account mapping')
    if errors:
        execq('UPDATE payroll_runs SET validation_errors=? WHERE id=?',('\n'.join(errors),run_id))
        raise HTTPException(400,'Payroll run has unresolved validation errors')
    with conn() as c:
        c.execute("UPDATE payroll_runs SET status='Finalized',finalized_at=CURRENT_TIMESTAMP WHERE id=? AND status='Preview'",(run_id,))
        c.execute("UPDATE payroll_periods SET status='Finalized' WHERE id=?",(run['period_id'],))
        for row in c.execute('SELECT id,employee_id FROM payroll_run_employees WHERE run_id=?',(run_id,)).fetchall():
            c.execute("INSERT INTO payslips(run_employee_id,payslip_no,status,published_at) VALUES(?,?, 'Published',CURRENT_TIMESTAMP)",(row['id'],f"{run['period_code']}-{row['employee_id']:05d}"))
    audit(u['username'],'FINALIZE','payroll_run',run_id,f'variance threshold={variance_threshold}%')
    return RedirectResponse('/payroll',303)

@app.get('/payroll/payslips/{run_employee_id}',response_class=HTMLResponse)
def payslip_view(run_employee_id:int,request:Request):
    u=require(request)
    item=q("""SELECT x.*,e.employee_no,e.first_name||' '||e.last_name employee,e.work_email,p.period_code,p.start_date,p.end_date,p.pay_date,
      s.name structure,ps.payslip_no,ps.status payslip_status FROM payroll_run_employees x JOIN payroll_runs r ON r.id=x.run_id
      JOIN payroll_periods p ON p.id=r.period_id JOIN employees e ON e.id=x.employee_id LEFT JOIN employee_compensation c ON c.id=x.compensation_id
      LEFT JOIN salary_structures s ON s.id=c.structure_id LEFT JOIN payslips ps ON ps.run_employee_id=x.id WHERE x.id=?""",(run_employee_id,),True)
    if not item: raise HTTPException(404,'Payslip not found')
    if u['role'] not in PAYROLL_ROLES and item['employee_id']!=u['employee_id']: raise HTTPException(403)
    lines=q('SELECT * FROM payroll_run_lines WHERE run_employee_id=? ORDER BY component_type,component_code',(run_employee_id,))
    return page(request,'payslip.html',item=item,lines=lines)

@app.get('/payroll/runs/{run_id}/accounting.csv')
def payroll_accounting_export(run_id:int,request:Request):
    u=require(request,PAYROLL_ROLES)
    run=q('SELECT * FROM payroll_runs WHERE id=? AND status=?',(run_id,'Finalized'),True)
    if not run: raise HTTPException(400,'Only finalized payroll can be exported')
    detail=q("""SELECT l.component_name,l.component_type,l.gl_account,l.liability_account,SUM(l.amount) amount
      FROM payroll_run_lines l JOIN payroll_run_employees e ON e.id=l.run_employee_id WHERE e.run_id=?
      GROUP BY l.component_name,l.component_type,l.gl_account,l.liability_account ORDER BY l.component_type,l.component_name""",(run_id,))
    rows=[]
    for line in detail:
        if line['component_type'] in ('Earning','Reimbursement'):
            rows.append({'account':line['gl_account'] or 'UNMAPPED','description':line['component_name'],'debit':money(line['amount']),'credit':0})
        elif line['component_type']=='Deduction':
            rows.append({'account':line['liability_account'] or line['gl_account'] or 'UNMAPPED','description':line['component_name'],'debit':0,'credit':money(line['amount'])})
        elif line['component_type']=='Employer Cost':
            rows.append({'account':line['gl_account'] or 'UNMAPPED','description':line['component_name']+' expense','debit':money(line['amount']),'credit':0})
            rows.append({'account':line['liability_account'] or 'UNMAPPED','description':line['component_name']+' liability','debit':0,'credit':money(line['amount'])})
    net_account=q("SELECT value FROM settings WHERE key='payroll_net_pay_liability_account'",one=True)
    rows.append({'account':net_account['value'] if net_account else 'PAYROLL-NET-PAY','description':'Net pay liability','debit':0,'credit':run['net_pay']})
    debit=money(sum(x['debit'] for x in rows));credit=money(sum(x['credit'] for x in rows))
    balanced=abs(debit-credit)<0.01
    export_id=execq('INSERT INTO payroll_accounting_exports(run_id,total_debits,total_credits,balanced,exported_by) VALUES(?,?,?,?,?)',(run_id,debit,credit,int(balanced),u['id']))
    if not balanced: raise HTTPException(400,f'Accounting export is not balanced: debit {debit}, credit {credit}')
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=['account','description','debit','credit']);w.writeheader();w.writerows(rows)
    audit(u['username'],'EXPORT','payroll_accounting',export_id,f'debits={debit}; credits={credit}')
    return StreamingResponse(iter([out.getvalue()]),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename=payroll-accounting-run-{run_id}.csv'})

@app.get('/api/v1/payroll-periods')
def api_payroll_periods(request:Request): require(request,PAYROLL_ROLES); return q('SELECT * FROM payroll_periods ORDER BY start_date DESC')

@app.get('/api/v1/payroll-runs')
def api_payroll_runs(request:Request): require(request,PAYROLL_ROLES); return q('SELECT * FROM payroll_runs ORDER BY id DESC')

@app.get('/api/v1/payslips')
def api_payslips(request:Request):
    u=require(request); where='' if u['role'] in PAYROLL_ROLES else ' WHERE e.employee_id=?';args=() if not where else (u['employee_id'],)
    return q('SELECT p.*,e.employee_id,e.net_pay FROM payslips p JOIN payroll_run_employees e ON e.id=p.run_employee_id'+where+' ORDER BY p.id DESC',args)

@app.post('/payroll/components/{cid}/mapping')
def payroll_component_mapping(cid:int,request:Request,gl_account:str=Form(...),liability_account:str=Form(''),statutory_rule_name:str=Form('')):
    u=require(request,PAYROLL_ROLES)
    execq('UPDATE pay_components SET gl_account=?,liability_account=?,statutory_rule_name=CASE WHEN ?=\'\' THEN statutory_rule_name ELSE ? END WHERE id=?',(gl_account,liability_account or None,statutory_rule_name,statutory_rule_name,cid))
    audit(u['username'],'MAP','pay_component',cid,gl_account)
    return RedirectResponse('/payroll',303)

# ----------------------------- v0.6 Cloud beta -----------------------------
def require_platform_admin(request):
    u=require(request,['admin'])
    if current_tenant()!='local':raise HTTPException(403,'Cloud control is available only from the local control tenant')
    return u

def cloud_context():
    with control_conn() as c:
        return {'tenants':[dict(x) for x in c.execute('SELECT * FROM tenants ORDER BY name').fetchall()],'domains':[dict(x) for x in c.execute('SELECT d.*,t.name tenant FROM tenant_domains d JOIN tenants t ON t.id=d.tenant_id ORDER BY t.name,d.domain').fetchall()],'plans':[dict(x) for x in c.execute('SELECT * FROM subscription_plans WHERE active=1 ORDER BY employee_limit').fetchall()],'subscriptions':[dict(x) for x in c.execute('SELECT s.*,t.name tenant,p.name plan,p.employee_limit FROM tenant_subscriptions s JOIN tenants t ON t.id=s.tenant_id JOIN subscription_plans p ON p.id=s.plan_id ORDER BY t.name').fetchall()],'identity_providers':[dict(x) for x in c.execute('SELECT i.*,t.name tenant FROM identity_providers i JOIN tenants t ON t.id=i.tenant_id ORDER BY t.name,i.name').fetchall()],'scim_credentials':[dict(x) for x in c.execute('SELECT s.id,s.tenant_id,s.name,s.token_prefix,s.status,s.created_at,s.last_used_at,t.name tenant FROM scim_credentials s JOIN tenants t ON t.id=s.tenant_id ORDER BY t.name,s.name').fetchall()],'storage_configs':[dict(x) for x in c.execute('SELECT s.*,t.name tenant FROM storage_configurations s JOIN tenants t ON t.id=s.tenant_id ORDER BY t.name').fetchall()],'backup_policies':[dict(x) for x in c.execute('SELECT b.*,t.name tenant FROM backup_policies b JOIN tenants t ON t.id=b.tenant_id ORDER BY t.name').fetchall()],'backup_runs':[dict(x) for x in c.execute('SELECT b.*,t.name tenant FROM backup_runs b JOIN tenants t ON t.id=b.tenant_id ORDER BY b.id DESC LIMIT 50').fetchall()],'migrations':[dict(x) for x in c.execute('SELECT m.*,t.name tenant FROM migration_jobs m JOIN tenants t ON t.id=m.tenant_id ORDER BY m.id DESC').fetchall()],'checks':[dict(x) for x in c.execute('SELECT s.*,t.name tenant FROM service_checks s LEFT JOIN tenants t ON t.id=s.tenant_id ORDER BY s.id DESC LIMIT 100').fetchall()],'cloud_audit_rows':[dict(x) for x in c.execute('SELECT a.*,t.name tenant FROM cloud_audit a LEFT JOIN tenants t ON t.id=a.tenant_id ORDER BY a.id DESC LIMIT 100').fetchall()]}

@app.get('/cloud',response_class=HTMLResponse)
def cloud_console(request:Request): require_platform_admin(request);return page(request,'cloud.html',**cloud_context())

@app.post('/cloud/tenants')
def tenant_create(request:Request,name:str=Form(...),slug:str=Form(...),region:str=Form('india-central'),plan_id:int=Form(...),admin_username:str=Form('admin')):
    u=require_platform_admin(request);slug=clean_slug(slug)
    with control_conn() as c:
        cur=c.execute('INSERT INTO tenants(slug,name,region,storage_prefix) VALUES(?,?,?,?)',(slug,name,region,slug));tenant_id=cur.lastrowid
        c.execute("INSERT INTO tenant_memberships(tenant_id,username,role) VALUES(?,?,'owner')",(tenant_id,admin_username));c.execute("INSERT INTO tenant_subscriptions(tenant_id,plan_id,status,starts_on) VALUES(?,?,'Trial',?)",(tenant_id,plan_id,date.today().isoformat()));c.execute("INSERT INTO storage_configurations(tenant_id,backend,prefix,status) VALUES(?,'Local',?,'Active')",(tenant_id,slug));c.execute("INSERT INTO backup_policies(tenant_id,frequency,retention_days,pitr_enabled) VALUES(?,'Daily',30,0)",(tenant_id,))
    context=set_tenant(slug)
    try:
        init_db()
        if not q('SELECT id FROM users LIMIT 1',one=True):seed()
        if admin_username!='admin':execq("UPDATE users SET username=? WHERE username='admin'",(admin_username,))
    finally:reset_tenant(context)
    cloud_audit(u['username'],'CREATE','tenant',tenant_id,slug,tenant_id);r=RedirectResponse('/login',303);r.set_cookie('peawork_tenant',slug,httponly=True,samesite='lax');r.delete_cookie('peawork_session');return r

@app.post('/cloud/tenant/switch')
def tenant_switch(request:Request,slug:str=Form(...)):
    require_platform_admin(request);tenant=resolve_tenant(slug=slug)
    if not tenant:raise HTTPException(404,'Tenant not found')
    r=RedirectResponse('/login',303);r.set_cookie('peawork_tenant',slug,httponly=True,samesite='lax');r.delete_cookie('peawork_session');return r

@app.post('/cloud/domains')
def tenant_domain_create(request:Request,tenant_id:int=Form(...),domain:str=Form(...)):
    u=require_platform_admin(request);domain=domain.lower().strip();verification=secrets.token_urlsafe(18)
    with control_conn() as c:did=c.execute('INSERT INTO tenant_domains(tenant_id,domain,verification_token) VALUES(?,?,?)',(tenant_id,domain,verification)).lastrowid
    cloud_audit(u['username'],'CREATE','tenant_domain',did,domain,tenant_id);return RedirectResponse('/cloud',303)

@app.post('/cloud/domains/{did}/verify')
def tenant_domain_verify(did:int,request:Request,verification_token:str=Form(...)):
    u=require_platform_admin(request)
    with control_conn() as c:
        row=c.execute('SELECT * FROM tenant_domains WHERE id=?',(did,)).fetchone()
        if not row or not secrets.compare_digest(row['verification_token'],verification_token):raise HTTPException(400,'Domain verification token does not match')
        c.execute("UPDATE tenant_domains SET status='Verified',verified_at=CURRENT_TIMESTAMP,tls_status='Provisioning' WHERE id=?",(did,))
    cloud_audit(u['username'],'VERIFY','tenant_domain',did,row['domain'],row['tenant_id']);return RedirectResponse('/cloud',303)

@app.post('/cloud/subscriptions/{tenant_id}')
def subscription_update(tenant_id:int,request:Request,plan_id:int=Form(...),status:str=Form('Active'),employee_limit_override:int|None=Form(None),renews_on:str=Form('')):
    u=require_platform_admin(request)
    if status not in ['Trial','Active','Past Due','Suspended','Cancelled']:raise HTTPException(400,'Invalid subscription status')
    with control_conn() as c:c.execute("""INSERT INTO tenant_subscriptions(tenant_id,plan_id,status,starts_on,renews_on,employee_limit_override) VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET plan_id=excluded.plan_id,status=excluded.status,renews_on=excluded.renews_on,employee_limit_override=excluded.employee_limit_override""",(tenant_id,plan_id,status,date.today().isoformat(),renews_on or None,employee_limit_override))
    cloud_audit(u['username'],'UPDATE','subscription',tenant_id,status,tenant_id);return RedirectResponse('/cloud',303)

@app.post('/cloud/identity-providers')
def identity_provider_create(request:Request,tenant_id:int=Form(...),provider_type:str=Form(...),name:str=Form(...),issuer:str=Form(''),client_id:str=Form(''),metadata_url:str=Form(''),enabled:int=Form(0)):
    u=require_platform_admin(request)
    if provider_type not in ['OIDC','SAML']:raise HTTPException(400,'Provider must be OIDC or SAML')
    with control_conn() as c:iid=c.execute('INSERT INTO identity_providers(tenant_id,provider_type,name,issuer,client_id,metadata_url,enabled,configuration_status) VALUES(?,?,?,?,?,?,?,?)',(tenant_id,provider_type,name,issuer or None,client_id or None,metadata_url or None,int(bool(enabled)),'Configured' if issuer and (client_id or metadata_url) else 'Draft')).lastrowid
    cloud_audit(u['username'],'CREATE','identity_provider',iid,provider_type,tenant_id);return RedirectResponse('/cloud',303)

@app.post('/cloud/scim-tokens')
def scim_token_create(request:Request,tenant_id:int=Form(...),name:str=Form(...)):
    u=require_platform_admin(request);plain,digest,prefix=issue_scim_token()
    with control_conn() as c:cid=c.execute('INSERT INTO scim_credentials(tenant_id,name,token_hash,token_prefix) VALUES(?,?,?,?)',(tenant_id,name,digest,prefix)).lastrowid
    cloud_audit(u['username'],'CREATE','scim_credential',cid,prefix,tenant_id);return page(request,'scim_token.html',token=plain,token_name=name)

@app.post('/scim/v2/Users')
async def scim_user_create(request:Request):
    authorization=request.headers.get('authorization','')
    if not authorization.startswith('Bearer '):raise HTTPException(401,'SCIM bearer token required')
    digest=hash_token(authorization[7:]);payload=await request.json()
    with control_conn() as c:
        cred=c.execute("SELECT * FROM scim_credentials WHERE token_hash=? AND status='Active'",(digest,)).fetchone()
        if not cred:raise HTTPException(401,'Invalid SCIM token')
        tenant=c.execute('SELECT * FROM tenants WHERE id=? AND status=?',(cred['tenant_id'],'Active')).fetchone();c.execute('UPDATE scim_credentials SET last_used_at=CURRENT_TIMESTAMP WHERE id=?',(cred['id'],))
    username=(payload.get('userName') or '').strip()
    if not username:raise HTTPException(400,'userName is required')
    role=payload.get('role','employee')
    if role not in ['employee','manager','hr','recruiter','payroll']:raise HTTPException(400,'SCIM role is not allowed')
    active=1 if payload.get('active',True) else 0;context=set_tenant(tenant['slug'])
    try:
        init_db();existing=q('SELECT id FROM users WHERE username=?',(username,),True)
        if existing:execq('UPDATE users SET role=?,active=? WHERE id=?',(role,active,existing['id']));uid=existing['id']
        else:uid=execq('INSERT INTO users(username,password_hash,role,active) VALUES(?,?,?,?)',(username,hash_password(secrets.token_urlsafe(32)),role,active))
    finally:reset_tenant(context)
    cloud_audit('scim','PROVISION','user',uid,username,tenant['id']);return {'schemas':['urn:ietf:params:scim:schemas:core:2.0:User'],'id':str(uid),'userName':username,'active':bool(active)}

@app.post('/cloud/storage/{tenant_id}')
def storage_config_update(tenant_id:int,request:Request,backend:str=Form('Local'),bucket:str=Form(''),prefix:str=Form(''),region:str=Form(''),encryption_required:int=Form(1)):
    u=require_platform_admin(request)
    if backend not in ['Local','S3','Object Storage']:raise HTTPException(400,'Unsupported storage backend')
    status='Active' if backend=='Local' or bucket else 'Draft'
    with control_conn() as c:c.execute("""INSERT INTO storage_configurations(tenant_id,backend,bucket,prefix,region,encryption_required,status) VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET backend=excluded.backend,bucket=excluded.bucket,prefix=excluded.prefix,region=excluded.region,encryption_required=excluded.encryption_required,status=excluded.status""",(tenant_id,backend,bucket or None,prefix or None,region or None,int(bool(encryption_required)),status))
    cloud_audit(u['username'],'UPDATE','storage_configuration',tenant_id,backend,tenant_id);return RedirectResponse('/cloud',303)

@app.post('/cloud/backups/policies/{tenant_id}')
def backup_policy_update(tenant_id:int,request:Request,frequency:str=Form('Daily'),retention_days:int=Form(30),pitr_enabled:int=Form(0),rpo_minutes:int=Form(1440),rto_minutes:int=Form(240)):
    u=require_platform_admin(request)
    with control_conn() as c:c.execute("""INSERT INTO backup_policies(tenant_id,frequency,retention_days,pitr_enabled,rpo_minutes,rto_minutes,status) VALUES(?,?,?,?,?,?,'Active') ON CONFLICT(tenant_id) DO UPDATE SET frequency=excluded.frequency,retention_days=excluded.retention_days,pitr_enabled=excluded.pitr_enabled,rpo_minutes=excluded.rpo_minutes,rto_minutes=excluded.rto_minutes,status='Active'""",(tenant_id,frequency,max(1,retention_days),int(bool(pitr_enabled)),max(1,rpo_minutes),max(1,rto_minutes)))
    cloud_audit(u['username'],'UPDATE','backup_policy',tenant_id,frequency,tenant_id);return RedirectResponse('/cloud',303)

@app.post('/cloud/backups/run/{tenant_id}')
def backup_run(tenant_id:int,request:Request,backup_type:str=Form('Full')):
    u=require_platform_admin(request)
    with control_conn() as c:
        tenant=c.execute('SELECT * FROM tenants WHERE id=?',(tenant_id,)).fetchone()
        if not tenant:raise HTTPException(404,'Tenant not found')
        run_id=c.execute("INSERT INTO backup_runs(tenant_id,backup_type,status) VALUES(?,?,'Running')",(tenant_id,backup_type)).lastrowid
    try:
        if os.getenv('PEAWORK_DATABASE_URL','').startswith('postgresql'):
            location='managed-postgresql://pitr-requested';size=0
        else:
            source=tenant_db_path(tenant['slug']);target=BASE/'backups'/tenant['slug']/(datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'.db');target.parent.mkdir(parents=True,exist_ok=True)
            if not source.exists():raise FileNotFoundError('Tenant database does not exist')
            src=sqlite3.connect(source);dst=sqlite3.connect(target)
            try:src.backup(dst)
            finally:src.close();dst.close()
            location=str(target);size=target.stat().st_size
        with control_conn() as c:c.execute("UPDATE backup_runs SET status='Completed',location=?,size_bytes=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(location,size,run_id))
        cloud_audit(u['username'],'RUN','backup',run_id,backup_type,tenant_id)
    except Exception as e:
        with control_conn() as c:c.execute("UPDATE backup_runs SET status='Failed',error=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(str(e),run_id))
        raise HTTPException(500,'Backup failed')
    return RedirectResponse('/cloud',303)

@app.post('/cloud/migrations')
def migration_create(request:Request,tenant_id:int=Form(...),source_path:str=Form(...)):
    u=require_platform_admin(request);source=Path(source_path).resolve();data_root=(BASE/'data').resolve()
    if source.suffix.lower()!='.db' or data_root not in source.parents:raise HTTPException(400,'Migration source must be a SQLite .db file inside the PeaWork data directory')
    if not source.exists():raise HTTPException(404,'Migration source database not found')
    source_conn=sqlite3.connect(source);source_conn.row_factory=sqlite3.Row
    try:
        tables=[x['name'] for x in source_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        counts={name:source_conn.execute(f'SELECT COUNT(*) n FROM "{name}"').fetchone()['n'] for name in tables}
        preflight={'tables':len(tables),'records':sum(counts.values()),'counts':counts,'source_bytes':source.stat().st_size}
    finally:source_conn.close()
    with control_conn() as c:mid=c.execute("INSERT INTO migration_jobs(tenant_id,source_path,status,preflight_json) VALUES(?,?,'Preflight Complete',?)",(tenant_id,str(source),json.dumps(preflight))).lastrowid
    cloud_audit(u['username'],'PREFLIGHT','migration',mid,f"{preflight['records']} records",tenant_id);return RedirectResponse('/cloud',303)

@app.post('/cloud/migrations/{mid}/run')
def migration_run(mid:int,request:Request,confirm:str=Form(...)):
    u=require_platform_admin(request)
    if confirm!='MIGRATE':raise HTTPException(400,'Type MIGRATE to confirm')
    with control_conn() as c:
        job=c.execute('SELECT m.*,t.slug FROM migration_jobs m JOIN tenants t ON t.id=m.tenant_id WHERE m.id=?',(mid,)).fetchone()
        if not job or job['status']!='Preflight Complete':raise HTTPException(400,'Migration is not ready')
        c.execute("UPDATE migration_jobs SET status='Running',started_at=CURRENT_TIMESTAMP WHERE id=?",(mid,))
    source=Path(job['source_path']);destination=tenant_db_path(job['slug'])
    try:
        if os.getenv('PEAWORK_DATABASE_URL','').startswith('postgresql'):
            src=sqlite3.connect(source);src.row_factory=sqlite3.Row;context=set_tenant(job['slug'])
            try:
                init_db();tables=[x['name'] for x in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
                with conn() as target:
                    for table in reversed(tables):target.execute(f'DELETE FROM "{table}"')
                    for table in tables:
                        columns=[x['name'] for x in src.execute(f'PRAGMA table_info("{table}")').fetchall()]
                        column_sql=','.join(f'"{x}"' for x in columns);placeholders=','.join('?' for _ in columns)
                        for row in src.execute(f'SELECT * FROM "{table}"').fetchall():target.execute(f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})',tuple(row[x] for x in columns))
                        if 'id' in columns:target.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'),COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM \"{table}\"")
            finally:src.close();reset_tenant(context)
        else:
            if source.resolve()==destination.resolve():raise ValueError('Source and destination databases are the same')
            destination.parent.mkdir(parents=True,exist_ok=True);src=sqlite3.connect(source);dst=sqlite3.connect(destination)
            try:src.backup(dst)
            finally:src.close();dst.close()
            context=set_tenant(job['slug'])
            try:init_db()
            finally:reset_tenant(context)
        info=json.loads(job['preflight_json']);records=info['records']
        with control_conn() as c:c.execute("UPDATE migration_jobs SET status='Completed',records_migrated=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(records,mid))
        cloud_audit(u['username'],'COMPLETE','migration',mid,f'{records} records',job['tenant_id'])
    except Exception as e:
        with control_conn() as c:c.execute("UPDATE migration_jobs SET status='Failed',error=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(str(e),mid))
        raise HTTPException(500,'Migration failed')
    return RedirectResponse('/cloud',303)

@app.post('/cloud/health-checks')
def cloud_health_checks(request:Request,tenant_id:int|None=Form(None)):
    u=require_platform_admin(request);checks=[];started=time.perf_counter()
    try:init_control_db();checks.append(('control_database','Healthy',(time.perf_counter()-started)*1000,'Control database reachable'))
    except Exception as e:checks.append(('control_database','Unhealthy',None,str(e)))
    tenants=[]
    with control_conn() as c:tenants=c.execute('SELECT * FROM tenants WHERE id=?',(tenant_id,)).fetchall() if tenant_id else c.execute("SELECT * FROM tenants WHERE status='Active'").fetchall()
    for tenant in tenants:
        token=set_tenant(tenant['slug']);tick=time.perf_counter()
        try:q('SELECT 1 ok',one=True);checks.append((f"tenant_database:{tenant['slug']}",'Healthy',(time.perf_counter()-tick)*1000,'Tenant database reachable'))
        except Exception as e:checks.append((f"tenant_database:{tenant['slug']}",'Unhealthy',None,str(e)))
        finally:reset_tenant(token)
    with control_conn() as c:
        for service,status,latency,details in checks:c.execute('INSERT INTO service_checks(tenant_id,service,status,latency_ms,details) VALUES(?,?,?,?,?)',(tenant_id,service,status,latency,details))
    cloud_audit(u['username'],'RUN','health_checks','',f'{len(checks)} checks',tenant_id);return RedirectResponse('/cloud',303)

@app.get('/metrics')
def metrics():
    init_control_db()
    with control_conn() as c:
        tenants=c.execute("SELECT COUNT(*) n FROM tenants WHERE status='Active'").fetchone()['n'];failed=c.execute("SELECT COUNT(*) n FROM backup_runs WHERE status='Failed'").fetchone()['n'];migrations=c.execute("SELECT COUNT(*) n FROM migration_jobs WHERE status IN ('Running','Failed')").fetchone()['n']
    body=f"peawork_active_tenants {tenants}\npeawork_failed_backups {failed}\npeawork_migration_attention {migrations}\n"
    return StreamingResponse(iter([body]),media_type='text/plain')

@app.get('/api/v1/cloud/tenants')
def api_cloud_tenants(request:Request): require_platform_admin(request);return cloud_context()['tenants']
@app.get('/api/v1/cloud/service-checks')
def api_cloud_checks(request:Request): require_platform_admin(request);return cloud_context()['checks']

@app.get('/cloud/return-control')
def cloud_return_control():
    r=RedirectResponse('/login',303);r.delete_cookie('peawork_tenant');r.delete_cookie('peawork_session');return r

# -------------------------- v1.0 Production readiness -----------------------
def active_tenant_record():return resolve_tenant(slug=current_tenant())
def readiness_context():
    with control_conn() as c:
        evidence=[dict(x) for x in c.execute('SELECT * FROM readiness_evidence ORDER BY category,control_code').fetchall()]
        return {'evidence':evidence,'release_ready':bool(evidence) and all(x['status']=='Verified' for x in evidence),'security_events':[dict(x) for x in c.execute('SELECT s.*,t.name tenant FROM security_events s LEFT JOIN tenants t ON t.id=s.tenant_id ORDER BY s.id DESC LIMIT 100').fetchall()],'dr_exercises':[dict(x) for x in c.execute('SELECT d.*,t.name tenant FROM dr_exercises d LEFT JOIN tenants t ON t.id=d.tenant_id ORDER BY d.exercise_date DESC,d.id DESC').fetchall()],'load_tests':[dict(x) for x in c.execute('SELECT l.*,t.name tenant FROM load_test_runs l LEFT JOIN tenants t ON t.id=l.tenant_id ORDER BY l.id DESC').fetchall()],'accessibility_findings':[dict(x) for x in c.execute('SELECT * FROM accessibility_findings ORDER BY CASE severity WHEN \'Critical\' THEN 1 WHEN \'High\' THEN 2 WHEN \'Medium\' THEN 3 ELSE 4 END,id DESC').fetchall()]}
def privacy_context(tenant_id):
    with control_conn() as c:
        return {'retention_policies':[dict(x) for x in c.execute('SELECT * FROM retention_policies WHERE tenant_id=? ORDER BY data_category',(tenant_id,)).fetchall()],'retention_runs':[dict(x) for x in c.execute('SELECT * FROM retention_runs WHERE tenant_id=? ORDER BY id DESC',(tenant_id,)).fetchall()],'privacy_requests':[dict(x) for x in c.execute('SELECT * FROM privacy_requests WHERE tenant_id=? ORDER BY id DESC',(tenant_id,)).fetchall()],'breaches':[dict(x) for x in c.execute('SELECT * FROM breach_incidents WHERE tenant_id=? ORDER BY id DESC',(tenant_id,)).fetchall()]}

@app.get('/readiness',response_class=HTMLResponse)
def readiness(request:Request):require_platform_admin(request);return page(request,'readiness.html',**readiness_context())

@app.post('/readiness/evidence/{eid}')
def readiness_evidence_update(eid:int,request:Request,status:str=Form(...),evidence_reference:str=Form(''),reviewed_by:str=Form(''),notes:str=Form('')):
    u=require_platform_admin(request)
    if status not in ['Pending','In Progress','Failed','Verified']:raise HTTPException(400,'Invalid evidence status')
    if status=='Verified' and (not evidence_reference or not reviewed_by):raise HTTPException(400,'Verified evidence requires a reference and named reviewer')
    with control_conn() as c:c.execute("UPDATE readiness_evidence SET status=?,evidence_reference=?,reviewed_by=?,reviewed_at=CASE WHEN ?='Verified' THEN CURRENT_TIMESTAMP ELSE NULL END,notes=? WHERE id=?",(status,evidence_reference or None,reviewed_by or None,status,notes,eid))
    cloud_audit(u['username'],'UPDATE','readiness_evidence',eid,status);return RedirectResponse('/readiness',303)

@app.post('/readiness/dr-exercises')
def dr_exercise_create(request:Request,tenant_id:int|None=Form(None),exercise_date:str=Form(...),scenario:str=Form(...),target_rpo_minutes:int=Form(...),target_rto_minutes:int=Form(...),actual_rpo_minutes:int|None=Form(None),actual_rto_minutes:int|None=Form(None),restore_verified:int=Form(0),evidence:str=Form(''),status:str=Form('Planned'),approved_by:str=Form('')):
    u=require_platform_admin(request)
    if status=='Completed' and (not restore_verified or not evidence or not approved_by):raise HTTPException(400,'Completed DR exercises require verified restore evidence and approver')
    with control_conn() as c:did=c.execute('INSERT INTO dr_exercises(tenant_id,exercise_date,scenario,target_rpo_minutes,target_rto_minutes,actual_rpo_minutes,actual_rto_minutes,restore_verified,evidence,status,approved_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(tenant_id,exercise_date,scenario,target_rpo_minutes,target_rto_minutes,actual_rpo_minutes,actual_rto_minutes,int(bool(restore_verified)),evidence,status,approved_by or None)).lastrowid
    cloud_audit(u['username'],'CREATE','dr_exercise',did,status,tenant_id);return RedirectResponse('/readiness',303)

@app.post('/readiness/load-tests')
def load_test_record(request:Request,tenant_id:int|None=Form(None),scenario:str=Form(...),concurrency:int=Form(...),duration_seconds:int=Form(...),requests_count:int=Form(...),errors:int=Form(...),p50_ms:float|None=Form(None),p95_ms:float|None=Form(None),p99_ms:float|None=Form(None),evidence:str=Form('')):
    u=require_platform_admin(request);status='Passed' if errors==0 and evidence else 'Review Required'
    with control_conn() as c:lid=c.execute('INSERT INTO load_test_runs(tenant_id,scenario,concurrency,duration_seconds,requests,errors,p50_ms,p95_ms,p99_ms,status,evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(tenant_id,scenario,concurrency,duration_seconds,requests_count,errors,p50_ms,p95_ms,p99_ms,status,evidence)).lastrowid
    cloud_audit(u['username'],'CREATE','load_test',lid,status,tenant_id);return RedirectResponse('/readiness',303)

@app.post('/readiness/accessibility')
def accessibility_finding_create(request:Request,page_name:str=Form(...),standard:str=Form('WCAG 2.2 AA'),finding:str=Form(...),severity:str=Form(...),evidence:str=Form(''),owner_role:str=Form('Product Engineering')):
    u=require_platform_admin(request)
    with control_conn() as c:fid=c.execute('INSERT INTO accessibility_findings(page_name,standard,finding,severity,evidence,owner_role) VALUES(?,?,?,?,?,?)',(page_name,standard,finding,severity,evidence,owner_role)).lastrowid
    cloud_audit(u['username'],'CREATE','accessibility_finding',fid,severity);return RedirectResponse('/readiness',303)

@app.post('/readiness/accessibility/{fid}/resolve')
def accessibility_finding_resolve(fid:int,request:Request,evidence:str=Form(...)):
    u=require_platform_admin(request)
    with control_conn() as c:c.execute("UPDATE accessibility_findings SET status='Resolved',evidence=?,resolved_at=CURRENT_TIMESTAMP WHERE id=?",(evidence,fid))
    cloud_audit(u['username'],'RESOLVE','accessibility_finding',fid,evidence);return RedirectResponse('/readiness',303)

@app.get('/privacy',response_class=HTMLResponse)
def privacy_console(request:Request):
    require(request,['admin','hr']);tenant=active_tenant_record()
    if not tenant:raise HTTPException(404,'Tenant control record not found')
    return page(request,'privacy.html',**privacy_context(tenant['id']))

@app.post('/privacy/retention')
def retention_policy_upsert(request:Request,data_category:str=Form(...),retention_days:int=Form(...),action:str=Form('Delete'),legal_hold:int=Form(0),status:str=Form('Draft'),approved_by:str=Form('')):
    u=require(request,['admin']);tenant=active_tenant_record()
    allowed={'Notifications','Login Attempts','Security Events','Documents Metadata','Candidate Data'}
    if data_category not in allowed:raise HTTPException(400,'Unsupported data category')
    if status=='Approved' and not approved_by:raise HTTPException(400,'Approved retention policies require an approver')
    with control_conn() as c:c.execute("""INSERT INTO retention_policies(tenant_id,data_category,retention_days,action,legal_hold,status,approved_by,approved_at) VALUES(?,?,?,?,?,?,?,CASE WHEN ?='Approved' THEN CURRENT_TIMESTAMP ELSE NULL END) ON CONFLICT(tenant_id,data_category) DO UPDATE SET retention_days=excluded.retention_days,action=excluded.action,legal_hold=excluded.legal_hold,status=excluded.status,approved_by=excluded.approved_by,approved_at=excluded.approved_at""",(tenant['id'],data_category,max(1,retention_days),action,int(bool(legal_hold)),status,approved_by or None,status))
    cloud_audit(u['username'],'UPDATE','retention_policy',data_category,status,tenant['id']);return RedirectResponse('/privacy',303)

@app.post('/privacy/retention/run')
def retention_run(request:Request,dry_run:int=Form(1)):
    u=require(request,['admin']);tenant=active_tenant_record();reviewed=acted=0;details=[]
    with control_conn() as c:run_id=c.execute("INSERT INTO retention_runs(tenant_id,status) VALUES(?,'Running')",(tenant['id'],)).lastrowid;policies=c.execute("SELECT * FROM retention_policies WHERE tenant_id=? AND status='Approved' AND legal_hold=0",(tenant['id'],)).fetchall()
    for policy in policies:
        cutoff=(datetime.utcnow()-timedelta(days=policy['retention_days'])).strftime('%Y-%m-%d %H:%M:%S');category=policy['data_category'];count=0
        if category=='Notifications':count=q('SELECT COUNT(*) n FROM notifications WHERE created_at<?',(cutoff,),True)['n'];reviewed+=count;acted+=0 if dry_run else count;execq('DELETE FROM notifications WHERE created_at<?',(cutoff,)) if not dry_run else None
        elif category=='Documents Metadata':count=q('SELECT COUNT(*) n FROM documents WHERE created_at<?',(cutoff,),True)['n'];reviewed+=count;acted+=0 if dry_run else count;execq('DELETE FROM documents WHERE created_at<?',(cutoff,)) if not dry_run else None
        elif category=='Candidate Data':count=q("SELECT COUNT(*) n FROM candidates WHERE created_at<? AND status IN ('Rejected','Withdrawn')",(cutoff,),True)['n'];reviewed+=count;details.append(f'{category}: {count} candidates require anonymization review; automatic deletion disabled')
        elif category=='Login Attempts':
            epoch=int((datetime.utcnow()-timedelta(days=policy['retention_days'])).timestamp())
            with control_conn() as c:count=c.execute('SELECT COUNT(*) n FROM login_attempts WHERE tenant_slug=? AND attempted_at<?',(current_tenant(),epoch)).fetchone()['n'];reviewed+=count;acted+=0 if dry_run else count;c.execute('DELETE FROM login_attempts WHERE tenant_slug=? AND attempted_at<?',(current_tenant(),epoch)) if not dry_run else None
        elif category=='Security Events':
            with control_conn() as c:count=c.execute("SELECT COUNT(*) n FROM security_events WHERE tenant_id=? AND created_at<? AND status='Resolved'",(tenant['id'],cutoff)).fetchone()['n'];reviewed+=count;acted+=0 if dry_run else count;c.execute("DELETE FROM security_events WHERE tenant_id=? AND created_at<? AND status='Resolved'",(tenant['id'],cutoff)) if not dry_run else None
        details.append(f'{category}: reviewed {count}, acted {0 if dry_run else count}')
    with control_conn() as c:c.execute("UPDATE retention_runs SET status='Completed',records_reviewed=?,records_acted=?,details=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(reviewed,acted,'\n'.join(details),run_id))
    cloud_audit(u['username'],'RUN','retention',run_id,f'dry_run={bool(dry_run)}; reviewed={reviewed}; acted={acted}',tenant['id']);return RedirectResponse('/privacy',303)

@app.post('/privacy/requests')
def privacy_request_create(request:Request,request_type:str=Form(...),data_subject:str=Form(...),received_on:str=Form(...),due_on:str=Form('')):
    u=require(request,['admin','hr']);tenant=active_tenant_record()
    if request_type not in ['Access','Correction','Erasure','Grievance','Consent Withdrawal']:raise HTTPException(400,'Invalid privacy request type')
    with control_conn() as c:rid=c.execute('INSERT INTO privacy_requests(tenant_id,request_type,data_subject,received_on,due_on) VALUES(?,?,?,?,?)',(tenant['id'],request_type,data_subject,received_on,due_on or None)).lastrowid
    cloud_audit(u['username'],'CREATE','privacy_request',rid,request_type,tenant['id']);return RedirectResponse('/privacy',303)

@app.post('/privacy/requests/{rid}/update')
def privacy_request_update(rid:int,request:Request,identity_status:str=Form(...),status:str=Form(...),decision:str=Form(''),evidence:str=Form('')):
    u=require(request,['admin','hr']);tenant=active_tenant_record()
    if status=='Completed' and (identity_status!='Verified' or not evidence):raise HTTPException(400,'Completed requests require verified identity and evidence')
    with control_conn() as c:c.execute("UPDATE privacy_requests SET identity_status=?,status=?,decision=?,evidence=?,completed_at=CASE WHEN ?='Completed' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=? AND tenant_id=?",(identity_status,status,decision,evidence,status,rid,tenant['id']))
    cloud_audit(u['username'],'UPDATE','privacy_request',rid,status,tenant['id']);return RedirectResponse('/privacy',303)

@app.post('/privacy/breaches')
def breach_create(request:Request,severity:str=Form(...),detected_at:str=Form(...),description:str=Form(...),systems_affected:str=Form(''),data_categories:str=Form('')):
    u=require(request,['admin']);tenant=active_tenant_record()
    with control_conn() as c:bid=c.execute('INSERT INTO breach_incidents(tenant_id,severity,detected_at,description,systems_affected,data_categories) VALUES(?,?,?,?,?,?)',(tenant['id'],severity,detected_at,description,systems_affected,data_categories)).lastrowid
    record_security_event('PRIVACY_BREACH',severity,u['username'],request.client.host,description,tenant['id']);cloud_audit(u['username'],'CREATE','breach_incident',bid,severity,tenant['id']);return RedirectResponse('/privacy',303)

@app.post('/privacy/breaches/{bid}/update')
def breach_update(bid:int,request:Request,containment:str=Form(...),assessment_status:str=Form(...),notification_decision:str=Form(''),status:str=Form(...)):
    u=require(request,['admin']);tenant=active_tenant_record()
    if status=='Closed' and (not containment or not notification_decision):raise HTTPException(400,'Closed incidents require containment and notification decision')
    with control_conn() as c:c.execute("UPDATE breach_incidents SET containment=?,assessment_status=?,notification_decision=?,status=?,closed_at=CASE WHEN ?='Closed' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=? AND tenant_id=?",(containment,assessment_status,notification_decision,status,status,bid,tenant['id']))
    cloud_audit(u['username'],'UPDATE','breach_incident',bid,status,tenant['id']);return RedirectResponse('/privacy',303)

@app.post('/locale')
def locale_change(request:Request,locale:str=Form(...)):
    if locale not in ['en','hi']:raise HTTPException(400,'Unsupported locale')
    r=RedirectResponse(request.headers.get('referer','/'),303);r.set_cookie('peawork_locale',locale,samesite='lax',max_age=31536000);return r

@app.post('/readiness/security-events/{sid}/resolve')
def security_event_resolve(sid:int,request:Request,resolution:str=Form(...)):
    u=require_platform_admin(request)
    with control_conn() as c:c.execute("UPDATE security_events SET status='Resolved',details=details||? ,resolved_at=CURRENT_TIMESTAMP WHERE id=?",('\nResolution: '+resolution,sid))
    cloud_audit(u['username'],'RESOLVE','security_event',sid,resolution);return RedirectResponse('/readiness',303)

# ----------------------------- v1.1 Global Core HR -------------------------
CORE_HR_ROLES=['admin','hr']
def current_assignment(employee_id,on_date=None):
    on_date=on_date or date.today().isoformat()
    return q("""SELECT * FROM employee_assignments WHERE employee_id=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) AND assignment_status='Active' ORDER BY primary_assignment DESC,effective_from DESC,id DESC LIMIT 1""",(employee_id,on_date,on_date),True)
def lifecycle_event(employee_id,code,effective_date,details,source_type='',source_id='',assignment_id=None,created_by=None):
    event=q('SELECT id FROM lifecycle_event_types WHERE code=?',(code,),True)
    if not event:raise HTTPException(400,f'Lifecycle event type {code} is not configured')
    return execq('INSERT INTO employee_events(employee_id,event_type_id,effective_date,assignment_id,details_json,source_entity_type,source_entity_id,created_by) VALUES(?,?,?,?,?,?,?,?)',(employee_id,event['id'],effective_date,assignment_id,json.dumps(details),source_type,str(source_id),created_by))
def core_hr_context():
    return {'entities':q('SELECT * FROM legal_entities ORDER BY name'),'locations':q("SELECT l.*,e.name legal_entity FROM work_locations l LEFT JOIN legal_entities e ON e.id=l.legal_entity_id ORDER BY l.country_code,l.name"),'cost_centers':q("SELECT c.*,e.name legal_entity,p.name parent,o.first_name||' '||o.last_name owner FROM cost_centers c LEFT JOIN legal_entities e ON e.id=c.legal_entity_id LEFT JOIN cost_centers p ON p.id=c.parent_id LEFT JOIN employees o ON o.id=c.owner_employee_id ORDER BY c.code"),'projects':q("SELECT p.*,e.name legal_entity,c.name cost_center,m.first_name||' '||m.last_name manager FROM projects p LEFT JOIN legal_entities e ON e.id=p.legal_entity_id LEFT JOIN cost_centers c ON c.id=p.cost_center_id LEFT JOIN employees m ON m.id=p.manager_employee_id ORDER BY p.status,p.code"),'job_families':q('SELECT * FROM job_families WHERE active=1 ORDER BY name'),'job_profiles':q("SELECT j.*,f.name family FROM job_profiles j LEFT JOIN job_families f ON f.id=j.family_id WHERE j.active=1 ORDER BY f.name,j.level_code,j.title"),'job_skills':q("SELECT r.*,j.title job,s.name skill,s.category FROM job_skill_requirements r JOIN job_profiles j ON j.id=r.job_profile_id JOIN skills s ON s.id=r.skill_id ORDER BY j.title,s.name"),'employees':q("SELECT id,employee_no,first_name||' '||last_name name FROM employees ORDER BY first_name"),'departments':q('SELECT * FROM departments WHERE active=1 ORDER BY name'),'positions':q('SELECT * FROM positions WHERE active=1 ORDER BY title'),'skills':q('SELECT * FROM skills WHERE active=1 ORDER BY category,name'),'global_profiles':q("SELECT g.*,e.employee_no,e.first_name||' '||e.last_name employee FROM employee_global_profiles g JOIN employees e ON e.id=g.employee_id ORDER BY e.first_name"),'assignments':q("""SELECT a.*,e.employee_no,e.first_name||' '||e.last_name employee,l.name legal_entity,w.name location,d.name department,c.name cost_center,p.name project,j.title job_profile,m.first_name||' '||m.last_name manager FROM employee_assignments a JOIN employees e ON e.id=a.employee_id JOIN legal_entities l ON l.id=a.legal_entity_id LEFT JOIN work_locations w ON w.id=a.location_id LEFT JOIN departments d ON d.id=a.department_id LEFT JOIN cost_centers c ON c.id=a.cost_center_id LEFT JOIN projects p ON p.id=a.project_id LEFT JOIN job_profiles j ON j.id=a.job_profile_id LEFT JOIN employees m ON m.id=a.manager_id ORDER BY a.effective_from DESC,e.first_name"""),'transfers':q("""SELECT t.*,e.first_name||' '||e.last_name employee,l.name target_entity,w.name target_location,d.name target_department,c.name target_cost_center,p.name target_project,j.title target_job,m.first_name||' '||m.last_name target_manager FROM employee_transfers t JOIN employees e ON e.id=t.employee_id LEFT JOIN legal_entities l ON l.id=t.target_legal_entity_id LEFT JOIN work_locations w ON w.id=t.target_location_id LEFT JOIN departments d ON d.id=t.target_department_id LEFT JOIN cost_centers c ON c.id=t.target_cost_center_id LEFT JOIN projects p ON p.id=t.target_project_id LEFT JOIN job_profiles j ON j.id=t.target_job_profile_id LEFT JOIN employees m ON m.id=t.target_manager_id ORDER BY t.id DESC"""),'salary_history':q("""SELECT s.*,e.first_name||' '||e.last_name employee,a.assignment_no FROM employee_salary_history s JOIN employees e ON e.id=s.employee_id LEFT JOIN employee_assignments a ON a.id=s.assignment_id ORDER BY s.effective_from DESC,e.first_name"""),'skill_gaps':q("""SELECT a.employee_id,e.first_name||' '||e.last_name employee,j.title job,s.name skill,r.required_proficiency,COALESCE(es.proficiency,0) employee_proficiency,r.critical FROM employee_assignments a JOIN employees e ON e.id=a.employee_id JOIN job_profiles j ON j.id=a.job_profile_id JOIN job_skill_requirements r ON r.job_profile_id=j.id JOIN skills s ON s.id=r.skill_id LEFT JOIN employee_skills es ON es.employee_id=a.employee_id AND es.skill_id=r.skill_id WHERE a.assignment_status='Active' AND a.effective_to IS NULL AND COALESCE(es.proficiency,0)<r.required_proficiency ORDER BY r.critical DESC,e.first_name,s.name""")}

@app.get('/core-hr',response_class=HTMLResponse)
def core_hr(request:Request):require(request);return page(request,'core_hr.html',**core_hr_context())

@app.post('/core-hr/legal-entities')
def legal_entity_create(request:Request,code:str=Form(...),name:str=Form(...),country_code:str=Form(...),registration_no:str=Form(''),tax_id_masked:str=Form(''),currency_code:str=Form(...),timezone:str=Form(...)):
 u=require(request,CORE_HR_ROLES);eid=execq('INSERT INTO legal_entities(code,name,country_code,registration_no,tax_id_masked,currency_code,timezone) VALUES(?,?,?,?,?,?,?)',(code.upper(),name,country_code.upper(),registration_no or None,tax_id_masked or None,currency_code.upper(),timezone));audit(u['username'],'CREATE','legal_entity',eid,code);return RedirectResponse('/core-hr',303)
@app.post('/core-hr/locations')
def location_create(request:Request,code:str=Form(...),name:str=Form(...),legal_entity_id:int|None=Form(None),country_code:str=Form(...),state_region:str=Form(''),city:str=Form(''),timezone:str=Form(...),address:str=Form('')):
 u=require(request,CORE_HR_ROLES);lid=execq('INSERT INTO work_locations(code,name,legal_entity_id,country_code,state_region,city,timezone,address) VALUES(?,?,?,?,?,?,?,?)',(code.upper(),name,legal_entity_id,country_code.upper(),state_region,city,timezone,address));audit(u['username'],'CREATE','work_location',lid,code);return RedirectResponse('/core-hr',303)
@app.post('/core-hr/cost-centers')
def cost_center_create(request:Request,code:str=Form(...),name:str=Form(...),legal_entity_id:int|None=Form(None),parent_id:int|None=Form(None),owner_employee_id:int|None=Form(None)):
 u=require(request,CORE_HR_ROLES);cid=execq('INSERT INTO cost_centers(code,name,legal_entity_id,parent_id,owner_employee_id) VALUES(?,?,?,?,?)',(code.upper(),name,legal_entity_id,parent_id,owner_employee_id));audit(u['username'],'CREATE','cost_center',cid,code);return RedirectResponse('/core-hr',303)
@app.post('/core-hr/projects')
def project_create(request:Request,code:str=Form(...),name:str=Form(...),legal_entity_id:int|None=Form(None),cost_center_id:int|None=Form(None),manager_employee_id:int|None=Form(None),start_date:str=Form(''),end_date:str=Form(''),client_name:str=Form('')):
 u=require(request,CORE_HR_ROLES);pid=execq('INSERT INTO projects(code,name,legal_entity_id,cost_center_id,manager_employee_id,start_date,end_date,client_name) VALUES(?,?,?,?,?,?,?,?)',(code.upper(),name,legal_entity_id,cost_center_id,manager_employee_id,start_date or None,end_date or None,client_name));audit(u['username'],'CREATE','project',pid,code);return RedirectResponse('/core-hr',303)
@app.post('/core-hr/job-families')
def job_family_create(request:Request,code:str=Form(...),name:str=Form(...),description:str=Form('')):
 u=require(request,CORE_HR_ROLES);fid=execq('INSERT INTO job_families(code,name,description) VALUES(?,?,?)',(code.upper(),name,description));audit(u['username'],'CREATE','job_family',fid,code);return RedirectResponse('/core-hr',303)
@app.post('/core-hr/job-profiles')
def job_profile_create(request:Request,code:str=Form(...),title:str=Form(...),family_id:int|None=Form(None),level_code:str=Form(''),grade:str=Form(''),description:str=Form(''),responsibilities:str=Form('')):
 u=require(request,CORE_HR_ROLES);jid=execq('INSERT INTO job_profiles(code,title,family_id,level_code,grade,description,responsibilities) VALUES(?,?,?,?,?,?,?)',(code.upper(),title,family_id,level_code,grade,description,responsibilities));audit(u['username'],'CREATE','job_profile',jid,code);return RedirectResponse('/core-hr',303)
@app.post('/core-hr/job-profiles/{jid}/skills')
def job_skill_create(jid:int,request:Request,skill_id:int=Form(...),required_proficiency:int=Form(...),critical:int=Form(0)):
 u=require(request,CORE_HR_ROLES);rid=execq('INSERT INTO job_skill_requirements(job_profile_id,skill_id,required_proficiency,critical) VALUES(?,?,?,?)',(jid,skill_id,max(1,min(5,required_proficiency)),int(bool(critical))));audit(u['username'],'CREATE','job_skill_requirement',rid,f'job={jid}');return RedirectResponse('/core-hr',303)

@app.post('/core-hr/global-profiles')
def global_profile_upsert(request:Request,employee_id:int=Form(...),global_employee_id:str=Form(...),preferred_name:str=Form(''),personal_email:str=Form(''),nationality_country_code:str=Form(''),home_country_code:str=Form(''),primary_language:str=Form('en'),emergency_contact_name:str=Form(''),emergency_contact_phone:str=Form(''),data_residency_region:str=Form('')):
 u=require(request,CORE_HR_ROLES);execq("""INSERT INTO employee_global_profiles(employee_id,global_employee_id,preferred_name,personal_email,nationality_country_code,home_country_code,primary_language,emergency_contact_name,emergency_contact_phone,data_residency_region) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(employee_id) DO UPDATE SET global_employee_id=excluded.global_employee_id,preferred_name=excluded.preferred_name,personal_email=excluded.personal_email,nationality_country_code=excluded.nationality_country_code,home_country_code=excluded.home_country_code,primary_language=excluded.primary_language,emergency_contact_name=excluded.emergency_contact_name,emergency_contact_phone=excluded.emergency_contact_phone,data_residency_region=excluded.data_residency_region,updated_at=CURRENT_TIMESTAMP""",(employee_id,global_employee_id,preferred_name,personal_email,nationality_country_code.upper() or None,home_country_code.upper() or None,primary_language,emergency_contact_name,emergency_contact_phone,data_residency_region));audit(u['username'],'UPSERT','employee_global_profile',employee_id,global_employee_id);return RedirectResponse('/core-hr',303)

@app.post('/core-hr/assignments')
def assignment_create(request:Request,employee_id:int=Form(...),assignment_no:str=Form('A1'),legal_entity_id:int=Form(...),location_id:int|None=Form(None),department_id:int|None=Form(None),cost_center_id:int|None=Form(None),project_id:int|None=Form(None),job_profile_id:int|None=Form(None),position_id:int|None=Form(None),manager_id:int|None=Form(None),employment_type:str=Form('Full-time'),worker_type:str=Form('Employee'),effective_from:str=Form(...),reason:str=Form('Initial assignment')):
 u=require(request,CORE_HR_ROLES)
 overlap=q("SELECT id FROM employee_assignments WHERE employee_id=? AND assignment_no=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)",(employee_id,assignment_no,effective_from,effective_from),True)
 if overlap:raise HTTPException(400,'Assignment overlaps an existing effective period')
 aid=execq('INSERT INTO employee_assignments(employee_id,assignment_no,legal_entity_id,location_id,department_id,cost_center_id,project_id,job_profile_id,position_id,manager_id,employment_type,worker_type,effective_from,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(employee_id,assignment_no,legal_entity_id,location_id,department_id,cost_center_id,project_id,job_profile_id,position_id,manager_id,employment_type,worker_type,effective_from,reason));lifecycle_event(employee_id,'HIRE',effective_from,{'assignment_id':aid,'reason':reason},'assignment',aid,aid,u['id']);audit(u['username'],'CREATE','employee_assignment',aid,f'employee={employee_id}');return RedirectResponse('/core-hr',303)

@app.post('/core-hr/transfers')
def transfer_request(request:Request,employee_id:int=Form(...),effective_date:str=Form(...),target_legal_entity_id:int|None=Form(None),target_location_id:int|None=Form(None),target_department_id:int|None=Form(None),target_cost_center_id:int|None=Form(None),target_project_id:int|None=Form(None),target_job_profile_id:int|None=Form(None),target_position_id:int|None=Form(None),target_manager_id:int|None=Form(None),reason:str=Form(...)):
 u=require(request,CORE_HR_ROLES);source=current_assignment(employee_id,effective_date)
 if not source:raise HTTPException(400,'No active source assignment exists on the transfer date')
 tid=execq("""INSERT INTO employee_transfers(employee_id,source_assignment_id,effective_date,target_legal_entity_id,target_location_id,target_department_id,target_cost_center_id,target_project_id,target_job_profile_id,target_position_id,target_manager_id,reason,status,requested_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'Pending Approval',?)""",(employee_id,source['id'],effective_date,target_legal_entity_id,target_location_id,target_department_id,target_cost_center_id,target_project_id,target_job_profile_id,target_position_id,target_manager_id,reason,u['id']));audit(u['username'],'REQUEST','employee_transfer',tid,reason);return RedirectResponse('/core-hr',303)

@app.post('/core-hr/transfers/{tid}/decision')
def transfer_decision(tid:int,request:Request,decision:str=Form(...)):
 u=require(request,['admin','hr']);transfer=q('SELECT * FROM employee_transfers WHERE id=?',(tid,),True)
 if not transfer or transfer['status']!='Pending Approval':raise HTTPException(400,'Transfer is not pending approval')
 if decision!='approve':execq("UPDATE employee_transfers SET status='Rejected',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],tid));audit(u['username'],'REJECT','employee_transfer',tid);return RedirectResponse('/core-hr',303)
 source=q('SELECT * FROM employee_assignments WHERE id=?',(transfer['source_assignment_id'],),True);end_date=(datetime.fromisoformat(transfer['effective_date']).date()-timedelta(days=1)).isoformat()
 with conn() as c:
  c.execute('UPDATE employee_assignments SET effective_to=?,assignment_status=? WHERE id=?',(end_date,'Transferred',source['id']))
  cur=c.execute("""INSERT INTO employee_assignments(employee_id,assignment_no,legal_entity_id,location_id,department_id,cost_center_id,project_id,job_profile_id,position_id,manager_id,employment_type,worker_type,assignment_status,effective_from,primary_assignment,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'Active',?,? ,?)""",(transfer['employee_id'],source['assignment_no'],transfer['target_legal_entity_id'] or source['legal_entity_id'],transfer['target_location_id'] or source['location_id'],transfer['target_department_id'] or source['department_id'],transfer['target_cost_center_id'] or source['cost_center_id'],transfer['target_project_id'] or source['project_id'],transfer['target_job_profile_id'] or source['job_profile_id'],transfer['target_position_id'] or source['position_id'],transfer['target_manager_id'] or source['manager_id'],source['employment_type'],source['worker_type'],transfer['effective_date'],source['primary_assignment'],transfer['reason']));new_assignment=cur.lastrowid
  c.execute("UPDATE employee_transfers SET status='Completed',approved_by=?,approved_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],tid))
  c.execute('UPDATE employees SET department_id=?,position_id=?,manager_id=?,location=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(transfer['target_department_id'] or source['department_id'],transfer['target_position_id'] or source['position_id'],transfer['target_manager_id'] or source['manager_id'],(q('SELECT name FROM work_locations WHERE id=?',(transfer['target_location_id'] or source['location_id'],),True) or {}).get('name'),transfer['employee_id']))
 lifecycle_event(transfer['employee_id'],'TRANSFER',transfer['effective_date'],{'source_assignment_id':source['id'],'target_assignment_id':new_assignment,'reason':transfer['reason']},'transfer',tid,new_assignment,u['id']);audit(u['username'],'COMPLETE','employee_transfer',tid,f'new assignment={new_assignment}');return RedirectResponse('/core-hr',303)

@app.post('/core-hr/salary')
def salary_change_request(request:Request,employee_id:int=Form(...),currency_code:str=Form(...),annual_base:float=Form(...),annual_variable_target:float=Form(0),effective_from:str=Form(...),reason:str=Form(...)):
 u=require(request,CORE_HR_ROLES);assignment=current_assignment(employee_id,effective_from);sid=execq("INSERT INTO employee_salary_history(employee_id,assignment_id,currency_code,annual_base,annual_variable_target,effective_from,reason,approval_status,requested_by) VALUES(?,?,?,?,?,?,?,'Pending Approval',?)",(employee_id,assignment['id'] if assignment else None,currency_code.upper(),money(annual_base),money(annual_variable_target),effective_from,reason,u['id']));audit(u['username'],'REQUEST','salary_change',sid,reason);return RedirectResponse('/core-hr',303)
@app.post('/core-hr/salary/{sid}/decision')
def salary_change_decision(sid:int,request:Request,decision:str=Form(...)):
 u=require(request,['admin','hr']);salary=q('SELECT * FROM employee_salary_history WHERE id=?',(sid,),True)
 if not salary or salary['approval_status']!='Pending Approval':raise HTTPException(400,'Salary change is not pending')
 if decision!='approve':execq("UPDATE employee_salary_history SET approval_status='Rejected',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],sid));return RedirectResponse('/core-hr',303)
 prior=q("SELECT id FROM employee_salary_history WHERE employee_id=? AND approval_status='Approved' AND effective_from<? AND (effective_to IS NULL OR effective_to>=?) ORDER BY effective_from DESC LIMIT 1",(salary['employee_id'],salary['effective_from'],salary['effective_from']),True)
 if prior:execq('UPDATE employee_salary_history SET effective_to=? WHERE id=?',((datetime.fromisoformat(salary['effective_from']).date()-timedelta(days=1)).isoformat(),prior['id']))
 execq("UPDATE employee_salary_history SET approval_status='Approved',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],sid));lifecycle_event(salary['employee_id'],'SALARY_CHANGE',salary['effective_from'],{'annual_base':salary['annual_base'],'annual_variable_target':salary['annual_variable_target'],'currency':salary['currency_code'],'reason':salary['reason']},'salary',sid,salary['assignment_id'],u['id']);audit(u['username'],'APPROVE','salary_change',sid);return RedirectResponse('/core-hr',303)

@app.get('/api/v1/core-hr/assignments')
def api_core_assignments(request:Request):require(request);return q('SELECT * FROM employee_assignments ORDER BY effective_from DESC,id DESC')
@app.get('/api/v1/core-hr/events')
def api_core_events(request:Request):require(request);return q('SELECT * FROM employee_events ORDER BY effective_date DESC,id DESC')

def journey_owner(role,manager_id=None):
 if role=='manager' and manager_id:
  row=q('SELECT id FROM users WHERE employee_id=? AND active=1',(manager_id,),True)
  if row:return row['id']
 row=q('SELECT id FROM users WHERE role=? AND active=1 ORDER BY id LIMIT 1',(role,),True)
 if not row:row=q("SELECT id FROM users WHERE role IN ('hr','admin') AND active=1 ORDER BY CASE role WHEN 'hr' THEN 1 ELSE 2 END,id LIMIT 1",one=True)
 return row['id'] if row else None
def lifecycle_context():
 return {'employees':q("SELECT id,employee_no,first_name||' '||last_name name FROM employees ORDER BY first_name"),'journey_definitions':q("SELECT j.*,l.name legal_entity,(SELECT COUNT(*) FROM journey_task_templates t WHERE t.journey_id=j.id) task_count FROM journey_definitions j LEFT JOIN legal_entities l ON l.id=j.legal_entity_id ORDER BY j.journey_type,j.name"),'journey_templates':q("SELECT t.*,j.name journey FROM journey_task_templates t JOIN journey_definitions j ON j.id=t.journey_id ORDER BY j.name,t.due_offset_days,t.id"),'employee_journeys':q("""SELECT j.*,e.first_name||' '||e.last_name employee,d.name journey,(SELECT COUNT(*) FROM employee_journey_tasks t WHERE t.employee_journey_id=j.id) total_tasks,(SELECT COUNT(*) FROM employee_journey_tasks t WHERE t.employee_journey_id=j.id AND t.status IN ('Completed','Waived')) completed_tasks FROM employee_journeys j JOIN employees e ON e.id=j.employee_id JOIN journey_definitions d ON d.id=j.journey_id ORDER BY j.id DESC"""),'journey_tasks':q("""SELECT t.*,e.first_name||' '||e.last_name employee,d.name journey,u.username owner FROM employee_journey_tasks t JOIN employee_journeys j ON j.id=t.employee_journey_id JOIN employees e ON e.id=j.employee_id JOIN journey_definitions d ON d.id=j.journey_id LEFT JOIN users u ON u.id=t.owner_user_id ORDER BY CASE t.status WHEN 'Open' THEN 1 WHEN 'In Progress' THEN 2 ELSE 3 END,t.due_date"""),'events':q("""SELECT x.*,e.first_name||' '||e.last_name employee,t.code,t.name event_type,t.category FROM employee_events x JOIN employees e ON e.id=x.employee_id JOIN lifecycle_event_types t ON t.id=x.event_type_id ORDER BY x.effective_date DESC,x.id DESC"""),'offboarding_templates':q('SELECT * FROM offboarding_task_templates WHERE active=1 ORDER BY due_offset_days,id'),'offboarding_cases':q("""SELECT c.*,e.first_name||' '||e.last_name employee,(SELECT COUNT(*) FROM offboarding_tasks t WHERE t.case_id=c.id) total_tasks,(SELECT COUNT(*) FROM offboarding_tasks t WHERE t.case_id=c.id AND t.status IN ('Completed','Waived')) completed_tasks FROM offboarding_cases c JOIN employees e ON e.id=c.employee_id ORDER BY c.id DESC"""),'offboarding_tasks':q("""SELECT t.*,e.first_name||' '||e.last_name employee,u.username owner FROM offboarding_tasks t JOIN offboarding_cases c ON c.id=t.case_id JOIN employees e ON e.id=c.employee_id LEFT JOIN users u ON u.id=t.owner_user_id ORDER BY CASE t.status WHEN 'Open' THEN 1 WHEN 'In Progress' THEN 2 ELSE 3 END,t.due_date""")}

@app.get('/lifecycle',response_class=HTMLResponse)
def lifecycle(request:Request):require(request);return page(request,'lifecycle.html',**lifecycle_context())
@app.post('/lifecycle/journeys')
def journey_definition_create(request:Request,code:str=Form(...),name:str=Form(...),journey_type:str=Form(...),legal_entity_id:int|None=Form(None),country_code:str=Form(''),employment_type:str=Form('Any')):
 u=require(request,CORE_HR_ROLES)
 if journey_type not in ['Onboarding','Transfer','Promotion','Return','Offboarding','Custom']:raise HTTPException(400,'Invalid journey type')
 jid=execq('INSERT INTO journey_definitions(code,name,journey_type,legal_entity_id,country_code,employment_type) VALUES(?,?,?,?,?,?)',(code.upper(),name,journey_type,legal_entity_id,country_code.upper() or None,employment_type));audit(u['username'],'CREATE','journey_definition',jid,code);return RedirectResponse('/lifecycle',303)
@app.post('/lifecycle/journeys/{jid}/templates')
def journey_template_create(jid:int,request:Request,title:str=Form(...),category:str=Form('General'),owner_role:str=Form('hr'),due_offset_days:int=Form(0),required:int=Form(1),completion_evidence_required:int=Form(0)):
 u=require(request,CORE_HR_ROLES);tid=execq('INSERT INTO journey_task_templates(journey_id,title,category,owner_role,due_offset_days,required,completion_evidence_required) VALUES(?,?,?,?,?,?,?)',(jid,title,category,owner_role,due_offset_days,int(bool(required)),int(bool(completion_evidence_required))));audit(u['username'],'CREATE','journey_task_template',tid,title);return RedirectResponse('/lifecycle',303)
@app.post('/lifecycle/journeys/start')
def employee_journey_start(request:Request,employee_id:int=Form(...),journey_id:int=Form(...),start_date:str=Form(...),target_date:str=Form(''),source_entity_type:str=Form('Manual'),source_entity_id:str=Form('')):
 u=require(request,CORE_HR_ROLES);definition=q('SELECT * FROM journey_definitions WHERE id=? AND active=1',(journey_id,),True);assignment=current_assignment(employee_id,start_date)
 if not definition:raise HTTPException(404,'Journey definition not found')
 ejid=execq("INSERT INTO employee_journeys(employee_id,journey_id,journey_type,start_date,target_date,status,source_entity_type,source_entity_id) VALUES(?,?,?,?,?,'In Progress',?,?)",(employee_id,journey_id,definition['journey_type'],start_date,target_date or None,source_entity_type,source_entity_id or None))
 for task in q('SELECT * FROM journey_task_templates WHERE journey_id=? ORDER BY due_offset_days,id',(journey_id,)):
  due=(datetime.fromisoformat(start_date).date()+timedelta(days=task['due_offset_days'])).isoformat();execq('INSERT INTO employee_journey_tasks(employee_journey_id,template_id,title,category,owner_user_id,due_date) VALUES(?,?,?,?,?,?)',(ejid,task['id'],task['title'],task['category'],journey_owner(task['owner_role'],assignment['manager_id'] if assignment else None),due))
 audit(u['username'],'START','employee_journey',ejid,definition['journey_type']);return RedirectResponse('/lifecycle',303)
@app.post('/lifecycle/journey-tasks/{tid}/status')
def employee_journey_task_status(tid:int,request:Request,status:str=Form(...),evidence:str=Form(''),notes:str=Form('')):
 u=require(request);task=q("SELECT t.*,x.required,x.completion_evidence_required,j.employee_id,j.journey_type FROM employee_journey_tasks t LEFT JOIN journey_task_templates x ON x.id=t.template_id JOIN employee_journeys j ON j.id=t.employee_journey_id WHERE t.id=?",(tid,),True)
 if not task:raise HTTPException(404,'Journey task not found')
 if status in ['Completed','Waived'] and task['completion_evidence_required'] and not evidence:raise HTTPException(400,'Completion evidence is required')
 execq("UPDATE employee_journey_tasks SET status=?,evidence=?,notes=?,completed_at=CASE WHEN ? IN ('Completed','Waived') THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",(status,evidence,notes,status,tid));remaining=q("SELECT COUNT(*) n FROM employee_journey_tasks WHERE employee_journey_id=? AND status NOT IN ('Completed','Waived')",(task['employee_journey_id'],),True)['n']
 if remaining==0:execq("UPDATE employee_journeys SET status='Completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",(task['employee_journey_id'],))
 audit(u['username'],'STATUS','employee_journey_task',tid,status);return RedirectResponse('/lifecycle',303)

@app.post('/lifecycle/offboarding')
def offboarding_create(request:Request,employee_id:int=Form(...),separation_type:str=Form(...),notice_date:str=Form(''),last_working_date:str=Form(...),reason:str=Form(...),regretted:int=Form(0),eligible_for_rehire:str=Form('Review')):
 u=require(request,CORE_HR_ROLES);assignment=current_assignment(employee_id,last_working_date)
 if not assignment:raise HTTPException(400,'No active assignment exists for the last working date')
 cid=execq("INSERT INTO offboarding_cases(employee_id,assignment_id,separation_type,notice_date,last_working_date,reason,regretted,eligible_for_rehire,status,requested_by) VALUES(?,?,?,?,?,?,?,?, 'Pending Approval',?)",(employee_id,assignment['id'],separation_type,notice_date or None,last_working_date,reason,int(bool(regretted)),eligible_for_rehire,u['id']));audit(u['username'],'REQUEST','offboarding_case',cid,reason);return RedirectResponse('/lifecycle',303)
@app.post('/lifecycle/offboarding/{cid}/decision')
def offboarding_decision(cid:int,request:Request,decision:str=Form(...)):
 u=require(request,['admin','hr']);case=q('SELECT * FROM offboarding_cases WHERE id=?',(cid,),True)
 if not case or case['status']!='Pending Approval':raise HTTPException(400,'Offboarding case is not pending approval')
 if decision!='approve':execq("UPDATE offboarding_cases SET status='Rejected',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],cid));return RedirectResponse('/lifecycle',303)
 execq("UPDATE offboarding_cases SET status='In Progress',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],cid))
 for task in q('SELECT * FROM offboarding_task_templates WHERE active=1 ORDER BY due_offset_days,id'):
  due=(datetime.fromisoformat(case['last_working_date']).date()+timedelta(days=task['due_offset_days'])).isoformat();assignment=q('SELECT manager_id FROM employee_assignments WHERE id=?',(case['assignment_id'],),True);execq('INSERT INTO offboarding_tasks(case_id,template_id,title,category,owner_user_id,due_date) VALUES(?,?,?,?,?,?)',(cid,task['id'],task['title'],task['category'],journey_owner(task['owner_role'],assignment['manager_id'] if assignment else None),due))
 audit(u['username'],'APPROVE','offboarding_case',cid);return RedirectResponse('/lifecycle',303)
@app.post('/lifecycle/offboarding-tasks/{tid}/status')
def offboarding_task_status(tid:int,request:Request,status:str=Form(...),evidence:str=Form(''),notes:str=Form('')):
 u=require(request);task=q("SELECT t.*,x.evidence_required FROM offboarding_tasks t LEFT JOIN offboarding_task_templates x ON x.id=t.template_id WHERE t.id=?",(tid,),True)
 if not task:raise HTTPException(404,'Offboarding task not found')
 if status in ['Completed','Waived'] and task['evidence_required'] and not evidence:raise HTTPException(400,'Evidence is required for this clearance task')
 execq("UPDATE offboarding_tasks SET status=?,evidence=?,notes=?,completed_at=CASE WHEN ? IN ('Completed','Waived') THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",(status,evidence,notes,status,tid));audit(u['username'],'STATUS','offboarding_task',tid,status);return RedirectResponse('/lifecycle',303)
@app.post('/lifecycle/offboarding/{cid}/complete')
def offboarding_complete(cid:int,request:Request):
 u=require(request,['admin','hr']);case=q('SELECT * FROM offboarding_cases WHERE id=?',(cid,),True)
 if not case or case['status']!='In Progress':raise HTTPException(400,'Offboarding case is not in progress')
 required_open=q("""SELECT COUNT(*) n FROM offboarding_tasks t LEFT JOIN offboarding_task_templates x ON x.id=t.template_id WHERE t.case_id=? AND COALESCE(x.required,1)=1 AND t.status NOT IN ('Completed','Waived')""",(cid,),True)['n']
 if required_open:raise HTTPException(400,f'{required_open} required offboarding task(s) remain open')
 with conn() as c:
  c.execute("UPDATE offboarding_cases SET status='Completed',completed_at=CURRENT_TIMESTAMP WHERE id=?",(cid,));c.execute("UPDATE employee_assignments SET effective_to=?,assignment_status='Separated' WHERE id=?",(case['last_working_date'],case['assignment_id']));c.execute("UPDATE employees SET status='Inactive',updated_at=CURRENT_TIMESTAMP WHERE id=?",(case['employee_id'],));c.execute("UPDATE users SET active=0 WHERE employee_id=?",(case['employee_id'],))
 lifecycle_event(case['employee_id'],'SEPARATION',case['last_working_date'],{'separation_type':case['separation_type'],'reason':case['reason'],'eligible_for_rehire':case['eligible_for_rehire']},'offboarding',cid,case['assignment_id'],u['id']);audit(u['username'],'COMPLETE','offboarding_case',cid);return RedirectResponse('/lifecycle',303)

@app.get('/api/v1/core-hr/journeys')
def api_core_journeys(request:Request):require(request);return q('SELECT * FROM employee_journeys ORDER BY id DESC')
@app.get('/api/v1/core-hr/offboarding')
def api_core_offboarding(request:Request):require(request,CORE_HR_ROLES);return q('SELECT * FROM offboarding_cases ORDER BY id DESC')

# ------------------------- v1.2 Employee Experience -------------------------
def ensure_preferences(user_id):
 execq("INSERT INTO notification_preferences(user_id) VALUES(?) ON CONFLICT(user_id) DO NOTHING",(user_id,))
def sync_inbox(u):
 uid=u['id'];emp=u.get('employee_id');items=[]
 if emp:
  for x in q("SELECT t.id,t.title,t.due_date,t.status FROM employee_journey_tasks t JOIN employee_journeys j ON j.id=t.employee_journey_id WHERE j.employee_id=? AND t.status NOT IN ('Completed','Waived')",(emp,)):items.append(('Journey Task','employee_journey_task',x['id'],x['title'],'Lifecycle task','/lifecycle',x['due_date'],'Normal'))
  for x in q("SELECT t.id,t.title,t.due_date,t.status FROM offboarding_tasks t JOIN offboarding_cases c ON c.id=t.case_id WHERE c.employee_id=? AND t.status NOT IN ('Completed','Waived')",(emp,)):items.append(('Offboarding Task','offboarding_task',x['id'],x['title'],'Offboarding clearance','/lifecycle',x['due_date'],'High'))
 if u['role'] in ['admin','hr','manager']:
  for x in q("SELECT id,employee_id,start_date,end_date FROM leave_requests WHERE status='Pending'"):items.append(('Approval','leave_request',x['id'],f"Leave request #{x['id']}",f"{x['start_date']} to {x['end_date']}",'/leave',None,'High'))
  for x in q("SELECT id,employee_id,week_start FROM timesheets WHERE status='Submitted'"):items.append(('Approval','timesheet',x['id'],f"Timesheet week {x['week_start']}",'Submitted for approval','/workforce',None,'High'))
 if u['role'] in ['admin','hr']:
  for x in q("SELECT id,employee_id,field_name FROM employee_change_requests WHERE status='Pending'"):items.append(('Approval','change_request',x['id'],f"Profile change: {x['field_name']}",f"Employee #{x['employee_id']}",'/experience',None,'Normal'))
 for item in items:execq("INSERT INTO inbox_items(user_id,item_type,source_entity_type,source_entity_id,title,subtitle,link,due_date,priority) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,item_type,source_entity_type,source_entity_id) DO UPDATE SET title=excluded.title,subtitle=excluded.subtitle,link=excluded.link,due_date=excluded.due_date,priority=excluded.priority,status='Open',completed_at=NULL",(uid,*item))
 return q("SELECT * FROM inbox_items WHERE user_id=? ORDER BY CASE priority WHEN 'High' THEN 1 ELSE 2 END,COALESCE(due_date,'9999-12-31'),id DESC",(uid,))
def experience_context(u):
 ensure_preferences(u['id']);sync_inbox(u);emp=u.get('employee_id')
 return {'employee':q("""SELECT e.*,g.global_employee_id,g.preferred_name,g.personal_email,g.primary_language,g.emergency_contact_name,g.emergency_contact_phone,a.assignment_no,l.name legal_entity,w.name work_location,d.name department,c.name cost_center,p.name project,j.title job_profile,m.first_name||' '||m.last_name manager FROM employees e LEFT JOIN employee_global_profiles g ON g.employee_id=e.id LEFT JOIN employee_assignments a ON a.employee_id=e.id AND a.assignment_status='Active' AND a.effective_to IS NULL LEFT JOIN legal_entities l ON l.id=a.legal_entity_id LEFT JOIN work_locations w ON w.id=a.location_id LEFT JOIN departments d ON d.id=a.department_id LEFT JOIN cost_centers c ON c.id=a.cost_center_id LEFT JOIN projects p ON p.id=a.project_id LEFT JOIN job_profiles j ON j.id=a.job_profile_id LEFT JOIN employees m ON m.id=a.manager_id WHERE e.id=?""",(emp,),True) if emp else None,'changes':q('SELECT * FROM employee_change_requests WHERE employee_id=? ORDER BY id DESC',(emp,)) if emp else [],'requests':q('SELECT * FROM employee_requests WHERE employee_id=? ORDER BY id DESC',(emp,)) if emp else [],'documents':q('SELECT * FROM documents WHERE employee_id=? ORDER BY id DESC',(emp,)) if emp else [],'acknowledgements':q('SELECT * FROM employee_acknowledgements WHERE employee_id=? ORDER BY id DESC',(emp,)) if emp else [],'preferences':q('SELECT * FROM notification_preferences WHERE user_id=?',(u['id'],),True),'inbox':q("SELECT * FROM inbox_items WHERE user_id=? AND status='Open' ORDER BY CASE priority WHEN 'High' THEN 1 ELSE 2 END,COALESCE(due_date,'9999-12-31')",(u['id'],)),'notifications_all':q('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)),'pending_changes':q("SELECT c.*,e.first_name||' '||e.last_name employee FROM employee_change_requests c JOIN employees e ON e.id=c.employee_id WHERE c.status='Pending' ORDER BY c.id") if u['role'] in ['admin','hr'] else [],'service_requests':q("SELECT r.*,e.first_name||' '||e.last_name employee FROM employee_requests r JOIN employees e ON e.id=r.employee_id WHERE r.status!='Completed' ORDER BY r.due_date,r.id") if u['role'] in ['admin','hr'] else []}
@app.get('/experience',response_class=HTMLResponse)
def experience(request:Request):u=require(request);return page(request,'experience.html',**experience_context(u))
@app.post('/experience/profile-change')
def profile_change(request:Request,field_name:str=Form(...),new_value:str=Form(...),reason:str=Form('')):
 u=require(request);allowed={'phone','personal_email','preferred_name','emergency_contact_name','emergency_contact_phone','primary_language'}
 if field_name not in allowed or not u.get('employee_id'):raise HTTPException(400,'Unsupported profile field')
 old='';profile=q('SELECT * FROM employee_global_profiles WHERE employee_id=?',(u['employee_id'],),True);employee=q('SELECT * FROM employees WHERE id=?',(u['employee_id'],),True)
 old=(employee or {}).get(field_name) if field_name=='phone' else (profile or {}).get(field_name)
 cid=execq('INSERT INTO employee_change_requests(employee_id,request_type,field_name,old_value,new_value,reason) VALUES(?,\'Profile Change\',?,?,?,?)',(u['employee_id'],field_name,old,new_value,reason));audit(u['username'],'REQUEST','employee_change',cid,field_name);return RedirectResponse('/experience',303)
@app.post('/experience/profile-change/{cid}/decision')
def profile_change_decision(cid:int,request:Request,decision:str=Form(...),comment:str=Form('')):
 u=require(request,['admin','hr']);change=q('SELECT * FROM employee_change_requests WHERE id=?',(cid,),True)
 if not change or change['status']!='Pending':raise HTTPException(400,'Change is not pending')
 if decision!='approve':execq("UPDATE employee_change_requests SET status='Rejected',reviewer_user_id=?,reviewer_comment=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],comment,cid));return RedirectResponse('/experience',303)
 if change['field_name']=='phone':execq('UPDATE employees SET phone=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(change['new_value'],change['employee_id']))
 else:execq(f"UPDATE employee_global_profiles SET {change['field_name']}=?,updated_at=CURRENT_TIMESTAMP WHERE employee_id=?",(change['new_value'],change['employee_id']))
 execq("UPDATE employee_change_requests SET status='Approved',reviewer_user_id=?,reviewer_comment=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],comment,cid));audit(u['username'],'APPROVE','employee_change',cid,change['field_name']);return RedirectResponse('/experience',303)
@app.post('/experience/requests')
def employee_request_create(request:Request,request_type:str=Form(...),title:str=Form(...),details:str=Form(''),due_date:str=Form('')):
 u=require(request);rid=execq('INSERT INTO employee_requests(employee_id,request_type,title,details,due_date) VALUES(?,?,?,?,?)',(u['employee_id'],request_type,title,details,due_date or None));audit(u['username'],'CREATE','employee_request',rid,request_type);return RedirectResponse('/experience',303)
@app.post('/experience/documents/{did}/acknowledge')
def document_acknowledge(did:int,request:Request,version:str=Form('current')):
 u=require(request);doc=q('SELECT * FROM documents WHERE id=? AND employee_id=?',(did,u['employee_id']),True)
 if not doc:raise HTTPException(404,'Document not found')
 execq('INSERT INTO employee_acknowledgements(employee_id,document_id,title,version,acknowledged_at,ip) VALUES(?,?,?,?,CURRENT_TIMESTAMP,?) ON CONFLICT(employee_id,document_id,version) DO UPDATE SET acknowledged_at=CURRENT_TIMESTAMP,ip=excluded.ip',(u['employee_id'],did,doc['title'],version,request.client.host));audit(u['username'],'ACKNOWLEDGE','document',did,version);return RedirectResponse('/experience',303)
@app.post('/experience/preferences')
def preferences_update(request:Request,email_enabled:int=Form(0),in_app_enabled:int=Form(0),push_enabled:int=Form(0),digest_frequency:str=Form('Daily'),quiet_start:str=Form(''),quiet_end:str=Form('')):
 u=require(request);ensure_preferences(u['id']);execq('UPDATE notification_preferences SET email_enabled=?,in_app_enabled=?,push_enabled=?,digest_frequency=?,quiet_start=?,quiet_end=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?',(int(bool(email_enabled)),int(bool(in_app_enabled)),int(bool(push_enabled)),digest_frequency,quiet_start or None,quiet_end or None,u['id']));return RedirectResponse('/experience',303)
@app.post('/experience/inbox/{iid}/complete')
def inbox_complete(iid:int,request:Request):
 u=require(request);execq("UPDATE inbox_items SET status='Completed',completed_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(iid,u['id']));return RedirectResponse('/experience',303)

def manager_context(u):
 emp=u.get('employee_id');team=q("SELECT id,employee_no,first_name||' '||last_name name,status,department_id,position_id FROM employees WHERE manager_id=? ORDER BY first_name",(emp,)) if emp else []
 ids=[x['id'] for x in team];calendar=[]
 for member in team:
  for x in q("SELECT start_date event_date,end_date,'Leave' event_type,status FROM leave_requests WHERE employee_id=? AND status IN ('Pending','Approved')",(member['id'],)):calendar.append({**x,'employee':member['name']})
  for x in q("SELECT effective_date event_date,effective_date end_date,t.name event_type,x.event_status status FROM employee_events x JOIN lifecycle_event_types t ON t.id=x.event_type_id WHERE x.employee_id=?",(member['id'],)):calendar.append({**x,'employee':member['name']})
 return {'team':team,'leave_approvals':q("SELECT l.*,e.first_name||' '||e.last_name employee,t.name type FROM leave_requests l JOIN employees e ON e.id=l.employee_id JOIN leave_types t ON t.id=l.leave_type_id WHERE e.manager_id=? AND l.status='Pending' ORDER BY l.id",(emp,)) if emp else [],'timesheet_approvals':q("SELECT t.*,e.first_name||' '||e.last_name employee,COALESCE((SELECT SUM(hours) FROM timesheet_entries x WHERE x.timesheet_id=t.id),0) hours FROM timesheets t JOIN employees e ON e.id=t.employee_id WHERE e.manager_id=? AND t.status='Submitted' ORDER BY t.week_start",(emp,)) if emp else [],'journey_tasks':q("SELECT t.*,e.first_name||' '||e.last_name employee,j.journey_type FROM employee_journey_tasks t JOIN employee_journeys j ON j.id=t.employee_journey_id JOIN employees e ON e.id=j.employee_id WHERE e.manager_id=? AND t.status NOT IN ('Completed','Waived') ORDER BY t.due_date",(emp,)) if emp else [],'calendar':sorted(calendar,key=lambda x:x['event_date'])}
@app.get('/manager',response_class=HTMLResponse)
def manager_workspace(request:Request):u=require(request,['admin','hr','manager']);return page(request,'manager.html',**manager_context(u))
@app.get('/api/v1/experience/inbox')
def api_experience_inbox(request:Request):u=require(request);sync_inbox(u);return q("SELECT * FROM inbox_items WHERE user_id=? AND status='Open' ORDER BY id DESC",(u['id'],))
@app.get('/api/v1/experience/team-calendar')
def api_team_calendar(request:Request):u=require(request,['admin','hr','manager']);return manager_context(u)['calendar']

@app.post('/experience/requests/{rid}/resolve')
def employee_request_resolve(rid:int,request:Request,resolution:str=Form(...)):
 u=require(request,['admin','hr']);execq("UPDATE employee_requests SET status='Completed',owner_user_id=?,resolution=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",(u['id'],resolution,rid));audit(u['username'],'RESOLVE','employee_request',rid,resolution);return RedirectResponse('/experience',303)
@app.post('/experience/notifications/{nid}/read')
def notification_read(nid:int,request:Request):
 u=require(request);execq('UPDATE notifications SET read_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(nid,u['id']));return RedirectResponse('/experience',303)
