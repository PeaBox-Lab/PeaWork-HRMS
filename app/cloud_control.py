import hashlib, json, os, re, secrets, sqlite3
from datetime import datetime
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
    '''
    with control_conn() as c:
        c.executescript(schema)
        if not c.execute('SELECT id FROM subscription_plans LIMIT 1').fetchone():
            c.execute("INSERT INTO subscription_plans(code,name,employee_limit,monthly_price,features_json) VALUES('BETA','Cloud Beta',2000,0,?)",(json.dumps({'sso':True,'scim':True,'custom_domains':True,'backups':True}),))

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
