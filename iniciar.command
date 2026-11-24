#!/bin/bash
cd "$(dirname "$0")"
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
(sleep 3; open http://localhost:8080 2>/dev/null || xdg-open http://localhost:8080) &
python3 app.py
