from fastapi import FastAPI,Request,Form,UploadFile,File,HTTPException,Depends
from fastapi.responses import HTMLResponse,RedirectResponse,StreamingResponse,JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from datetime import date,datetime
import csv,io,json,os,shutil
from .db import conn,init_db
from .security import hash_password,verify_password,token,untoken

BASE=Path(__file__).resolve().parent.parent; UPLOADS=BASE/'uploads'; UPLOADS.mkdir(exist_ok=True)
app=FastAPI(title='PeaWork HRIS',version='0.1.0',description='India-first local and cloud-ready HRIS')
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
    audit('system','SEED','database','','Demo organization initialized')

@app.exception_handler(401)
async def unauthorized(request,exc): return RedirectResponse('/login?error=Please+sign+in',303)
@app.exception_handler(403)
async def forbidden(request,exc): return page(request,'error.html',code=403,message='You do not have access to this action.')

@app.get('/health')
def health(): return {'status':'ok','version':'0.1.0','deployment':os.getenv('PEAWORK_ENV','local')}
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
    stats={'employees':q("SELECT COUNT(*) n FROM employees WHERE status='Active'",one=True)['n'],'departments':q('SELECT COUNT(*) n FROM departments WHERE active=1',one=True)['n'],'pending':q("SELECT COUNT(*) n FROM leave_requests WHERE status='Pending'",one=True)['n'],'documents':q('SELECT COUNT(*) n FROM documents',one=True)['n']}
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
