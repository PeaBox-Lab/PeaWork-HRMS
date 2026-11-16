import sqlite3, os  
from pathlib import Path  
BASE = Path(__file__).resolve().parent.parent  
DB = Path(os.getenv("PEAWORK_DATABASE", str(BASE / "data" / "peawork.db")))  
DB.parent.mkdir(parents=True, exist_ok=True)  
def conn():  
    c = sqlite3.connect(DB)  
    c.row_factory = sqlite3.Row  
    c.execute("PRAGMA foreign_keys=ON")  
    return c  
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
    """  
    with conn() as c:  
        c.executescript(schema)
