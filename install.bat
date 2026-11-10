@echo off
setlocal EnableDelayedExpansion

title CDDM Live - Windows Installer
echo ==========================================================
echo            CDDM Live Windows Installation Gate            
echo ==========================================================

:: 1. Authorization check
set /p "USER_INPUT=Enter authorization key to install CDDM: "
if not "!USER_INPUT!"=="123456" (
    echo.
    echo [ERROR] Unauthorized: Invalid key. Installation aborted.
    pause
    exit /b 1
)

echo.
echo [OK] Authorized. Proceeding with installation...
echo ==========================================================

:: 2. Check for Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.10+ from python.org and check "Add python.exe to PATH".
    pause
    exit /b 1
)

:: 3. Setup Virtual Environment
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
)

:: 4. Upgrade pip and install requirements
echo Installing Python dependencies (PySide6, requests, certifi)...
venv\Scripts\python.exe -m pip install --upgrade pip

if exist "requirements.txt" (
    venv\Scripts\pip.exe install -r requirements.txt
) else (
    venv\Scripts\pip.exe install PySide6>=6.5.0 requests>=2.28.0 certifi>=2023.5.7
)

:: 5. Create Desktop Shortcut using PowerShell
echo Creating Windows Desktop Shortcut...
set "TARGET=%~dp0run_cddm.bat"
set "ICON=%~dp0program\cddis_icon.png"
set "SHORTCUT=%USERPROFILE%\Desktop\CDDM Live.lnk"

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%TARGET%'; $s.WorkingDirectory = '%~dp0'; $s.Save()"

echo.
echo ==========================================================
echo [SUCCESS] Installation complete!
echo - Run 'run_cddm.bat' or launch 'CDDM Live' from your Desktop.
echo ==========================================================
pause
