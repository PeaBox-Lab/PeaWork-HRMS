@echo off
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
echo Installed. Run: .venv\Scripts\activate ^&^& python run.py
