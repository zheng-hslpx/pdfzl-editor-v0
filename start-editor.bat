@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo    PDF Editor One-Click Launcher
echo ============================================
echo.

rem ---------- locate backend python ----------
set "PYTHON=C:\Users\Lenovo\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

rem ---------- check backend dependencies ----------
"%PYTHON%" -c "import fastapi, fitz, pdf2docx" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Backend dependencies are missing. Please run:
    echo    "%PYTHON%" -m pip install -r backend\requirements.txt
    echo.
    pause
    exit /b 1
)

rem ---------- check frontend dependencies ----------
if not exist "frontend\node_modules\" (
    echo [ERROR] Frontend dependencies are missing. Please run:
    echo    cd frontend
    echo    npm install
    echo.
    pause
    exit /b 1
)

rem ---------- launch servers in separate windows ----------
set "BACKEND_DIR=%~dp0backend"
set "FRONTEND_DIR=%~dp0frontend"

echo [1/3] Starting backend on port 8000...
start "PDF Editor Backend 8000" cmd /k cd /d "%BACKEND_DIR%" ^&^& "%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo [2/3] Starting frontend on port 5173...
start "PDF Editor Frontend 5173" cmd /k cd /d "%FRONTEND_DIR%" ^&^& npm run dev -- --host 127.0.0.1

rem ---------- wait for readiness ----------
echo [3/3] Waiting for services to become ready...
set /a _tries=0
:wait_loop
set /a _tries+=1
curl -s --max-time 2 http://localhost:5173/api/health >nul 2>&1
if not errorlevel 1 goto ready
if %_tries% geq 30 goto slow
ping -n 3 127.0.0.1 >nul
goto wait_loop

:ready
echo.
echo ============================================
echo    Ready! Opening http://localhost:5173
echo.
echo    To stop: close the two server windows
echo    (Backend 8000 / Frontend 5173).
echo ============================================
ping -n 3 127.0.0.1 >nul
start http://localhost:5173
exit /b 0

:slow
echo.
echo [WARN] Wait timed out after 60s. Check the two
echo server windows for logs. Opening browser anyway.
start http://localhost:5173
exit /b 0
