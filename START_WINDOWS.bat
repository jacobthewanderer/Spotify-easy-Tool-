@echo off
setlocal
cd /d "%~dp0"

echo.
echo Spotify Library Manager
echo =======================
echo.
echo This will install Python packages from requirements.txt if needed.
echo Keep this terminal open while the app is running.
echo.

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=py -3
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        set PYTHON_CMD=python
    ) else (
        echo Python was not found.
        echo Install Python 3.10+ from https://www.python.org/downloads/
        echo Make sure to check "Add Python to PATH" during install.
        pause
        exit /b 1
    )
)

%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install -r requirements.txt
%PYTHON_CMD% app.py

pause
