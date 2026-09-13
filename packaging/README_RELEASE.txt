TKO — Production Windows Release (Core + GUI)

REQUIREMENTS
- Windows 10/11 x64
- No Python required on target PC for the EXE
- Administrator once for install to C:\Program Files\TKO + auto-start

INSTALL TO C: (recommended)
1. Extract the Core zip (folder with TKO-Core.exe + _internal).
2. Admin PowerShell:
     .\scripts\install_core_to_c.ps1 -Source "C:\path\to\extracted\TKO-Core" -IncludeGui
3. Installs to:
     C:\Program Files\TKO\TKO-Core.exe
     C:\Program Files\TKO\TKO-GUI.exe  (if -IncludeGui)
4. Auto-start: task "TKO-Core" ONSTART / SYSTEM
5. Runtime data (NOT under Program Files):
     C:\ProgramData\TKO\ipc.token
     %LOCALAPPDATA%\TKO\logs\

FIRST RUN AFTER INSTALL
1. & "C:\Program Files\TKO\TKO-Core.exe" setup
2. & "C:\Program Files\TKO\TKO-Core.exe" run
3. & "C:\Program Files\TKO\TKO-GUI.exe"
4. Confirm C:\ProgramData\TKO\ipc.token exists

SAFETY
- Credentials in OS keyring only
- No credentials → fail-closed
- GUI crash does not stop Core
- ipc.token is NEVER under Program Files

DO NOT
- Create ipc.token manually under Program Files
- Set TKO_IPC_TCP=1 on production Windows
- Bundle secrets into the release folder
