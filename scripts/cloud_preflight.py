import importlib.util, os, sys
from pathlib import Path
checks=[]
def check(name,ok,detail): checks.append((name,ok,detail))
check('PEAWORK_SECRET',len(os.getenv('PEAWORK_SECRET',''))>=32,'must be at least 32 characters')
check('PostgreSQL driver',importlib.util.find_spec('psycopg') is not None,'install requirements.txt')
check('Object storage driver',importlib.util.find_spec('boto3') is not None,'install requirements.txt')
check('Database URL',os.getenv('PEAWORK_DATABASE_URL','').startswith('postgresql'),'set PEAWORK_DATABASE_URL')
check('Object bucket',bool(os.getenv('PEAWORK_OBJECT_BUCKET')),'set PEAWORK_OBJECT_BUCKET')
for name,ok,detail in checks:print(('PASS' if ok else 'FAIL'),name,'-',detail)
sys.exit(0 if all(x[1] for x in checks) else 1)
