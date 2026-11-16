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
    """
    with conn() as c:
        c.executescript(schema)
