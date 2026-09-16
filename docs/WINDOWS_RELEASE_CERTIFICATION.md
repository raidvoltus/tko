# TKO — Windows Release Certification

**Product:** TKO (Tokocrypto Full Autopilot)  
**Distribution:** Unpackaged / portable `TKO-Core.exe` + `TKO-GUI.exe` (PyInstaller)  
**OS target:** Windows 10 / Windows 11 (64-bit)  
**Default trading mode:** PAPER  

---

## 1. Scope

This document defines **TKO Internal Windows Release Certification**: reproducible checks that must pass before a GitHub Release of Windows artifacts.

It covers:

- Source integrity and CI (Level A)
- Windows artifact build and inspection (Level B)
- Clean Windows runtime validation (Level C)

It does **not** cover Tokocrypto LIVE order placement in automated CI.

---

## 2. Certification terminology

| Term | Meaning |
|------|---------|
| **INTERNAL TKO RELEASE CERTIFICATION** | This project's evidence-based gate for shipping Windows EXEs |
| **Microsoft/WACK TECHNICAL CHECKS** | Optional technical scans via Windows App Certification Kit when package format is supported |
| **Microsoft Store CERTIFICATION** | Only Microsoft Partner Center / Store process — **not claimed by this repo** |

Running GitHub Actions, WACK, or this checklist **does not** mean “Microsoft Store Certified” or “Microsoft Certified Application.”

### Allowed test states

Every gate must use exactly one of:

`PASS` | `FAIL` | `BLOCKED` | `NOT RUN` | `NOT APPLICABLE`

**Never** convert `BLOCKED` or `NOT RUN` into `PASS`.

---

## 3. Supported Windows versions

| OS | Status |
|----|--------|
| Windows 10 64-bit | Supported |
| Windows 11 64-bit | Supported |
| Windows 7 | **Not supported** (legacy files only) |
| Linux / macOS | Dev/CI source tests only — **cannot** certify Windows EXE |

---

## 4. Certification levels

### LEVEL A — Source / CI (Linux or Windows runner)

- Ruff
- pytest
- secret / dependency scan
- source integrity (clean tree, commit SHA)
- static order-path / IPC authority audit

### LEVEL B — Windows artifact (`windows-2022` runner)

- PyInstaller `TKO-Core.exe` / `TKO-GUI.exe`
- PE presence, size, SHA256
- version metadata (when configured)
- optional Authenticode (if secrets configured)
- optional Defender scan
- optional WACK (**only if package format supported**)

### LEVEL C — Clean Windows runtime

- Fresh profile / no prior `%ProgramData%\TKO`
- IPC bootstrap → `%ProgramData%\TKO\ipc.token`
- Core + GUI process separation
- PAPER smoke (no real exchange orders)
- SHADOW path check
- LIVE gate static/runtime (no live orders in CI)
- restart / concurrent startup / kill switch
- path independence (run EXE outside repo)

**Only Level C with all Critical gates PASS may produce `RELEASE READY`.**

---

## 5–8. Source integrity, reproducible build, Python matrix

Release builds must:

```text
git clone https://github.com/raidvoltus/tko.git
git checkout <tag-or-commit>
git status --porcelain   # empty
git rev-parse HEAD       # recorded in manifest
```

| Python | Role |
|--------|------|
| 3.10 | Supported for source CI |
| **3.11** | **Recommended release build** |
| 3.12 | Supported for source CI |

Do not claim all versions passed if only one was tested.

Dependencies: `requirements.txt` (Win10+). Training deps are separate.

---

## 9–11. Packaging, manifest, SHA256

Build (Windows):

```powershell
.\scripts\build_windows_release.ps1 -Clean
# or: scripts\build_exe.bat
```

Artifacts:

- `dist/TKO-Core.exe`
- `dist/TKO-GUI.exe`
- `release-manifest.json`
- `SHA256SUMS`

`release-manifest.json` must include product, version, commit, timestamp, OS, arch, Python, filename, SHA256, size, signing status — **never** API keys, Telegram tokens, IPC tokens, or private keys.

---

## 12–13. Digital signing

If GitHub Secrets provide a code-signing certificate:

- sign with `signtool`
- verify with `Get-AuthenticodeSignature`
- use secrets only; never commit PFX/password

If not configured:

```text
BLOCKED — PRODUCTION CODE SIGNING NOT CONFIGURED
```

Unsigned artifacts may ship only under explicit project policy; they cannot claim full production signing PASS.

---

## 14–16. IPC, security tests, GUI/Core

Canonical path: `%ProgramData%\TKO\ipc.token`

| Property | Requirement |
|----------|-------------|
| Generation | CSPRNG (`secrets.token_hex`) |
| Persistence | No regenerate if valid |
| Atomicity | temp + replace |
| Concurrent | Single canonical token |
| ACL | Not Everyone:(F) |
| Auth | HMAC over IPC messages |
| Bind | localhost only |

GUI = IPC client only. Core = sole trading authority.  
GUI must not call `rest.new_order` / buy / sell directly.

---

## 17–22. Trading authority

Order path:

```text
Market → Decision → Risk → Approval → Intent → Execution → Exchange
  → Confirmation | UNKNOWN → Reconciliation
```

| Mode | Behavior |
|------|----------|
| PAPER (default) | Simulated fills; no real orders |
| SHADOW | Decision only; no real orders |
| LIVE | All safety gates; **not** exercised with real orders in CI |

UNKNOWN (5xx/timeout) → reconcile; **no blind POST retry**.  
Kill switch blocks new authorization.

---

## 23–25. Installer / offline / no-network

Current distribution is **portable EXE** (no MSI/installer in-repo).

