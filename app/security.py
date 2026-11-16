import base64,hashlib,hmac,json,os,secrets,time
SECRET=os.getenv('PEAWORK_SECRET','dev-only-change-me').encode()
PASSWORD_ITERATIONS=int(os.getenv('PEAWORK_PASSWORD_ITERATIONS','600000'))

def hash_password(password,salt=None,iterations=None):
    salt=salt or secrets.token_hex(16);iterations=iterations or PASSWORD_ITERATIONS
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),iterations)
    return f'pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}'

def verify_password(password,stored):
    try:
        if stored.startswith('pbkdf2_sha256$'):
            _,iterations,salt,_=stored.split('$',3)
            return hmac.compare_digest(hash_password(password,salt,int(iterations)),stored)
        salt,_=stored.split('$',1)
        digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),210000)
        return hmac.compare_digest(salt+'$'+base64.b64encode(digest).decode(),stored)
    except (ValueError,TypeError):return False

def token(uid,role,tenant='local',ttl=28800,jti=None):
    payload={'u':uid,'r':role,'t':tenant,'j':jti or secrets.token_urlsafe(24),'e':int(time.time())+ttl}
    data=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode()
    sig=hmac.new(SECRET,data.encode(),hashlib.sha256).hexdigest();return data+'.'+sig

def untoken(value):
    try:
        data,sig=value.rsplit('.',1)
        if not hmac.compare_digest(sig,hmac.new(SECRET,data.encode(),hashlib.sha256).hexdigest()):return None
        payload=json.loads(base64.urlsafe_b64decode(data));return payload if payload['e']>time.time() else None
    except (ValueError,TypeError,KeyError,json.JSONDecodeError):return None

def csrf_token(session_id='anonymous',ttl=7200):
    nonce=secrets.token_urlsafe(24);expires=int(time.time())+ttl
    data=f'{session_id}.{expires}.{nonce}';sig=hmac.new(SECRET,data.encode(),hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f'{data}.{sig}'.encode()).decode()

def verify_csrf(value,session_id='anonymous'):
    try:
        decoded=base64.urlsafe_b64decode(value.encode()).decode();sid,expires,nonce,sig=decoded.split('.',3)
        data=f'{sid}.{expires}.{nonce}'
        return sid==session_id and int(expires)>time.time() and hmac.compare_digest(sig,hmac.new(SECRET,data.encode(),hashlib.sha256).hexdigest())
    except (ValueError,TypeError):return False
