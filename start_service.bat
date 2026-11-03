@echo off
chcp 65001 >nul
title AgentScope Agent Service · 容器沙箱
cd /d "%~dp0"

rem ============================================================
rem  AgentScope 2.0 官方 Agent Service（默认容器沙箱）
rem    端口 8799 · 接口文档 /docs
rem    和 start.bat 是两套独立服务，可以只开其中一个
rem    默认 SANDBOX=docker：工具/代码在 Docker 容器里执行
rem    想放弃隔离直接用宿主机：set SANDBOX=local 后再运行本文件
rem ============================================================

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\WorkBuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%LOCALAPPDATA%\WorkBuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY if exist "C:\Users\m1821\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=C:\Users\m1821\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not defined PY (
  echo [错误] 没找到 Python 虚拟环境，请先双击 start.bat 完成初始化。
  pause
  exit /b 1
)

echo [1/2] 检查 Agent Service 依赖（service / storage-sql / workspace-docker）...
"%PY%" -c "import agentscope, sqlalchemy, aiosqlite, aiodocker" >nul 2>nul
if errorlevel 1 (
  "%PY%" -m pip install -U -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请手动执行：
    echo   "%PY%" -m pip install -U -r requirements.txt
    pause
    exit /b 1
  )
)

echo [2/2] 启动 Agent Service（容器沙箱，端口 8799）...
echo.
"%PY%" agent_service.py
pause
