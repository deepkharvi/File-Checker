@echo off
REM Runs the app using the virtual environment created by setup.bat.

cd /d "%~dp0"

if not exist venv\Scripts\activate.bat (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python main.py

if errorlevel 1 (
    echo.
    echo The app exited with an error. See the message above.
    pause
)
