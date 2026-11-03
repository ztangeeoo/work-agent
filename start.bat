@echo off
chcp 65001 >nul
title 工作智能体集合 · 启动器
cd /d "%~dp0"

rem ============================================================
rem  工作智能体集合 一键启动（前后端分离 + CopilotKit）
rem    1. AgentScope 智能体服务（agent_server.py，Python，端口 8790）
rem    2. React 前端构建（web/，Vite，产物输出到 web/dist）
rem    3. 页面与转发服务（server.js，Node，端口 8787，托管 web/dist + CopilotKit runtime）
rem ============================================================

rem ---------- 1. 找到可用的 Python ----------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\WorkBuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%LOCALAPPDATA%\WorkBuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY if exist "C:\Users\m1821\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=C:\Users\m1821\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not defined PY (
  rem 项目内没有虚拟环境：用系统 Python 创建一个
  where python >nul 2>nul
  if errorlevel 1 goto no_python
  echo [1/4] 创建 Python 虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 goto no_python
  set "PY=.venv\Scripts\python.exe"
)

rem ---------- 2. 安装/确认依赖（AgentScope 必须 2.0+） ----------
echo [2/4] 检查 Python 依赖（首次较慢，请耐心等待）...
"%PY%" -c "import agentscope, fastapi, uvicorn, httpx, sqlalchemy, aiosqlite, sys; sys.exit(0 if int(agentscope.__version__.split('.')[0]) >= 2 else 1)" >nul 2>nul
if errorlevel 1 (
  echo      安装 / 升级依赖到 AgentScope 2.0 ...
  "%PY%" -m pip install -U -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请手动执行：
    echo   "%PY%" -m pip install -U -r requirements.txt
    pause
    exit /b 1
  )
)

rem ---------- 3. 构建 React 前端（CopilotKit + Vite） ----------
where node >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Node.js，请先安装：https://nodejs.org
  pause
  exit /b 1
)

echo [3/4] 检查 Node 依赖并构建前端（首次约需几分钟）...
if not exist "node_modules\@copilotkit\runtime" (
  call npm install --no-audit --no-fund
  if errorlevel 1 echo [警告] 根目录依赖安装失败，CopilotKit runtime 可能不可用
)
if not exist "web\node_modules" (
  pushd web
  call npm install --no-audit --no-fund
  popd
  if errorlevel 1 (
    echo [错误] 前端依赖安装失败，请手动执行：cd web ^&^& npm install
    pause
    exit /b 1
  )
)
if not exist "web\dist\index.html" (
  pushd web
  call npm run build
  popd
  if errorlevel 1 (
    echo [错误] 前端构建失败，请手动执行：cd web ^&^& npm run build
    pause
    exit /b 1
  )
)

rem ---------- 4. 启动两个服务 ----------
echo [4/4] 启动服务...
start "AgentScope 智能体服务" "%PY%" agent_server.py
timeout /t 2 /nobreak >nul
start "工作智能体集合 页面服务" node server.js
timeout /t 2 /nobreak >nul
start "" http://localhost:8787

echo.
echo   已启动两个窗口：
echo     - AgentScope 智能体服务（策划官 / 面试官 / 评分官）
echo     - 页面服务 http://localhost:8787 （React 前端 + CopilotKit runtime）
echo.
echo   开发模式（热更新）：cd web ^&^& npm run dev，然后打开 http://localhost:5173
echo   关闭任意一个窗口即停止对应服务；全部关闭请直接关掉这两个黑窗口。
echo   本窗口可以关闭。
pause
exit /b 0

:no_python
echo [错误] 未检测到 Python 3.11+（AgentScope 2.0 的最低要求），也没有可用的虚拟环境。
echo   请先安装 Python：https://www.python.org/downloads/
echo   安装时勾选 "Add Python to PATH"，然后重新双击本文件。
pause
exit /b 1
