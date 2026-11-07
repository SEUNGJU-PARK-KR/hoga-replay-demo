@echo off
cd /d "%~dp0"
set "HOGA_REPLAY_ROOT=%~dp0data"
set "HOGA_VIEWER_PORT=8775"
start "" http://127.0.0.1:8775/
start "Hoga Viewer" /min pythonw hoga_server.py
