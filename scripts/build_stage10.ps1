#Requires -Version 5.1
<#
  Build TKO-Core.exe + TKO-GUI.exe from the CURRENT git commit only.
  Run on native Windows with Python + PyInstaller installed.
  Does NOT embed credentials, state, or KILL files.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Refuse uncommitted source — release must match remote HEAD
$status = (git status --porcelain)
if ($status) {
    Write-Error "Working tree is dirty. Commit or stash before release build:`n$status"
    exit 1
}
$commit = (git rev-parse HEAD).Trim()
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$pyver  = (python --version 2>&1)
try { $pyi = (pyinstaller --version 2>&1) } catch { throw "PyInstaller not found on PATH" }

Write-Host "Building Stage 10 dual EXE"
Write-Host "  commit=$commit branch=$branch"
Write-Host "  $pyver  PyInstaller=$pyi"

Remove-Item -Recurse -Force dist\TKO-Core, dist\TKO-GUI, build -ErrorAction SilentlyContinue
pyinstaller --clean --noconfirm --distpath dist packaging\TKO-Core.spec
if ($LASTEXITCODE -ne 0) { throw "TKO-Core.spec build failed" }
pyinstaller --clean --noconfirm --distpath dist packaging\TKO-GUI.spec
if ($LASTEXITCODE -ne 0) { throw "TKO-GUI.spec build failed" }

$core = Get-ChildItem -Recurse dist -Filter TKO-Core.exe | Select-Object -First 1
$gui  = Get-ChildItem -Recurse dist -Filter TKO-GUI.exe  | Select-Object -First 1
if (-not $core -or -not $gui) { throw "Build missing Core or GUI exe" }

$coreHash = (Get-FileHash $core.FullName -Algorithm SHA256).Hash
$guiHash  = (Get-FileHash $gui.FullName  -Algorithm SHA256).Hash
$ts = Get-Date -Format o

@"
TKO Stage 10 Windows Release
============================
BUILD COMMIT: $commit
BRANCH:       $branch
PYTHON:       $pyver
PYINSTALLER:  $pyi
OS:           $([Environment]::OSVersion.VersionString)
ARCH:         $env:PROCESSOR_ARCHITECTURE
CORE PATH:    $($core.FullName)
GUI PATH:     $($gui.FullName)
CORE SHA256:  $coreHash
GUI  SHA256:  $guiHash
BUILT AT:     $ts

NOTES
- Credentials are NOT bundled. Provision via: python -m tko setup  (or keyring)
- Runtime state, KILL, journals, backups are external (next to EXE / user profile)
- Production Windows IPC: Named Pipe (do NOT set TKO_IPC_TCP=1)
- Closing GUI does not stop Core
- Source/Core certification baseline: Stage 4-10 invariants on this commit

SMOKE (manual, no live orders):
1. Start TKO-Core.exe (console/service) alone — confirm process stays up
2. Start TKO-GUI.exe — status should show Core lifecycle (not fake READY)
3. Close GUI — Core must remain running
4. Restart GUI — reconnect via IPC
5. KILL / STOP only via existing lifecycle commands
6. Do not place live orders solely to prove GUI
"@ | Out-File dist\RELEASE.txt -Encoding utf8

Write-Host ""
Write-Host "OK Core=$($core.FullName)"
Write-Host "   SHA256=$coreHash"
Write-Host "OK GUI =$($gui.FullName)"
Write-Host "   SHA256=$guiHash"
Write-Host "Wrote dist\RELEASE.txt"
Get-ChildItem -Recurse dist | Select-Object FullName, Length
