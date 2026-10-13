@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo Python is not available. Install Python 3.12 or newer and enable Add Python to PATH.
    pause
    exit /b 1
)
start "Personal Job Agent" pythonw "%~dp0run.py" gui