| Gate | Status |
|------|--------|
| MSI / Start Menu / ARP | **NOT APPLICABLE** (portable) |
| Silent install | **NOT APPLICABLE** |
| Offline standalone install of binaries | N/A for MSI; EXEs must not need network to *launch* |
| No-network runtime | Core/GUI must not crash; no false LIVE auth |

If an installer is added later, Level C must test install / silent / uninstall / reinstall.

---

## 26–27. Defender and WACK

- Run Defender on artifacts when on Windows certification host.
- False positive → `BLOCKED — SECURITY REVIEW REQUIRED`.

**WACK:** TKO ships **unpackaged PyInstaller EXE**, not MSIX/APPX.  
WACK is typically aimed at Store package formats.

```text
WACK: NOT APPLICABLE — unpackaged EXE distribution
```

Do not fake WACK PASS. Optional exploratory WACK may be recorded as `NOT APPLICABLE` or `NOT RUN`.

---

## 28–32. Runtime, clean VM, standard user, registration, version

Critical Level C evidence (Windows only):

- Event log / no missing DLL on smoke
- Clean VM: Win10 or Win11, no prior TKO state
- Standard user launch + IPC under correct permission model
- Portable: Start Menu registration **NOT APPLICABLE**
- ProductName / FileVersion should match release tag when version resources are set

---

## 33–38. Scripts, Actions, reports

| Script | Purpose |
|--------|---------|
| `scripts/build_windows_release.ps1` | Clean build, hash, manifest |
| `scripts/windows_certification.ps1` | Orchestrate gates; `-Strict` → non-zero on critical fail |
| `scripts/windows_smoke_test.ps1` | Core/GUI start, IPC, PAPER-oriented smoke |
| `scripts/windows_install_test.ps1` | Portable layout / ProgramData bootstrap checks |
| `scripts/windows_security_test.ps1` | Token matrix, secret scan of dist |
| `scripts/windows_wack.ps1` | Explicitly NOT APPLICABLE or skip |

Workflows:

- `.github/workflows/windows-release-certification.yml` — Level A+B on `windows-2022`
- `.github/workflows/windows-release-gate.yml` — required check name for branch protection

Artifacts uploaded: EXEs, SHA256SUMS, manifest, certification-report.json/md, logs — **never** secrets or ipc.token.

---

## 39. Certification matrix

| Gate | Severity | Automation | Required for RELEASE READY |
|------|----------|------------|----------------------------|
| Clean source | Critical | Yes | Yes |
| Ruff | High | Yes | Yes |
| Unit tests | Critical | Yes | Yes |
| PyInstaller | Critical | Windows | Yes |
| SHA256 | Critical | Yes | Yes |
| Code signing | Critical | Conditional | Per release policy |
| IPC token unit | Critical | Yes | Yes |
| IPC ACL | Critical | Windows | Yes |
| GUI/Core smoke | Critical | Windows | Yes |
| PAPER smoke | Critical | Windows | Yes |
| UNKNOWN static | Critical | Automated | Yes |
| Kill switch | Critical | Windows | Yes |
| Restart / concurrent | Critical | Windows | Yes |
| Clean VM | Critical | Windows | Yes |
| Defender | High | Windows | Yes |
| WACK | Conditional | — | **NOT APPLICABLE** |
| MSI install/uninstall | High | — | **NOT APPLICABLE** (portable) |

---

## 40. Release decision

| Decision | Rule |
|----------|------|
| **RELEASE READY** | All **Critical** gates **PASS** (including Level C on real Windows) |
| **RELEASE BLOCKED** | Any Critical gate is FAIL / BLOCKED / NOT RUN |
| **PASS WITH LIMITATIONS** | Engineering milestone only — **not** production certification |

---

## 41. Microsoft disclaimer

This system is **TKO Internal Windows Release Certification**.  
It is **not** Microsoft Store certification. Only Microsoft Partner Center establishes Store certification.

---

## 42–44. GitHub release gate and security

- Required check name: `windows-release-certification`
- PR: Level A; tag/release: full Windows workflow on `windows-2022`
- `permissions: contents: read` by default; no secret printing; no uploading IPC tokens or private keys

---

## 45–50. Pinning, no bypass, artifact retest, checklist

- Prefer pinned Actions SHAs where practical
- No `--trusted-host`, no Everyone Full Control, no Defender disable for green CI
- Downloaded Release artifacts must be re-hashed and smoke-tested on clean Windows
- Human checklist: see end of this file

---

## PRE-RELEASE CHECKLIST

```
PRE-RELEASE
[ ] Version updated
[ ] Commit clean
[ ] pytest pass
[ ] Ruff pass
[ ] Security / secret scan
BUILD
[ ] Clean build
[ ] TKO-Core.exe
[ ] TKO-GUI.exe
[ ] SHA256SUMS + release-manifest.json
[ ] Signing (if policy requires)
WINDOWS (Level C — real machine/VM)
[ ] Windows 10 and/or 11
[ ] Fresh ProgramData (no prior token)
[ ] IPC bootstrap automatic
[ ] GUI/Core authenticate
[ ] PAPER smoke (no real orders)
[ ] SHADOW path
[ ] Kill switch
[ ] Restart / concurrent startup
[ ] Path independence
[ ] Defender scan
[ ] WACK N/A documented
CERTIFICATION
[ ] certification-report.json
[ ] Clean-clone build matches commit
[ ] Remote parity
RELEASE
[ ] Git tag
[ ] GitHub Release
[ ] Downloaded artifact retested
```

---

## Known limitations

1. Portable EXE — no MSI installer gates.
2. WACK not applicable to unpackaged PyInstaller output.
3. Linux CI cannot execute Level B/C Windows runtime; those require `windows-2022` or a physical/VM Windows host.
4. Code signing blocked until secrets are configured.
5. LIVE real orders are out of scope for automated certification.
