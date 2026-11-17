import os
import shutil
import tempfile
import socket
import struct
from datetime import datetime
from pathlib import Path
from .db import current_tenant
from .cloud_control import control_conn,resolve_tenant

BASE=Path(__file__).resolve().parent.parent
ALLOWED_EXTENSIONS={'.pdf','.png','.jpg','.jpeg','.doc','.docx','.csv','.txt'}
ALLOWED_MIME_PREFIXES=('application/pdf','image/png','image/jpeg','application/msword','application/vnd.openxmlformats-officedocument','text/csv','text/plain','application/octet-stream')

def safe_name(filename):return ''.join(c for c in filename if c.isalnum() or c in '._-')[:100] or 'file'
def tenant_storage_config():
    tenant=resolve_tenant(slug=current_tenant())
    if not tenant:return {}
    with control_conn() as c:
        row=c.execute('SELECT * FROM storage_configurations WHERE tenant_id=?',(tenant['id'],)).fetchone();return dict(row) if row else {}
def validated_stream(file_obj,filename,content_type=None):
    extension=Path(filename).suffix.lower();maximum=int(os.getenv('PEAWORK_MAX_UPLOAD_BYTES',str(10*1024*1024)))
    if extension not in ALLOWED_EXTENSIONS:raise ValueError(f'File type {extension or "unknown"} is not allowed')
    if content_type and not any(content_type.startswith(x) for x in ALLOWED_MIME_PREFIXES):raise ValueError('File content type is not allowed')
    staged=tempfile.SpooledTemporaryFile(max_size=2*1024*1024);size=0
    while True:
        chunk=file_obj.read(1024*1024)
        if not chunk:break
        size+=len(chunk)
        if size>maximum:staged.close();raise ValueError(f'File exceeds the {maximum} byte upload limit')
        staged.write(chunk)
    staged.seek(0);return staged,size
def malware_scan(stream):
    host=os.getenv('PEAWORK_CLAMAV_HOST');required=os.getenv('PEAWORK_UPLOAD_SCAN_REQUIRED','false').lower()=='true'
    if not host:
        if required:raise RuntimeError('Malware scanning is required but no scanner is configured')
        return 'Not configured'
    port=int(os.getenv('PEAWORK_CLAMAV_PORT','3310'));sock=socket.create_connection((host,port),timeout=15)
    try:
        sock.sendall(b'zINSTREAM\0')
        while True:
            chunk=stream.read(65536)
            if not chunk:break
            sock.sendall(struct.pack('!I',len(chunk))+chunk)
        sock.sendall(struct.pack('!I',0));result=sock.recv(4096).decode(errors='replace')
        if 'FOUND' in result:raise ValueError('Malware scan rejected the upload')
        if 'OK' not in result:raise RuntimeError('Malware scanner did not return a clean result')
        return result.strip()
    finally:sock.close();stream.seek(0)
def store_upload(file_obj,filename,category='document',content_type=None):
    cfg=tenant_storage_config();prefix=cfg.get('prefix') or current_tenant();stream,size=validated_stream(file_obj,filename,content_type);malware_scan(stream)
    key=f"{prefix}/{category}/{datetime.utcnow().strftime('%Y/%m/%d/%H%M%S%f')}_{safe_name(filename)}";backend=os.getenv('PEAWORK_STORAGE_BACKEND',cfg.get('backend','local')).lower()
    try:
        if backend in ('s3','object','object-storage'):
            try:import boto3
            except ImportError as e:raise RuntimeError('Object storage mode requires boto3') from e
            bucket=os.getenv('PEAWORK_OBJECT_BUCKET') or cfg.get('bucket')
            if not bucket:raise RuntimeError('Object storage bucket is not configured')
            client=boto3.client('s3',endpoint_url=os.getenv('PEAWORK_OBJECT_ENDPOINT'),region_name=os.getenv('PEAWORK_OBJECT_REGION') or cfg.get('region'))
            client.upload_fileobj(stream,bucket,key,ExtraArgs={'ServerSideEncryption':os.getenv('PEAWORK_OBJECT_ENCRYPTION','AES256'),'ContentType':content_type or 'application/octet-stream'})
            return f's3://{bucket}/{key}',size
        target=BASE/'uploads'/key;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('wb') as f:shutil.copyfileobj(stream,f)
        return str(target),size
    finally:stream.close()
