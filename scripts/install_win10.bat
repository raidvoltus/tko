@echo off
REM Tokocrypto Bot - Windows 10+ install
REM Requires: Python 3.10 / 3.11 / 3.12 64-bit on PATH

echo ============================================
echo Tokocrypto Autopilot - Windows 10+ Install
echo ============================================

python --version
if errorlevel 1 (
  echo ERROR: Python not found. Install Python 3.11 64-bit from python.org
  pause
  exit /b 1
)

python -c "import sys; v=sys.version_info; assert v.major==3 and v.minor>=10, 'Need Python 3.10+'"
if errorlevel 1 (
  echo ERROR: Python 3.10 or newer required.
  pause
  exit /b 1
)

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo.
echo Running diagnostic...
python scripts\startup_diagnostic.py

echo.
echo Done. Run: python main.py
pause
