@echo off
REM One-time setup: creates a virtual environment and installs dependencies.
REM Run this once (or again after editing requirements.txt).

cd /d "%~dp0"

echo Creating virtual environment in .\venv ...
python -m venv venv
if errorlevel 1 (
    echo Failed to create the virtual environment. Is Python installed and on PATH?
    pause
    exit /b 1
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing dependencies from requirements.txt ...
pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed. See the error above.
    pause
    exit /b 1
)

echo.
echo Setup complete. Use run.bat to start the app.
pause
