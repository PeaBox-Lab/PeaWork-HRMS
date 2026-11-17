import hashlib
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path

BASE=Path(__file__).resolve().parent.parent
CONTROL_DB=Path(os.getenv('PEAWORK_CONTROL_DATABASE',str(BASE/'data'/'control.db')))
CONTROL_DB.parent.mkdir(parents=True,exist_ok=True)

def control_conn():
    c=sqlite3.connect(CONTROL_DB);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def init_control_db():
    schema='''
    CREATE TABLE IF NOT EXISTS tenants(id INTEGER PRIMARY KEY,slug TEXT UNIQUE NOT NULL,name TEXT NOT NULL,status TEXT DEFAULT 'Active',region TEXT DEFAULT 'india-central',database_mode TEXT DEFAULT 'Isolated',storage_prefix TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS tenant_domains(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,domain TEXT UNIQUE NOT NULL,status TEXT DEFAULT 'Pending',verification_token TEXT NOT NULL,verified_at TEXT,tls_status TEXT DEFAULT 'Pending');
    CREATE TABLE IF NOT EXISTS tenant_memberships(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,username TEXT NOT NULL,role TEXT DEFAULT 'member',status TEXT DEFAULT 'Active',UNIQUE(tenant_id,username));
    CREATE TABLE IF NOT EXISTS subscription_plans(id INTEGER PRIMARY KEY,code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,employee_limit INTEGER,monthly_price REAL DEFAULT 0,features_json TEXT DEFAULT '{}',active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS tenant_subscriptions(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,plan_id INTEGER NOT NULL,status TEXT DEFAULT 'Trial',starts_on TEXT NOT NULL,renews_on TEXT,employee_limit_override INTEGER,UNIQUE(tenant_id));
    CREATE TABLE IF NOT EXISTS identity_providers(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,provider_type TEXT NOT NULL,name TEXT NOT NULL,issuer TEXT,client_id TEXT,metadata_url TEXT,enabled INTEGER DEFAULT 0,configuration_status TEXT DEFAULT 'Draft',UNIQUE(tenant_id,name));
    CREATE TABLE IF NOT EXISTS scim_credentials(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,name TEXT NOT NULL,token_hash TEXT NOT NULL,token_prefix TEXT NOT NULL,status TEXT DEFAULT 'Active',created_at TEXT DEFAULT CURRENT_TIMESTAMP,last_used_at TEXT,UNIQUE(tenant_id,name));
    CREATE TABLE IF NOT EXISTS storage_configurations(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,backend TEXT DEFAULT 'Local',bucket TEXT,prefix TEXT,region TEXT,encryption_required INTEGER DEFAULT 1,status TEXT DEFAULT 'Draft',UNIQUE(tenant_id));
    CREATE TABLE IF NOT EXISTS backup_policies(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,frequency TEXT DEFAULT 'Daily',retention_days INTEGER DEFAULT 30,pitr_enabled INTEGER DEFAULT 0,rpo_minutes INTEGER DEFAULT 1440,rto_minutes INTEGER DEFAULT 240,status TEXT DEFAULT 'Active',UNIQUE(tenant_id));
    CREATE TABLE IF NOT EXISTS backup_runs(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,backup_type TEXT DEFAULT 'Full',status TEXT DEFAULT 'Requested',location TEXT,started_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT,size_bytes INTEGER,error TEXT);
    CREATE TABLE IF NOT EXISTS migration_jobs(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,source_type TEXT DEFAULT 'Local SQLite',source_path TEXT,status TEXT DEFAULT 'Draft',preflight_json TEXT,records_migrated INTEGER DEFAULT 0,started_at TEXT,completed_at TEXT,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS service_checks(id INTEGER PRIMARY KEY,tenant_id INTEGER,service TEXT NOT NULL,status TEXT NOT NULL,latency_ms REAL,details TEXT,checked_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS cloud_audit(id INTEGER PRIMARY KEY,tenant_id INTEGER,actor TEXT NOT NULL,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT,details TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS session_registry(id INTEGER PRIMARY KEY,tenant_slug TEXT NOT NULL,user_id INTEGER NOT NULL,jti TEXT UNIQUE NOT NULL,issued_at TEXT DEFAULT CURRENT_TIMESTAMP,expires_at INTEGER NOT NULL,revoked_at TEXT,ip TEXT,user_agent TEXT);
    CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY,tenant_slug TEXT NOT NULL,username TEXT NOT NULL,ip TEXT,success INTEGER DEFAULT 0,attempted_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS security_events(id INTEGER PRIMARY KEY,tenant_id INTEGER,severity TEXT NOT NULL,event_type TEXT NOT NULL,actor TEXT,ip TEXT,details TEXT,status TEXT DEFAULT 'Open',created_at TEXT DEFAULT CURRENT_TIMESTAMP,resolved_at TEXT);
    CREATE TABLE IF NOT EXISTS retention_policies(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,data_category TEXT NOT NULL,retention_days INTEGER NOT NULL,action TEXT DEFAULT 'Delete',legal_hold INTEGER DEFAULT 0,status TEXT DEFAULT 'Draft',approved_by TEXT,approved_at TEXT,UNIQUE(tenant_id,data_category));
    CREATE TABLE IF NOT EXISTS retention_runs(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,status TEXT DEFAULT 'Running',records_reviewed INTEGER DEFAULT 0,records_acted INTEGER DEFAULT 0,details TEXT,started_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
    CREATE TABLE IF NOT EXISTS privacy_requests(id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,request_type TEXT NOT NULL,data_subject TEXT NOT NULL,identity_status TEXT DEFAULT 'Pending',received_on TEXT NOT NULL,due_on TEXT,status TEXT DEFAULT 'Received',owner_role TEXT DEFAULT 'Privacy Officer',decision TEXT,evidence TEXT,completed_at TEXT);
    CREATE TABLE IF NOT EXISTS breach_incidents(id INTEGER PRIMARY KEY,tenant_id INTEGER,severity TEXT NOT NULL,detected_at TEXT NOT NULL,description TEXT NOT NULL,systems_affected TEXT,data_categories TEXT,containment TEXT,assessment_status TEXT DEFAULT 'Assessing',notification_decision TEXT,owner_role TEXT DEFAULT 'Incident Commander',status TEXT DEFAULT 'Open',closed_at TEXT);
    CREATE TABLE IF NOT EXISTS dr_exercises(id INTEGER PRIMARY KEY,tenant_id INTEGER,exercise_date TEXT NOT NULL,scenario TEXT NOT NULL,target_rpo_minutes INTEGER,target_rto_minutes INTEGER,actual_rpo_minutes INTEGER,actual_rto_minutes INTEGER,restore_verified INTEGER DEFAULT 0,evidence TEXT,status TEXT DEFAULT 'Planned',approved_by TEXT);
    CREATE TABLE IF NOT EXISTS load_test_runs(id INTEGER PRIMARY KEY,tenant_id INTEGER,scenario TEXT NOT NULL,concurrency INTEGER NOT NULL,duration_seconds INTEGER NOT NULL,requests INTEGER DEFAULT 0,errors INTEGER DEFAULT 0,p50_ms REAL,p95_ms REAL,p99_ms REAL,status TEXT DEFAULT 'Recorded',evidence TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS readiness_evidence(id INTEGER PRIMARY KEY,control_code TEXT NOT NULL,title TEXT NOT NULL,category TEXT NOT NULL,status TEXT DEFAULT 'Pending',evidence_reference TEXT,reviewer_role TEXT,reviewed_by TEXT,reviewed_at TEXT,notes TEXT,UNIQUE(control_code));
    CREATE TABLE IF NOT EXISTS accessibility_findings(id INTEGER PRIMARY KEY,page_name TEXT NOT NULL,standard TEXT DEFAULT 'WCAG 2.2 AA',finding TEXT NOT NULL,severity TEXT NOT NULL,status TEXT DEFAULT 'Open',evidence TEXT,owner_role TEXT DEFAULT 'Product Engineering',created_at TEXT DEFAULT CURRENT_TIMESTAMP,resolved_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_sessions_tenant_user ON session_registry(tenant_slug,user_id,revoked_at);
    CREATE INDEX IF NOT EXISTS idx_login_attempts_window ON login_attempts(tenant_slug,username,attempted_at);
    CREATE INDEX IF NOT EXISTS idx_privacy_status ON privacy_requests(tenant_id,status,due_on);
    CREATE INDEX IF NOT EXISTS idx_security_events ON security_events(status,severity,created_at);
    '''
    with control_conn() as c:
        c.executescript(schema)
        if not c.execute('SELECT id FROM subscription_plans LIMIT 1').fetchone():
            c.execute("INSERT INTO subscription_plans(code,name,employee_limit,monthly_price,features_json) VALUES('BETA','Cloud Beta',2000,0,?)",(json.dumps({'sso':True,'scim':True,'custom_domains':True,'backups':True}),))
        if not c.execute('SELECT id FROM readiness_evidence LIMIT 1').fetchone():
            for code,title,category,reviewer in [
                ('SEC-ASVS-L2','OWASP ASVS Level 2 independent verification','Security','Independent Security Assessor'),
                ('SEC-PENTEST','External penetration test','Security','Independent Penetration Tester'),
                ('SEC-SAST','SAST and dependency scan','Security','Security Engineering'),
                ('A11Y-AA','Accessibility audit against WCAG AA','Accessibility','Accessibility Specialist'),
                ('L10N-REVIEW','Localization and language review','Localization','Localization Reviewer'),
                ('DR-RESTORE','Disaster-recovery restore exercise','Resilience','Business Continuity Owner'),
                ('PERF-LOAD','Load test to approved concurrency','Performance','Performance Engineering'),
                ('PRIV-DPDP','DPDP workflows and notices validated','Privacy','Qualified Privacy Counsel'),
            ]: c.execute('INSERT INTO readiness_evidence(control_code,title,category,reviewer_role) VALUES(?,?,?,?)',(code,title,category,reviewer))
        if not c.execute("SELECT id FROM tenants WHERE slug='local'").fetchone():
            tenant_id=c.execute("INSERT INTO tenants(slug,name,status,region,database_mode,storage_prefix) VALUES('local','Local Control Tenant','Active','local','Local','local')").lastrowid
            plan=c.execute("SELECT id FROM subscription_plans WHERE code='BETA'").fetchone()['id']
            c.execute("INSERT INTO tenant_subscriptions(tenant_id,plan_id,status,starts_on) VALUES(?,?,'Active',date('now'))",(tenant_id,plan))
            c.execute("INSERT INTO storage_configurations(tenant_id,backend,prefix,status) VALUES(?,'Local','local','Active')",(tenant_id,))
            c.execute("INSERT INTO backup_policies(tenant_id,frequency,retention_days,pitr_enabled) VALUES(?,'Daily',30,0)",(tenant_id,))

