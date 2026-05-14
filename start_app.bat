@echo off
title Zenvest Launcher
echo 🚀 Starting Zenvest Setup...
cd /d "%~dp0"

echo 📦 Installing dependencies...
pip install supabase python-dotenv flask flask-cors yfinance pandas numpy requests

echo.
echo ✅ Checking Supabase connection...
python test_supabase.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ ERROR: Connection failed! 
    echo Please make sure you have:
    echo 1. Run the SQL script in your Supabase SQL Editor.
    echo 2. Checked that your URL and Key in .env are correct.
    echo.
    pause
    exit /b
)

echo.
echo 🌐 Starting Web Server on http://localhost:5001...
python app.py
pause
