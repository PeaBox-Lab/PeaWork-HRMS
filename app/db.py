import os, re, sqlite3
from contextvars import ContextVar
from pathlib import Path

BASE=Path(__file__).resolve().parent.parent
DEFAULT_DB=Path(os.getenv("PEAWORK_DATABASE",str(BASE/'data'/'peawork.db')))
DEFAULT_DB.parent.mkdir(parents=True,exist_ok=True)
TENANT_SLUG=ContextVar('peawork_tenant_slug',default=os.getenv('PEAWORK_DEFAULT_TENANT','local'))

def set_tenant(slug): return TENANT_SLUG.set(slug or 'local')
def reset_tenant(token): TENANT_SLUG.reset(token)
def current_tenant(): return TENANT_SLUG.get()

def tenant_db_path(slug=None):
    resolved=slug or current_tenant()
    if resolved!='local' or os.getenv('PEAWORK_ENV','local').startswith('cloud'):
        root=Path(os.getenv('PEAWORK_TENANT_DATABASE_ROOT',str(DEFAULT_DB.parent/'tenants')))
        path=root/f"{resolved}.db";path.parent.mkdir(parents=True,exist_ok=True);return path
    return DEFAULT_DB

class PgCursor:
    def __init__(self,cur,lastrowid=None): self.cur=cur;self.lastrowid=lastrowid
    def fetchall(self): return self.cur.fetchall()
    def fetchone(self): return self.cur.fetchone()

class PgConnection:
    def __init__(self,raw,schema): self.raw=raw;self.schema=schema
    def __enter__(self): self.raw.execute(f'SET search_path TO "{self.schema}",public');return self
    def __exit__(self,typ,val,tb):
        if typ:self.raw.rollback()
        else:self.raw.commit()
        self.raw.close()
    def execute(self,sql,args=()):
        converted=sql.replace('?','%s');is_insert=converted.lstrip().upper().startswith('INSERT INTO')
        table_match=re.match(r'\s*INSERT\s+INTO\s+([A-Za-z0-9_]+)',converted,re.I);table=table_match.group(1).lower() if table_match else ''
        returns_id=is_insert and table not in {'settings'}
        if returns_id and 'RETURNING' not in converted.upper(): converted=converted.rstrip().rstrip(';')+' RETURNING id'
        cur=self.raw.execute(converted,args);last=None
        if returns_id:
            row=cur.fetchone();last=row['id'] if row and 'id' in row else None
        return PgCursor(cur,last)
    def executescript(self,script):
        for stmt in [x.strip() for x in script.split(';') if x.strip()]:self.raw.execute(stmt)

