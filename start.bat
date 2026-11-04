@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Work Agent Launcher
cd /d "%~dp0"

rem ============================================================
rem  Work Agent one-click launcher (frontend + backend, CopilotKit)
rem    1. AgentScope agent service (backend\run.py, Python, port 8790)
rem    2. React build (web\ -> web\dist)
rem    3. Page + gateway (backend\server.js, Node, port 8787)
rem
rem  IMPORTANT: keep this file ASCII-only.
rem  cmd.exe reads batch files in fixed-size buffers; a multi-byte
rem  character straddling a buffer boundary gets split and the rest
rem  of that line is executed as a command ("... is not recognized").
rem  Chinese text here is therefore fragile -- messages are English.
rem ============================================================

rem ---------- 1. locate python ----------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\WorkBuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%LOCALAPPDATA%\WorkBuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY if exist "C:\Users\m1821\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=C:\Users\m1821\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not defined PY (
  rem no virtualenv in this folder: create one from system python
  where python >nul 2>nul
  if errorlevel 1 goto no_python
  echo [1/5] Creating virtualenv .venv ...
  python -m venv .venv
  if errorlevel 1 goto no_python
  set "PY=.venv\Scripts\python.exe"
)

rem ---------- 2. python dependencies (AgentScope must be 2.0+) ----------
echo [2/5] Checking Python dependencies (slow on first run) ...
"%PY%" -c "import agentscope, fastapi, uvicorn, httpx, sqlalchemy, aiosqlite, sys; sys.exit(0 if int(agentscope.__version__.split('.')[0]) >= 2 else 1)" >nul 2>nul
if errorlevel 1 (
  echo       Installing / upgrading dependencies to AgentScope 2.0 ...
  "%PY%" -m pip install -U -r backend\requirements.txt
  if errorlevel 1 (
    echo [ERROR] Dependency install failed. Run manually:
    echo   "%PY%" -m pip install -U -r backend\requirements.txt
    pause
    exit /b 1
  )
)

rem ---------- 3. build react frontend ----------
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js not found. Install it first: https://nodejs.org
  pause
  exit /b 1
)

echo [3/5] Checking Node dependencies and building frontend ...
if not exist "node_modules\@copilotkit\runtime" (
  call npm install --no-audit --no-fund
  if errorlevel 1 echo [WARN] Root dependency install failed, CopilotKit runtime may be unavailable
)
if not exist "web\node_modules" (
  pushd web
  call npm install --no-audit --no-fund
  popd
  if errorlevel 1 (
    echo [ERROR] Frontend dependency install failed. Run manually: cd web ^&^& npm install
    pause
    exit /b 1
  )
)
if not exist "web\dist\index.html" (
  pushd web
  call npm run build
  popd
  if errorlevel 1 (
    echo [ERROR] Frontend build failed. Run manually: cd web ^&^& npm run build
    pause
    exit /b 1
  )
)

rem ---------- 4. free ports held by leftover processes ----------
rem  Skipping this leaves the new windows dying with EADDRINUSE while
rem  an old process keeps serving the page -- hard to notice.
echo [4/5] Checking ports (8787 / 8790) ...
call :free_port 8787
call :free_port 8790

rem ---------- 5. start both services ----------
echo [5/5] Starting services ...
start "AgentScope Agent Service" "%PY%" backend\run.py
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul
start "Work Agent Page Service" node backend\server.js
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul
start "" http://localhost:8787

echo.
echo   Two windows launched:
echo     - AgentScope agent service (planner / interviewer / evaluator)
echo     - Page service http://localhost:8787 (React + CopilotKit runtime)
echo.
echo   Dev mode (hot reload): cd web ^&^& npm run dev, then open http://localhost:5173
echo   Closing a window stops that service; close both to stop everything.
echo   This window can be closed now.
pause
exit /b 0

rem ============================================================
rem  Subroutine: free a TCP port
rem    usage: call :free_port 8787
rem    Only processes belonging to this project are stopped
rem    (node.exe / python.exe / pythonw.exe). Anything else is
rem    reported and left untouched.
rem ============================================================
:free_port
set "PORT=%~1"
set "FOUND="
set "LASTPID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% " ^| findstr /I "LISTENING"') do (
  rem  0.0.0.0 and [::] rows share the same PID and are adjacent
  if not "%%P"=="!LASTPID!" (
    set "LASTPID=%%P"
    set "FOUND=1"
    set "PNAME="
    for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %%P" /NH /FO CSV 2^>nul') do set "PNAME=%%~N"
    if not defined PNAME set "PNAME=unknown"
    set "MINE="
    if /i "!PNAME!"=="node.exe" set "MINE=1"
    if /i "!PNAME!"=="python.exe" set "MINE=1"
    if /i "!PNAME!"=="pythonw.exe" set "MINE=1"
    if defined MINE (
      taskkill /F /PID %%P >nul 2>nul
      if errorlevel 1 (
        echo        [WARN] Failed to stop !PNAME! ^(PID %%P^). Try running as administrator.
      ) else (
        echo        Stopped !PNAME! ^(PID %%P^), port %PORT% released
      )
    ) else (
      echo        [SKIP] Port %PORT% is held by !PNAME! ^(PID %%P^), not part of this project.
      echo        Start may fail -- close it manually first.
    )
  )
)
if not defined FOUND echo        Port %PORT% is free
"%SystemRoot%\System32\ping.exe" -n 2 127.0.0.1 >nul
exit /b 0

:no_python
echo [ERROR] No usable Python 3.11+ found (AgentScope 2.0 requires it),
echo         and no virtualenv could be created.
echo   Install Python first: https://www.python.org/downloads/
echo   Tick "Add Python to PATH" during setup, then run this file again.
pause
exit /b 1
