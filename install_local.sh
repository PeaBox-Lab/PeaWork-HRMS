#!/bin/sh    
set -eu    
python3 -m venv .venv    
. .venv/bin/activate    
pip install -r requirements.txt    
printf '    
Installed. Run: . .venv/bin/activate && python run.py    
Open: http://127.0.0.1:8090    
'
