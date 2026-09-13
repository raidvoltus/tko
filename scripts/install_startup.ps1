#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Register TKO-Core to auto-start when Windows boots (ONSTART / SYSTEM).

.DESCRIPTION
  - Creates %PROGRAMDATA%\TKO if missing
  - Does NOT embed or print IPC token; Core first-run bootstraps ipc.token
  - Task: TKO-Core, trigger ONSTART, run as SYSTEM, highest privileges
  - GUI is optional and must never stop Core

.PARAMETER CoreExe
  Path to TKO-Core.exe (default: search common install locations)
#>
param(
  [string]$CoreExe = ""
)

$ErrorActionPreference = "Stop"

function Find-CoreExe {
  param([string]$Hint)
  if ($Hint -and (Test-Path -LiteralPath $Hint)) { return (Resolve-Path -LiteralPath $Hint).Path }
  $candidates = @(
    "C:\Program Files\TKO\TKO-Core.exe",
    "C:\Program Files (x86)\TKO\TKO-Core.exe",
    (Join-Path $PSScriptRoot "..\dist\TKO-Core\TKO-Core.exe"),
    (Join-Path $PSScriptRoot "..\dist\TKO-Core.exe"),
    (Join-Path (Get-Location) "TKO-Core.exe"),
    (Join-Path (Get-Location) "TKO-Core\TKO-Core.exe")
  )
  foreach ($c in $candidates) {
    if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path }
  }
  return $null
}

$CoreExe = Find-CoreExe -Hint $CoreExe
if (-not $CoreExe) {
  Write-Host "ERROR: TKO-Core.exe not found. Install binaries first or pass -CoreExe <path>."
  exit 1
}
Write-Host "Using Core: $CoreExe"

# Ensure ProgramData\TKO exists (token created on first Core run, not here)
$progData = Join-Path $env:PROGRAMDATA "TKO"
if (-not (Test-Path -LiteralPath $progData)) {
  New-Item -ItemType Directory -Path $progData -Force | Out-Null
  Write-Host "Created $progData"
}

# Prefer running the EXE itself (not cmd wrapper) so freeze_support works
$tr = "`"$CoreExe`" run"
$tn = "TKO-Core"

schtasks /Create /TN $tn /TR $tr /SC ONSTART /RU SYSTEM /RL HIGHEST /F
if ($LASTEXITCODE -ne 0) {
  Write-Host "ERROR: schtasks failed with code $LASTEXITCODE (run as Administrator)."
  exit $LASTEXITCODE
}

Write-Host "Registered task '$tn' (ONSTART / SYSTEM)."
Write-Host "Core will auto-start when the PC boots."
Write-Host "IPC token is bootstrapped on first Core start under $progData\ipc.token (never bundled)."
Write-Host "GUI is optional and must not stop Core."
