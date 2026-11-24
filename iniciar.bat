@echo off
cd /d "%~dp0"
if not exist venv (
  py -m venv venv
)
call venv\Scripts\activate
pip install -r requirements.txt
start "" http://localhost:8080
py app.py
pause
