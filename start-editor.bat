@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo    PDF Editor One-Click Launcher
echo ============================================
echo.

rem ---------- 定位后端 Python ----------
set "PYTHON=C:\Users\Lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

rem ---------- 检查后端依赖 ----------
"%PYTHON%" -c "import fastapi, fitz, pdf2docx" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Backend dependencies are missing. Please run:
    echo    "%PYTHON%" -m pip install -r backend\requirements.txt
    echo.
    pause
    exit /b 1
)

rem ---------- 检查前端依赖 ----------
if not exist "frontend\node_modules\" (
    echo [ERROR] Frontend dependencies are missing. Please run:
    echo    cd frontend
    echo    npm install
    echo.
    pause
    exit /b 1
)

rem ---------- 在独立窗口启动服务 ----------
set "BACKEND_DIR=%~dp0backend"
set "FRONTEND_DIR=%~dp0frontend"
set "UI_URL=http://127.0.0.1:5173"
set "HEALTH_URL=%UI_URL%/api/health"

echo [1/3] Starting backend on port 8000...
start "PDF Editor Backend 8000" cmd /k cd /d "%BACKEND_DIR%" ^&^& "%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo [2/3] Starting frontend on port 5173...
start "PDF Editor Frontend 5173" cmd /k cd /d "%FRONTEND_DIR%" ^&^& npm run dev -- --host 127.0.0.1

rem ---------- 等待服务就绪 ----------
echo [3/3] Waiting for services to become ready...
set /a _tries=0
:wait_loop
set /a _tries+=1
curl.exe --fail --silent --show-error --max-time 2 "%HEALTH_URL%" >nul 2>&1
if not errorlevel 1 goto ready
if %_tries% geq 30 goto slow
ping -n 3 127.0.0.1 >nul
goto wait_loop

:ready
echo.
echo ============================================
echo    Ready! Opening %UI_URL%
echo.
echo    To stop: close the two server windows
echo    (Backend 8000 / Frontend 5173).
echo ============================================
ping -n 3 127.0.0.1 >nul
start "" "%UI_URL%"
exit /b 0

:slow
echo.
echo [WARN] Wait timed out after 60s. Check the two
echo server windows for logs. Opening browser anyway.
start "" "%UI_URL%"
exit /b 0
