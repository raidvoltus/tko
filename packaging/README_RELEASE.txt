TKO — Production Windows Release (Core + GUI)

REQUIREMENTS
- Windows 10/11 x64
- No Python/pip required on target PC for running the EXE

LAYOUT
- TKO-Core/TKO-Core.exe   trading engine (authority path)
- TKO-GUI/TKO-GUI.exe     IPC dashboard only (no exchange)
- BUILD_MANIFEST.json
- SHA256SUMS.txt
- RELEASE.txt

FIRST RUN
1. Copy the dist folders to the target machine.
2. Optional portable mode: place an empty file named .portable next to the EXE
   → writable data under <exe_dir>/data/
   Default (no .portable): %LOCALAPPDATA%\TKO\{state,logs,config,models,backups}
3. Run: TKO-Core.exe setup
4. Run: TKO-Core.exe version
5. Run: TKO-Core.exe run

SAFETY
- Credentials stored in OS keyring only (never inside EXE)
- No credentials → fail-closed (no trading)
- Second instance → refused (single-instance lock)
- ML invalid/corrupt → BUY blocked; core authority remains
- GUI crash does not stop Core

DO NOT
- Place live orders solely to validate packaging
- Set TKO_IPC_TCP=1 on production Windows
- Bundle .env / secrets into the release folder
