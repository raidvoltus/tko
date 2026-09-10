# TKO Stage 10 — Windows Release (one-shot)

**Source/Core** is certified on `main` (Stage 4–10 invariants).  
**Windows RELEASE READY** requires this checklist on a **native Windows** machine.

## Prerequisites

- Windows 10/11 x64
- Git
- Python 3.11+ on PATH
- `pip install pyinstaller`
- Clean working tree (`git status` empty)
- Checkout the certified commit (see `git log` / certification report)

```powershell
git fetch origin
git checkout main
git pull
git rev-parse HEAD   # record this SHA in RELEASE.txt (build script does this)
```

## Build

```powershell
.\scripts\build_stage10.ps1
```

Produces:

- `dist\TKO-Core\TKO-Core.exe` (or nested path under `dist\`)
- `dist\TKO-GUI\TKO-GUI.exe`
- `dist\RELEASE.txt` (commit SHA, SHA256, Python, PyInstaller, timestamp)

## Static smoke

```powershell
.\scripts\smoke_stage10.ps1
```

## Runtime smoke (manual, **no live orders**)

1. Start **TKO-Core.exe** alone — process stays up; IPC server (Named Pipe on Windows).
2. Start **TKO-GUI.exe** — dashboard reflects Core status (not a fake READY).
3. Close GUI — **Core must keep running**.
4. Restart GUI — reconnects via IPC.
5. Confirm GUI has **no** direct exchange/order path.
6. KILL/STOP only through existing lifecycle (GUI buttons map to IPC commands).
7. Do **not** set `TKO_IPC_TCP=1` on production Windows.
8. Credentials: provision with existing setup/keyring — **never** embedded in EXE.

## Fail closed

If any step fails: **NOT RELEASE READY**. Do not ship. Do not open Stage 11 for packaging gaps.

## Out of scope here

- Live Tokocrypto orders solely to prove GUI
- Changing Stage 4–9 trading semantics
- Stage 11 features