def clean_slug(value):
    value=re.sub(r'[^a-z0-9-]+','-',value.lower()).strip('-')
    if not value or len(value)>50: raise ValueError('Tenant slug must contain 1-50 lowercase letters, numbers or hyphens')
    return value

def hash_token(token): return hashlib.sha256(token.encode()).hexdigest()
def issue_scim_token():
    token='pwscim_'+secrets.token_urlsafe(32)
    return token,hash_token(token),token[:14]

def cloud_audit(actor,action,etype,eid='',details='',tenant_id=None):
    with control_conn() as c:c.execute('INSERT INTO cloud_audit(tenant_id,actor,action,entity_type,entity_id,details) VALUES(?,?,?,?,?,?)',(tenant_id,actor,action,etype,str(eid),details))

def resolve_tenant(host=None,slug=None):
    init_control_db()
    with control_conn() as c:
        if slug:
            r=c.execute("SELECT * FROM tenants WHERE slug=? AND status='Active'",(slug,)).fetchone()
        elif host:
            domain=host.split(':')[0].lower();r=c.execute("SELECT t.* FROM tenants t JOIN tenant_domains d ON d.tenant_id=t.id WHERE d.domain=? AND d.status='Verified' AND t.status='Active'",(domain,)).fetchone()
        else:r=c.execute("SELECT * FROM tenants WHERE status='Active' ORDER BY id LIMIT 1").fetchone()
        return dict(r) if r else None
