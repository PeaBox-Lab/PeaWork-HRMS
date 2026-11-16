import sqlite3, os
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
DB=Path(os.getenv("PEAWORK_DATABASE", str(BASE/'data'/'peawork.db')))
DB.parent.mkdir(parents=True,exist_ok=True)

def conn():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init_db():
    schema='''
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
    '''
    with conn() as c: c.executescript(schema)
