@echo off
REM Build TKO-Core.exe and TKO-GUI.exe (Windows 10+)
python -m pip install pyinstaller
cd /d %~dp0\..
pyinstaller packaging\tko-core.spec --noconfirm
pyinstaller packaging\tko-gui.spec --noconfirm
echo.
echo Output: dist\TKO-Core.exe  dist\TKO-GUI.exe
echo First run Core then GUI. Token auto-created under %%ProgramData%%\TKO\ipc.token
pause
