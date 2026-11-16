import os, shutil
from datetime import datetime
from pathlib import Path
from .db import current_tenant
from .cloud_control import control_conn,resolve_tenant

BASE=Path(__file__).resolve().parent.parent

def safe_name(filename): return ''.join(c for c in filename if c.isalnum() or c in '._-')[:100] or 'file'
def tenant_storage_config():
    tenant=resolve_tenant(slug=current_tenant())
    if not tenant:return {}
    with control_conn() as c:
        row=c.execute('SELECT * FROM storage_configurations WHERE tenant_id=?',(tenant['id'],)).fetchone()
        return dict(row) if row else {}

def store_upload(file_obj,filename,category='document'):
    cfg=tenant_storage_config();prefix=cfg.get('prefix') or current_tenant()
    key=f"{prefix}/{category}/{datetime.utcnow().strftime('%Y/%m/%d/%H%M%S%f')}_{safe_name(filename)}"
    backend=os.getenv('PEAWORK_STORAGE_BACKEND',cfg.get('backend','local')).lower()
    if backend in ('s3','object','object-storage'):
        try: import boto3
        except ImportError as e: raise RuntimeError('Object storage mode requires boto3') from e
        bucket=os.getenv('PEAWORK_OBJECT_BUCKET') or cfg.get('bucket')
        if not bucket:raise RuntimeError('Object storage bucket is not configured')
        client=boto3.client('s3',endpoint_url=os.getenv('PEAWORK_OBJECT_ENDPOINT'),region_name=os.getenv('PEAWORK_OBJECT_REGION') or cfg.get('region'))
        client.upload_fileobj(file_obj,bucket,key,ExtraArgs={'ServerSideEncryption':os.getenv('PEAWORK_OBJECT_ENCRYPTION','AES256')})
        return f's3://{bucket}/{key}'
    target=BASE/'uploads'/key;target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('wb') as f:shutil.copyfileobj(file_obj,f)
    return str(target)
