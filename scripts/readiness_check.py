import os,sqlite3,sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent;db=Path(os.getenv('PEAWORK_CONTROL_DATABASE',root/'data'/'control.db'))
if not db.exists():print('FAIL control database not found');sys.exit(1)
c=sqlite3.connect(db);c.row_factory=sqlite3.Row
rows=c.execute('SELECT control_code,title,status,evidence_reference,reviewed_by FROM readiness_evidence ORDER BY control_code').fetchall()
for x in rows:print(('PASS' if x['status']=='Verified' else 'FAIL'),x['control_code'],x['status'],'-',x['evidence_reference'] or 'no evidence')
open_events=c.execute("SELECT COUNT(*) n FROM security_events WHERE status='Open' AND severity IN ('Critical','High')").fetchone()['n'];print(('PASS' if open_events==0 else 'FAIL'),'open high/critical security events:',open_events)
sys.exit(0 if rows and all(x['status']=='Verified' for x in rows) and open_events==0 else 1)
