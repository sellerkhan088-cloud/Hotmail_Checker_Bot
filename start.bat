@echo off
echo.
echo  ===================================
echo   ORBIT Hotmail Checker
echo  ===================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from python.org
    pause
    exit /b 1
)

:: Install deps if needed
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt -q

:: Check .env
if not exist ".env" (
    echo.
    echo WARNING: .env file not found!
    echo Copy .env.example to .env and add your BOT_TOKEN
    echo.
    copy .env.example .env
    echo Created .env from template. Edit it now then re-run this script.
    pause
    exit /b 1
)

echo.
echo Starting ORBIT...
echo.
python bot.py
pause
