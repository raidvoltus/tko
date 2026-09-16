@echo off
REM Tokocrypto Bot - Windows 7 SP1 install helper
REM Requires: Python 3.8.10 64-bit already installed and on PATH

echo ============================================
echo Tokocrypto Bot - Win7 Install
echo ============================================

python --version
if errorlevel 1 (
  echo ERROR: Python not found. Install Python 3.8.10 64-bit first.
  pause
  exit /b 1
)

python -c "import sys; assert sys.version_info[:2]==(3,8), 'Need Python 3.8.x'"
if errorlevel 1 (
  echo ERROR: Python 3.8.x required for Windows 7 compatibility.
  pause
  exit /b 1
)

python -m pip install --upgrade "pip<22" "setuptools<60" wheel
python -m pip install -r requirements-win7.txt

echo.
echo Running diagnostic...
python scripts\startup_diagnostic.py

echo.
echo Done. Run: python main.py
pause

REM LEGACY — Windows 7 no longer supported. Use install_win10.bat
