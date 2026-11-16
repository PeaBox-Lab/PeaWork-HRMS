from fastapi import FastAPI,Request,Form,UploadFile,File,HTTPException,Depends    
from fastapi.responses import HTMLResponse,RedirectResponse,StreamingResponse,JSONResponse    
from fastapi.templating import Jinja2Templates    
from fastapi.staticfiles import StaticFiles    
from pathlib import Path    
from datetime import date,datetime,timedelta    
import csv,io,json,os,shutil    
from .db import conn,init_db    
from .security import hash_password,verify_password,token,untoken    
BASE=Path(__file__).resolve().parent.parent; UPLOADS=BASE/'uploads'; UPLOADS.mkdir(exist_ok=True)    
app=FastAPI(title='PeaWork HRIS',version='0.4.0',description='India-first local and cloud-ready HRIS')    
app.mount('/static',StaticFiles(directory=BASE/'app'/'static'),name='static'); templates=Jinja2Templates(directory=BASE/'app'/'templates')    
def q(sql,args=(),one=False):    
    with conn() as c:    
        r=c.execute(sql,args).fetchall(); return (dict(r[0]) if r and one else None) if one else [dict(x) for x in r]    
def execq(sql,args=()):    
    with conn() as c:    
        cur=c.execute(sql,args); return cur.lastrowid    
def audit(actor,action,etype,eid='',details='',ip=''):    
    execq('INSERT INTO audit_log(actor,action,entity_type,entity_id,details,ip) VALUES(?,?,?,?,?,?)',(actor,action,etype,str(eid),details,ip))    
def me(request):    
    t=request.cookies.get('peawork_session'); x=untoken(t) if t else None    
    return q('SELECT id,username,role,employee_id FROM users WHERE id=? AND active=1',(x['u'],),True) if x else None    
def require(request:Request,roles=None):    
    u=me(request)    
    if not u: raise HTTPException(401,'Login required')    
    if roles and u['role'] not in roles: raise HTTPException(403,'Insufficient permissions')    
    return u    
def page(request,name,**ctx):    
    u=me(request); notes=q('SELECT * FROM notifications WHERE user_id=? AND read_at IS NULL ORDER BY id DESC LIMIT 5',(u['id'],)) if u else []    
    return templates.TemplateResponse(request,name,{'user':u,'notifications':notes,'today':date.today().isoformat(),**ctx})    
@app.on_event('startup')    
def startup():    
    init_db()    
    if not q('SELECT id FROM users LIMIT 1',one=True): seed()    
def seed():    
    admin=execq('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',('admin',hash_password('ChangeMe123!'),'admin'))    
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
    audit('system','SEED','database','','Demo organization initialized through v0.4 talent')    
@app.exception_handler(401)    
async def unauthorized(request,exc): return RedirectResponse('/login?error=Please+sign+in',303)    
@app.exception_handler(403)    
async def forbidden(request,exc): return page(request,'error.html',code=403,message='You do not have access to this action.')    
@app.get('/health')    
def health(): return {'status':'ok','version':'0.4.0','deployment':os.getenv('PEAWORK_ENV','local')}    
@app.get('/login',response_class=HTMLResponse)    
def login_page(request:Request,error:str=''): return page(request,'login.html',error=error)    
@app.post('/login')    
def login(request:Request,username:str=Form(...),password:str=Form(...)):    
    u=q('SELECT * FROM users WHERE username=? AND active=1',(username,),True)    
    if not u or not verify_password(password,u['password_hash']):    
        audit(username,'LOGIN_FAILED','session','','',request.client.host); return RedirectResponse('/login?error=Invalid+credentials',303)    
    r=RedirectResponse('/',303); r.set_cookie('peawork_session',token(u['id'],u['role']),httponly=True,samesite='lax',secure=False,max_age=28800)    
    audit(username,'LOGIN','session',u['id'],'',request.client.host); return r    
@app.get('/logout')    
def logout(request:Request):    
    u=me(request)    
    if u:audit(u['username'],'LOGOUT','session',u['id'])    
    r=RedirectResponse('/login',303);r.delete_cookie('peawork_session');return r    
@app.get('/',response_class=HTMLResponse)    
def dashboard(request:Request):    
    u=require(request)    
    stats={'employees':q("SELECT COUNT(*) n FROM employees WHERE status='Active'",one=True)['n'],'departments':q('SELECT COUNT(*) n FROM departments WHERE active=1',one=True)['n'],'pending':q("SELECT COUNT(*) n FROM leave_requests WHERE status='Pending'",one=True)['n'],'documents':q('SELECT COUNT(*) n FROM documents',one=True)['n'],'open_requisitions':q("SELECT COUNT(*) n FROM requisitions WHERE status='Open'",one=True)['n'],'active_candidates':q("SELECT COUNT(*) n FROM applications WHERE status='Active'",one=True)['n'],'onboarding':q("SELECT COUNT(*) n FROM onboardings WHERE status IN ('Planned','In Progress')",one=True)['n'],'present_today':q("SELECT COUNT(*) n FROM attendance_records WHERE attendance_date=? AND status IN ('Present','Late','Half Day')",(date.today().isoformat(),),True)['n'],'pending_timesheets':q("SELECT COUNT(*) n FROM timesheets WHERE status='Submitted'",one=True)['n'],'active_cycles':q("SELECT COUNT(*) n FROM performance_cycles WHERE status='Active'",one=True)['n'],'goals_at_risk':q("SELECT COUNT(*) n FROM goals WHERE status='At Risk'",one=True)['n'],'feedback_due':q("SELECT COUNT(*) n FROM feedback_requests WHERE status='Requested'",one=True)['n']}    
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
    u=require(request,['admin','hr']); safe=''.join(c for c in file.filename if c.isalnum() or c in '._-')[:100]; target=UPLOADS/(datetime.now().strftime('%Y%m%d%H%M%S')+'_'+safe)    
    with target.open('wb') as f: shutil.copyfileobj(file.file,f)    
    i=execq('INSERT INTO documents(employee_id,category,title,filename,storage_path,expires_on) VALUES(?,?,?,?,?,?)',(employee_id,category,title,file.filename,str(target),expires_on or None));audit(u['username'],'UPLOAD','document',i,file.filename);return RedirectResponse('/documents',303)    
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
        safe=''.join(c for c in filename if c.isalnum() or c in '._-')[:100]  
        target=UPLOADS/(datetime.now().strftime('%Y%m%d%H%M%S')+'_resume_'+safe)  
        with target.open('wb') as f: shutil.copyfileobj(resume.file,f)  
        path=str(target)  
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
