import importlib.util
import os
import sys
checks=[]
def check(name,ok,detail): checks.append((name,ok,detail))
check('PEAWORK_SECRET',len(os.getenv('PEAWORK_SECRET',''))>=32 and os.getenv('PEAWORK_SECRET')!='dev-only-change-me','must be a non-default value of at least 32 characters')
check('Bootstrap admin password',len(os.getenv('PEAWORK_BOOTSTRAP_ADMIN_PASSWORD',''))>=16 and os.getenv('PEAWORK_BOOTSTRAP_ADMIN_PASSWORD')!='ChangeMe123!','must be a unique non-default value of at least 16 characters')
check('Malware scanner',os.getenv('PEAWORK_UPLOAD_SCAN_REQUIRED','true').lower()!='true' or bool(os.getenv('PEAWORK_CLAMAV_HOST')),'required uploads need PEAWORK_CLAMAV_HOST')
check('PostgreSQL driver',importlib.util.find_spec('psycopg') is not None,'install requirements.txt')
check('Object storage driver',importlib.util.find_spec('boto3') is not None,'install requirements.txt')
check('Database URL',os.getenv('PEAWORK_DATABASE_URL','').startswith('postgresql'),'set PEAWORK_DATABASE_URL')
check('Object bucket',bool(os.getenv('PEAWORK_OBJECT_BUCKET')),'set PEAWORK_OBJECT_BUCKET')
for name,ok,detail in checks:print(('PASS' if ok else 'FAIL'),name,'-',detail)
sys.exit(0 if all(x[1] for x in checks) else 1)
