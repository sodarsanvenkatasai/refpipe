@echo off
cd /d "%~dp0"
".env\Scripts\python.exe" -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 8000)); s.close()" >nul 2>&1
if errorlevel 1 (
  echo RefPipe is already running at http://127.0.0.1:8000/
  start "" "http://127.0.0.1:8000/"
  exit /b 0
)
echo Starting RefPipe at http://127.0.0.1:8000/ ...
echo Close the minimized "RefPipe server" window to stop it.
start "RefPipe server" /MIN ".env\Scripts\python.exe" manage.py runserver 127.0.0.1:8000
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8000/"