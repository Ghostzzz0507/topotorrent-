@echo off
echo ================================================
echo   TopoTorrent - Setup Script
echo ================================================

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt

echo.
echo ================================================
echo   Setup complete! Launching TopoTorrent...
echo ================================================
python main.py
pause
