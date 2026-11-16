import importlib.metadata,json,subprocess,sys,urllib.request
# Offline inventory always works. Vulnerability decisions require an approved scanner/database.
packages=sorted([{'name':d.metadata['Name'],'version':d.version} for d in importlib.metadata.distributions()],key=lambda x:x['name'].lower())
open('dependency-inventory.json','w').write(json.dumps(packages,indent=2));print(f'Wrote dependency-inventory.json with {len(packages)} packages')
print('UNVERIFIED: run an organization-approved dependency vulnerability scanner and attach its report to SEC-SAST.')
