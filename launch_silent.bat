@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
start "" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" -B main.py
