@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Virtual environment missing. Running install.bat...
    call install.bat
)

start "" venv\Scripts\pythonw.exe program\cddis_monitor.py
exit
