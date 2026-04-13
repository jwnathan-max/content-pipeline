@echo off
title Content Pipeline

cd /d "%~dp0"

echo [1/3] Installing packages...
pip install -r requirements.txt -q --disable-pip-version-check
if %errorlevel% neq 0 (
    echo ERROR: Package installation failed.
    pause
    exit /b 1
)

echo [2/3] Checking .env file...
if not exist ".env" (
    echo ERROR: .env file not found. Copy .env.example and fill in your API keys.
    pause
    exit /b 1
)

echo [3/3] Starting app...
echo.
echo Browser will open at http://localhost:8501
echo Press Ctrl+C to stop.
echo.
start http://localhost:8501
streamlit run app.py

pause
