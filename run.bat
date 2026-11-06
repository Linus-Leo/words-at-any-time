@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
py -3.11 main.py
if errorlevel 1 (
    echo.
    echo FloatingWordBall failed to start. Install dependencies:
    echo py -3.11 -m pip install -r requirements.txt
    pause
)