def conn():
    url=os.getenv('PEAWORK_DATABASE_URL','')
    if url.startswith('postgresql'):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e: raise RuntimeError('PostgreSQL mode requires psycopg[binary]') from e
        schema='tenant_'+re.sub(r'[^a-z0-9_]','_',current_tenant().lower())
        raw=psycopg.connect(url,row_factory=dict_row);raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        return PgConnection(raw,schema)
    c=sqlite3.connect(tenant_db_path());c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def init_db():  
    schema = """  
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, employee_id INTEGER, active INTEGER DEFAULT 1);  
    CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL, parent_id INTEGER, manager_employee_id INTEGER, active INTEGER DEFAULT 1);  
    CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, title TEXT NOT NULL, code TEXT UNIQUE NOT NULL, department_id INTEGER, grade TEXT, active INTEGER DEFAULT 1);  
    CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY, employee_no TEXT UNIQUE NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL, work_email TEXT UNIQUE, phone TEXT, date_of_joining TEXT, employment_type TEXT DEFAULT 'Full-time', status TEXT DEFAULT 'Active', department_id INTEGER, position_id INTEGER, manager_id INTEGER, location TEXT DEFAULT 'Gurugram', pan_masked TEXT, bank_masked TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);  
    CREATE TABLE IF NOT EXISTS employee_history(id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, effective_date TEXT NOT NULL, change_type TEXT NOT NULL, old_value TEXT, new_value TEXT, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);  
    CREATE TABLE IF NOT EXISTS leave_types(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL, annual_entitlement REAL NOT NULL, carry_forward_limit REAL DEFAULT 0, active INTEGER DEFAULT 1);  
    CREATE TABLE IF NOT EXISTS leave_balances(id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, leave_type_id INTEGER NOT NULL, year INTEGER NOT NULL, opening REAL DEFAULT 0, accrued REAL DEFAULT 0, used REAL DEFAULT 0, UNIQUE(employee_id,leave_type_id,year));  
    CREATE TABLE IF NOT EXISTS leave_requests(id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, leave_type_id INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, days REAL NOT NULL, reason TEXT, status TEXT DEFAULT 'Pending', approver_id INTEGER, approver_comment TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT);  
    CREATE TABLE IF NOT EXISTS holidays(id INTEGER PRIMARY KEY, holiday_date TEXT UNIQUE NOT NULL, name TEXT NOT NULL, location TEXT DEFAULT 'India', optional INTEGER DEFAULT 0);  
    CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, employee_id INTEGER, category TEXT NOT NULL, title TEXT NOT NULL, filename TEXT, storage_path TEXT, expires_on TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);  
    CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, user_id INTEGER, message TEXT NOT NULL, link TEXT, read_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);  
    CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, details TEXT, ip TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);  
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);  
    CREATE TABLE IF NOT EXISTS requisitions(  
      id INTEGER PRIMARY KEY, req_no TEXT UNIQUE NOT NULL, title TEXT NOT NULL,  
      department_id INTEGER, position_id INTEGER, hiring_manager_id INTEGER,  
      openings INTEGER NOT NULL DEFAULT 1, employment_type TEXT DEFAULT 'Full-time',  
      location TEXT DEFAULT 'Gurugram', target_date TEXT, status TEXT DEFAULT 'Draft',  
      description TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,  
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP  
    );  
    CREATE TABLE IF NOT EXISTS candidates(  
      id INTEGER PRIMARY KEY, candidate_no TEXT UNIQUE NOT NULL, first_name TEXT NOT NULL,  
      last_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, phone TEXT, current_company TEXT,  
      experience_years REAL DEFAULT 0, source TEXT, status TEXT DEFAULT 'Active',  
      resume_filename TEXT, resume_path TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,  
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP  
    );  
    CREATE TABLE IF NOT EXISTS applications(  
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL, requisition_id INTEGER NOT NULL,  
      stage TEXT DEFAULT 'Applied', status TEXT DEFAULT 'Active', applied_on TEXT NOT NULL,  
      owner_employee_id INTEGER, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,  
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(candidate_id,requisition_id)  
    );  
    CREATE TABLE IF NOT EXISTS interviews(  
      id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, interview_type TEXT NOT NULL,  
      scheduled_at TEXT NOT NULL, interviewer_employee_id INTEGER, status TEXT DEFAULT 'Scheduled',  
      rating INTEGER, scorecard TEXT, feedback TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,  
      completed_at TEXT  
    );  
    CREATE TABLE IF NOT EXISTS offers(  
      id INTEGER PRIMARY KEY, application_id INTEGER UNIQUE NOT NULL, offered_title TEXT NOT NULL,  
      offered_salary REAL, currency TEXT DEFAULT 'INR', joining_date TEXT NOT NULL,  
      status TEXT DEFAULT 'Draft', issued_at TEXT, responded_at TEXT, notes TEXT,  
      created_at TEXT DEFAULT CURRENT_TIMESTAMP  
    );  
    CREATE TABLE IF NOT EXISTS onboarding_journeys(  
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, department_id INTEGER,  
      employment_type TEXT DEFAULT 'Any', active INTEGER DEFAULT 1,  
      created_at TEXT DEFAULT CURRENT_TIMESTAMP  
    );  
    CREATE TABLE IF NOT EXISTS onboarding_task_templates(  
      id INTEGER PRIMARY KEY, journey_id INTEGER NOT NULL, title TEXT NOT NULL,  
      category TEXT DEFAULT 'General', owner_role TEXT DEFAULT 'hr',  
      due_offset_days INTEGER DEFAULT 0, required INTEGER DEFAULT 1  
    );  
    CREATE TABLE IF NOT EXISTS onboardings(  
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL, offer_id INTEGER UNIQUE NOT NULL,  
      journey_id INTEGER, employee_id INTEGER, status TEXT DEFAULT 'Planned',  
      start_date TEXT NOT NULL, probation_end_date TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,  
      completed_at TEXT  
    );  
    CREATE TABLE IF NOT EXISTS onboarding_tasks(  
      id INTEGER PRIMARY KEY, onboarding_id INTEGER NOT NULL, template_id INTEGER,  
      title TEXT NOT NULL, category TEXT DEFAULT 'General', owner_user_id INTEGER,  
      due_date TEXT, status TEXT DEFAULT 'Open', completed_at TEXT, notes TEXT  
    );  
    CREATE TABLE IF NOT EXISTS probation_reviews(  
      id INTEGER PRIMARY KEY, onboarding_id INTEGER NOT NULL, review_date TEXT NOT NULL,  
      reviewer_employee_id INTEGER, rating INTEGER, recommendation TEXT,  
      notes TEXT, status TEXT DEFAULT 'Planned', created_at TEXT DEFAULT CURRENT_TIMESTAMP  
    );  
    CREATE INDEX IF NOT EXISTS idx_applications_stage ON applications(stage,status);  
    CREATE INDEX IF NOT EXISTS idx_interviews_schedule ON interviews(scheduled_at,status);  
    CREATE INDEX IF NOT EXISTS idx_onboarding_tasks_due ON onboarding_tasks(due_date,status);  
    CREATE TABLE IF NOT EXISTS shifts(  
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL,  
      start_time TEXT NOT NULL, end_time TEXT NOT NULL, break_minutes INTEGER DEFAULT 60,  
      grace_minutes INTEGER DEFAULT 10, minimum_hours REAL DEFAULT 8, active INTEGER DEFAULT 1  
    );  
    CREATE TABLE IF NOT EXISTS weekly_offs(  
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, monday INTEGER DEFAULT 0,  
      tuesday INTEGER DEFAULT 0, wednesday INTEGER DEFAULT 0, thursday INTEGER DEFAULT 0,  
      friday INTEGER DEFAULT 0, saturday INTEGER DEFAULT 0, sunday INTEGER DEFAULT 1  
    );  
    CREATE TABLE IF NOT EXISTS employee_work_patterns(  
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, shift_id INTEGER NOT NULL,  
      weekly_off_id INTEGER, effective_from TEXT NOT NULL, effective_to TEXT,  
      UNIQUE(employee_id,effective_from)  
    );  
    CREATE TABLE IF NOT EXISTS attendance_records(  
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, attendance_date TEXT NOT NULL,  
      shift_id INTEGER, check_in TEXT, check_out TEXT, worked_hours REAL DEFAULT 0,  
      overtime_hours REAL DEFAULT 0, status TEXT DEFAULT 'Present', source TEXT DEFAULT 'Manual',  
      notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,  
      UNIQUE(employee_id,attendance_date)  
    );  
    CREATE TABLE IF NOT EXISTS overtime_requests(  
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, attendance_date TEXT NOT NULL,  
      requested_hours REAL NOT NULL, reason TEXT, status TEXT DEFAULT 'Pending',  
      approver_employee_id INTEGER, approver_comment TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,  
      decided_at TEXT  
    );  
    CREATE TABLE IF NOT EXISTS timesheets(  
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, week_start TEXT NOT NULL,  
      status TEXT DEFAULT 'Draft', submitted_at TEXT, approver_employee_id INTEGER,  
      approver_comment TEXT, decided_at TEXT, UNIQUE(employee_id,week_start)  
    );  
    CREATE TABLE IF NOT EXISTS timesheet_entries(  
      id INTEGER PRIMARY KEY, timesheet_id INTEGER NOT NULL, work_date TEXT NOT NULL,  
      project_code TEXT, activity TEXT NOT NULL, hours REAL NOT NULL, notes TEXT,  
      UNIQUE(timesheet_id,work_date,project_code,activity)  
    );  
    CREATE TABLE IF NOT EXISTS biometric_devices(  
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, device_code TEXT UNIQUE NOT NULL,  
      location TEXT, active INTEGER DEFAULT 1, last_sync_at TEXT  
    );  
    CREATE TABLE IF NOT EXISTS biometric_imports(  
      id INTEGER PRIMARY KEY, filename TEXT, device_id INTEGER, imported_by INTEGER,  
      rows_received INTEGER DEFAULT 0, rows_imported INTEGER DEFAULT 0,  
      rows_rejected INTEGER DEFAULT 0, errors TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP  
    );  
    CREATE TABLE IF NOT EXISTS leave_accrual_runs(  
      id INTEGER PRIMARY KEY, period TEXT NOT NULL, leave_type_id INTEGER NOT NULL,  
      amount_per_employee REAL NOT NULL, employees_processed INTEGER DEFAULT 0,  
      total_accrued REAL DEFAULT 0, status TEXT DEFAULT 'Completed', run_by INTEGER,  
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(period,leave_type_id)  
    );  
    CREATE TABLE IF NOT EXISTS leave_accrual_entries(  
      id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,  
      leave_balance_id INTEGER NOT NULL, amount REAL NOT NULL, UNIQUE(run_id,employee_id)  
    );  
    CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance_records(attendance_date,status);  
    CREATE INDEX IF NOT EXISTS idx_timesheets_week ON timesheets(week_start,status);  
    CREATE INDEX IF NOT EXISTS idx_overtime_status ON overtime_requests(status,attendance_date);

    CREATE TABLE IF NOT EXISTS performance_cycles(
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, cycle_type TEXT DEFAULT 'Annual',
      start_date TEXT NOT NULL, end_date TEXT NOT NULL, goal_due_date TEXT,
      self_review_due_date TEXT, manager_review_due_date TEXT, status TEXT DEFAULT 'Draft',
      description TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS cycle_participants(
      id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
      manager_employee_id INTEGER, status TEXT DEFAULT 'Not Started',
      overall_self_rating REAL, overall_manager_rating REAL, final_rating REAL,
      self_summary TEXT, manager_summary TEXT, final_summary TEXT,
      submitted_at TEXT, reviewed_at TEXT, finalized_at TEXT,
      UNIQUE(cycle_id,employee_id)
    );
    CREATE TABLE IF NOT EXISTS goals(
      id INTEGER PRIMARY KEY, cycle_id INTEGER, employee_id INTEGER NOT NULL,
      parent_goal_id INTEGER, title TEXT NOT NULL, description TEXT,
      goal_type TEXT DEFAULT 'Individual', metric_name TEXT, baseline REAL,
      target_value REAL, current_value REAL DEFAULT 0, unit TEXT, weight REAL DEFAULT 0,
      start_date TEXT, due_date TEXT, status TEXT DEFAULT 'Draft', progress REAL DEFAULT 0,
      created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS goal_updates(
      id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL, progress REAL NOT NULL,
      current_value REAL, status TEXT, update_text TEXT, updated_by INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS check_ins(
      id INTEGER PRIMARY KEY, cycle_id INTEGER, employee_id INTEGER NOT NULL,
      manager_employee_id INTEGER, check_in_date TEXT NOT NULL,
      accomplishments TEXT, blockers TEXT, support_needed TEXT, next_steps TEXT,
      employee_sentiment TEXT, manager_notes TEXT, status TEXT DEFAULT 'Planned',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS feedback_requests(
      id INTEGER PRIMARY KEY, cycle_id INTEGER, subject_employee_id INTEGER NOT NULL,
      requested_from_employee_id INTEGER NOT NULL, requested_by INTEGER,
      relationship TEXT DEFAULT 'Peer', due_date TEXT, message TEXT,
      status TEXT DEFAULT 'Requested', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      responded_at TEXT, UNIQUE(cycle_id,subject_employee_id,requested_from_employee_id)
    );
    CREATE TABLE IF NOT EXISTS feedback_responses(
      id INTEGER PRIMARY KEY, request_id INTEGER UNIQUE NOT NULL,
      strengths TEXT, improvements TEXT, examples TEXT, rating REAL,
      visibility TEXT DEFAULT 'Manager Only', created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS competencies(
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, category TEXT DEFAULT 'Core',
      description TEXT, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS competency_expectations(
      id INTEGER PRIMARY KEY, competency_id INTEGER NOT NULL, grade TEXT NOT NULL,
      expected_level INTEGER NOT NULL, UNIQUE(competency_id,grade)
    );
    CREATE TABLE IF NOT EXISTS employee_competencies(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, competency_id INTEGER NOT NULL,
      self_level INTEGER, manager_level INTEGER, assessed_on TEXT, evidence TEXT,
      UNIQUE(employee_id,competency_id)
    );
    CREATE TABLE IF NOT EXISTS skills(
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, category TEXT DEFAULT 'Technical',
      description TEXT, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS employee_skills(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, skill_id INTEGER NOT NULL,
      proficiency INTEGER DEFAULT 1, interest INTEGER DEFAULT 0, years_experience REAL DEFAULT 0,
      evidence TEXT, verified_by_employee_id INTEGER, verified_at TEXT,
      UNIQUE(employee_id,skill_id)
    );
    CREATE TABLE IF NOT EXISTS development_plans(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, cycle_id INTEGER,
      title TEXT NOT NULL, career_goal TEXT, target_date TEXT, status TEXT DEFAULT 'Draft',
      owner_employee_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS development_actions(
      id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL, action_type TEXT DEFAULT 'Learning',
      title TEXT NOT NULL, description TEXT, due_date TEXT, status TEXT DEFAULT 'Not Started',
      success_measure TEXT, progress_notes TEXT, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS calibration_sessions(
      id INTEGER PRIMARY KEY, cycle_id INTEGER NOT NULL, name TEXT NOT NULL,
      department_id INTEGER, session_date TEXT NOT NULL, facilitator_employee_id INTEGER,
      status TEXT DEFAULT 'Planned', notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(cycle_id,name)
    );
    CREATE TABLE IF NOT EXISTS calibration_entries(
      id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, participant_id INTEGER NOT NULL,
      proposed_rating REAL, calibrated_rating REAL, performance_bucket TEXT,
      potential_bucket TEXT, rationale TEXT, risk_flag TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(session_id,participant_id)
    );
    CREATE INDEX IF NOT EXISTS idx_goals_employee_cycle ON goals(employee_id,cycle_id,status);
    CREATE INDEX IF NOT EXISTS idx_checkins_employee_date ON check_ins(employee_id,check_in_date);
    CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_requests(requested_from_employee_id,status);
    CREATE INDEX IF NOT EXISTS idx_development_actions_due ON development_actions(due_date,status);
    CREATE INDEX IF NOT EXISTS idx_calibration_cycle ON calibration_sessions(cycle_id,status);

    CREATE TABLE IF NOT EXISTS pay_components(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT UNIQUE NOT NULL,
      component_type TEXT NOT NULL, calculation_type TEXT DEFAULT 'Fixed',
      base_component_code TEXT, default_rate REAL, taxable INTEGER DEFAULT 1,
      statutory INTEGER DEFAULT 0, statutory_rule_name TEXT, active INTEGER DEFAULT 1,
      gl_account TEXT, liability_account TEXT, display_order INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS statutory_rule_versions(
      id INTEGER PRIMARY KEY, rule_name TEXT NOT NULL, jurisdiction TEXT DEFAULT 'India',
      effective_from TEXT NOT NULL, effective_to TEXT, parameters_json TEXT NOT NULL,
      validation_status TEXT DEFAULT 'Draft', validated_by TEXT, validated_at TEXT,
      notes TEXT, UNIQUE(rule_name,effective_from)
    );
    CREATE TABLE IF NOT EXISTS salary_structures(
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL,
      description TEXT, active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS salary_structure_lines(
      id INTEGER PRIMARY KEY, structure_id INTEGER NOT NULL, component_id INTEGER NOT NULL,
      calculation_type TEXT DEFAULT 'Fixed', amount REAL, rate REAL,
      base_component_code TEXT, employer_cost INTEGER DEFAULT 0,
      UNIQUE(structure_id,component_id)
    );
    CREATE TABLE IF NOT EXISTS employee_compensation(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, structure_id INTEGER NOT NULL,
      annual_ctc REAL, effective_from TEXT NOT NULL, effective_to TEXT,
      pay_frequency TEXT DEFAULT 'Monthly', status TEXT DEFAULT 'Active',
      UNIQUE(employee_id,effective_from)
    );
    CREATE TABLE IF NOT EXISTS employee_component_overrides(
      id INTEGER PRIMARY KEY, compensation_id INTEGER NOT NULL, component_id INTEGER NOT NULL,
      amount REAL, rate REAL, notes TEXT, UNIQUE(compensation_id,component_id)
    );
    CREATE TABLE IF NOT EXISTS payroll_periods(
      id INTEGER PRIMARY KEY, period_code TEXT UNIQUE NOT NULL, start_date TEXT NOT NULL,
      end_date TEXT NOT NULL, pay_date TEXT NOT NULL, status TEXT DEFAULT 'Open',
      working_days REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS payroll_inputs(
      id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
      component_id INTEGER NOT NULL, input_type TEXT DEFAULT 'Variable', amount REAL NOT NULL,
      description TEXT, source TEXT DEFAULT 'Manual', approved INTEGER DEFAULT 0,
      approved_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS payroll_attendance_inputs(
      id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
      payable_days REAL NOT NULL, loss_of_pay_days REAL DEFAULT 0,
      overtime_hours REAL DEFAULT 0, source TEXT DEFAULT 'Workforce',
      UNIQUE(period_id,employee_id)
    );
    CREATE TABLE IF NOT EXISTS reimbursement_claims(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, period_id INTEGER,
      category TEXT NOT NULL, amount REAL NOT NULL, description TEXT,
      status TEXT DEFAULT 'Pending', approved_by INTEGER, approved_at TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS payroll_runs(
      id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL, run_number INTEGER NOT NULL DEFAULT 1,
      status TEXT DEFAULT 'Draft', employee_count INTEGER DEFAULT 0,
      gross_pay REAL DEFAULT 0, total_deductions REAL DEFAULT 0,
      net_pay REAL DEFAULT 0, employer_cost REAL DEFAULT 0,
      validation_errors TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      finalized_at TEXT, UNIQUE(period_id,run_number)
    );
    CREATE TABLE IF NOT EXISTS payroll_run_employees(
      id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
      compensation_id INTEGER, payable_days REAL, loss_of_pay_days REAL DEFAULT 0,
      gross_pay REAL DEFAULT 0, deductions REAL DEFAULT 0, net_pay REAL DEFAULT 0,
      employer_cost REAL DEFAULT 0, previous_net_pay REAL DEFAULT 0,
      net_variance REAL DEFAULT 0, variance_percent REAL DEFAULT 0,
      status TEXT DEFAULT 'Calculated', errors TEXT, UNIQUE(run_id,employee_id)
    );
    CREATE TABLE IF NOT EXISTS payroll_run_lines(
      id INTEGER PRIMARY KEY, run_employee_id INTEGER NOT NULL, component_id INTEGER NOT NULL,
      component_code TEXT NOT NULL, component_name TEXT NOT NULL,
      component_type TEXT NOT NULL, amount REAL NOT NULL,
      taxable INTEGER DEFAULT 1, statutory INTEGER DEFAULT 0,
      source TEXT, gl_account TEXT, liability_account TEXT
    );
    CREATE TABLE IF NOT EXISTS payslips(
      id INTEGER PRIMARY KEY, run_employee_id INTEGER UNIQUE NOT NULL,
      payslip_no TEXT UNIQUE NOT NULL, generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
      published_at TEXT, status TEXT DEFAULT 'Generated'
    );
    CREATE TABLE IF NOT EXISTS payroll_accounting_exports(
      id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, export_type TEXT DEFAULT 'Summary',
      total_debits REAL NOT NULL, total_credits REAL NOT NULL,
      balanced INTEGER NOT NULL, exported_by INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_payroll_period_status ON payroll_periods(status,pay_date);
    CREATE INDEX IF NOT EXISTS idx_payroll_inputs_period ON payroll_inputs(period_id,employee_id);
    CREATE INDEX IF NOT EXISTS idx_payroll_run_employee ON payroll_run_employees(run_id,employee_id);
    CREATE INDEX IF NOT EXISTS idx_payroll_lines_employee ON payroll_run_lines(run_employee_id,component_type);

    CREATE TABLE IF NOT EXISTS legal_entities(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      country_code TEXT NOT NULL, registration_no TEXT, tax_id_masked TEXT,
      currency_code TEXT DEFAULT 'INR', timezone TEXT DEFAULT 'Asia/Kolkata',
      active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS work_locations(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      legal_entity_id INTEGER, country_code TEXT NOT NULL, state_region TEXT,
      city TEXT, timezone TEXT, address TEXT, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS cost_centers(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      legal_entity_id INTEGER, parent_id INTEGER, owner_employee_id INTEGER,
      active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS projects(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      legal_entity_id INTEGER, cost_center_id INTEGER, manager_employee_id INTEGER,
      start_date TEXT, end_date TEXT, status TEXT DEFAULT 'Active', client_name TEXT
    );
    CREATE TABLE IF NOT EXISTS job_families(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      description TEXT, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS job_profiles(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
      family_id INTEGER, level_code TEXT, grade TEXT, description TEXT,
      responsibilities TEXT, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS job_skill_requirements(
      id INTEGER PRIMARY KEY, job_profile_id INTEGER NOT NULL, skill_id INTEGER NOT NULL,
      required_proficiency INTEGER NOT NULL DEFAULT 1, critical INTEGER DEFAULT 0,
      UNIQUE(job_profile_id,skill_id)
    );
    CREATE TABLE IF NOT EXISTS employee_global_profiles(
      id INTEGER PRIMARY KEY, employee_id INTEGER UNIQUE NOT NULL,
      global_employee_id TEXT UNIQUE NOT NULL, preferred_name TEXT,
      personal_email TEXT, nationality_country_code TEXT, home_country_code TEXT,
      primary_language TEXT DEFAULT 'en', emergency_contact_name TEXT,
      emergency_contact_phone TEXT, data_residency_region TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS employee_assignments(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, assignment_no TEXT NOT NULL,
      legal_entity_id INTEGER NOT NULL, location_id INTEGER, department_id INTEGER,
      cost_center_id INTEGER, project_id INTEGER, job_profile_id INTEGER,
      position_id INTEGER, manager_id INTEGER, employment_type TEXT DEFAULT 'Full-time',
      worker_type TEXT DEFAULT 'Employee', assignment_status TEXT DEFAULT 'Active',
      effective_from TEXT NOT NULL, effective_to TEXT, primary_assignment INTEGER DEFAULT 1,
      reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(employee_id,assignment_no,effective_from)
    );
    CREATE TABLE IF NOT EXISTS employee_transfers(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, source_assignment_id INTEGER NOT NULL,
      effective_date TEXT NOT NULL, target_legal_entity_id INTEGER,
      target_location_id INTEGER, target_department_id INTEGER,
      target_cost_center_id INTEGER, target_project_id INTEGER,
      target_job_profile_id INTEGER, target_position_id INTEGER,
      target_manager_id INTEGER, reason TEXT, status TEXT DEFAULT 'Draft',
      requested_by INTEGER, approved_by INTEGER, requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
      approved_at TEXT, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS employee_salary_history(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, assignment_id INTEGER,
      currency_code TEXT NOT NULL, annual_base REAL NOT NULL, annual_variable_target REAL DEFAULT 0,
      effective_from TEXT NOT NULL, effective_to TEXT, reason TEXT,
      approval_status TEXT DEFAULT 'Draft', requested_by INTEGER, approved_by INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, approved_at TEXT,
      UNIQUE(employee_id,effective_from)
    );
    CREATE TABLE IF NOT EXISTS lifecycle_event_types(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      category TEXT NOT NULL, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS employee_events(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, event_type_id INTEGER NOT NULL,
      effective_date TEXT NOT NULL, event_status TEXT DEFAULT 'Completed',
      assignment_id INTEGER, details_json TEXT, source_entity_type TEXT,
      source_entity_id TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS journey_definitions(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      journey_type TEXT NOT NULL, legal_entity_id INTEGER, country_code TEXT,
      employment_type TEXT DEFAULT 'Any', active INTEGER DEFAULT 1,
      version INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS journey_task_templates(
      id INTEGER PRIMARY KEY, journey_id INTEGER NOT NULL, title TEXT NOT NULL,
      category TEXT DEFAULT 'General', owner_role TEXT DEFAULT 'hr',
      due_offset_days INTEGER DEFAULT 0, required INTEGER DEFAULT 1,
      completion_evidence_required INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS employee_journeys(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, journey_id INTEGER NOT NULL,
      journey_type TEXT NOT NULL, start_date TEXT NOT NULL, target_date TEXT,
      status TEXT DEFAULT 'Planned', source_entity_type TEXT, source_entity_id TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS employee_journey_tasks(
      id INTEGER PRIMARY KEY, employee_journey_id INTEGER NOT NULL, template_id INTEGER,
      title TEXT NOT NULL, category TEXT DEFAULT 'General', owner_user_id INTEGER,
      due_date TEXT, status TEXT DEFAULT 'Open', evidence TEXT, notes TEXT,
      completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS offboarding_cases(
      id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, assignment_id INTEGER,
      separation_type TEXT NOT NULL, notice_date TEXT, last_working_date TEXT NOT NULL,
      reason TEXT, regretted INTEGER DEFAULT 0, eligible_for_rehire TEXT DEFAULT 'Review',
      status TEXT DEFAULT 'Draft', requested_by INTEGER, approved_by INTEGER,
      requested_at TEXT DEFAULT CURRENT_TIMESTAMP, approved_at TEXT, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS offboarding_task_templates(
      id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
      owner_role TEXT DEFAULT 'hr', due_offset_days INTEGER DEFAULT 0,
      required INTEGER DEFAULT 1, evidence_required INTEGER DEFAULT 0, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS offboarding_tasks(
      id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, template_id INTEGER,
      title TEXT NOT NULL, category TEXT NOT NULL, owner_user_id INTEGER,
      due_date TEXT, status TEXT DEFAULT 'Open', evidence TEXT, notes TEXT,
      completed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_assignments_employee_date ON employee_assignments(employee_id,effective_from,effective_to);
    CREATE INDEX IF NOT EXISTS idx_transfers_status_date ON employee_transfers(status,effective_date);
    CREATE INDEX IF NOT EXISTS idx_events_employee_date ON employee_events(employee_id,effective_date);
    CREATE INDEX IF NOT EXISTS idx_journey_tasks_due ON employee_journey_tasks(due_date,status);
    CREATE INDEX IF NOT EXISTS idx_offboarding_tasks_due ON offboarding_tasks(due_date,status);
    CREATE TABLE IF NOT EXISTS employee_change_requests(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL,request_type TEXT NOT NULL,field_name TEXT NOT NULL,old_value TEXT,new_value TEXT NOT NULL,reason TEXT,status TEXT DEFAULT 'Pending',reviewer_user_id INTEGER,reviewer_comment TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,decided_at TEXT);
    CREATE TABLE IF NOT EXISTS employee_acknowledgements(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL,document_id INTEGER,title TEXT NOT NULL,version TEXT,acknowledged_at TEXT,ip TEXT,UNIQUE(employee_id,document_id,version));
    CREATE TABLE IF NOT EXISTS notification_preferences(id INTEGER PRIMARY KEY,user_id INTEGER UNIQUE NOT NULL,email_enabled INTEGER DEFAULT 1,in_app_enabled INTEGER DEFAULT 1,push_enabled INTEGER DEFAULT 0,digest_frequency TEXT DEFAULT 'Daily',quiet_start TEXT,quiet_end TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS employee_requests(id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL,request_type TEXT NOT NULL,title TEXT NOT NULL,details TEXT,status TEXT DEFAULT 'Open',owner_role TEXT DEFAULT 'hr',owner_user_id INTEGER,due_date TEXT,resolution TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
    CREATE TABLE IF NOT EXISTS inbox_items(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,item_type TEXT NOT NULL,source_entity_type TEXT,source_entity_id TEXT,title TEXT NOT NULL,subtitle TEXT,link TEXT,due_date TEXT,priority TEXT DEFAULT 'Normal',status TEXT DEFAULT 'Open',created_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT,UNIQUE(user_id,item_type,source_entity_type,source_entity_id));
    CREATE INDEX IF NOT EXISTS idx_change_requests_status ON employee_change_requests(status,created_at);
    CREATE INDEX IF NOT EXISTS idx_employee_requests_status ON employee_requests(status,due_date);
    CREATE INDEX IF NOT EXISTS idx_inbox_user_status ON inbox_items(user_id,status,due_date);
    """
    if os.getenv('PEAWORK_DATABASE_URL','').startswith('postgresql'):
        schema=re.sub(r'INTEGER PRIMARY KEY','BIGSERIAL PRIMARY KEY',schema)
        schema=re.sub(r'\bREAL\b','DOUBLE PRECISION',schema)
    with conn() as c:
        c.executescript(schema)
