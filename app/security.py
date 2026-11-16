import hashlib,hmac,secrets,time,base64,json,os  
SECRET=os.getenv('PEAWORK_SECRET','dev-only-change-me').encode()  
def hash_password(p,salt=None):  
    salt=salt or secrets.token_hex(16); d=hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),210000)  
    return salt+'$'+base64.b64encode(d).decode()  
def verify_password(p,stored):  
    try:  
        salt,_=stored.split('$',1); return hmac.compare_digest(hash_password(p,salt),stored)  
    except: return False  
def token(uid,role,ttl=28800):  
    data=base64.urlsafe_b64encode(json.dumps({'u':uid,'r':role,'e':int(time.time())+ttl}).encode()).decode()  
    sig=hmac.new(SECRET,data.encode(),hashlib.sha256).hexdigest(); return data+'.'+sig  
def untoken(t):  
    try:  
        data,sig=t.rsplit('.',1)  
        if not hmac.compare_digest(sig,hmac.new(SECRET,data.encode(),hashlib.sha256).hexdigest()): return None  
        x=json.loads(base64.urlsafe_b64decode(data)); return x if x['e']>time.time() else None  
    except: return None
