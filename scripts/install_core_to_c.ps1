#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install TKO-Core (and optional GUI) to C:\Program Files\TKO\ and register auto-start on boot.

.DESCRIPTION
  - Copies onedir build into C:\Program Files\TKO\
  - Creates C:\ProgramData\TKO\ (IPC token lives HERE, not under Program Files)
  - Registers scheduled task TKO-Core: ONSTART / SYSTEM
  - Does NOT bundle credentials or ipc.token into Program Files
  - GUI is optional; Core is the trading authority

.PARAMETER Source
  Folder containing TKO-Core.exe (extracted zip or dist\TKO-Core).

.PARAMETER IncludeGui
  Also copy TKO-GUI.exe from the same source tree if present.

.PARAMETER StartNow
  Start Core once after install (non-blocking).

.EXAMPLE
  .\scripts\install_core_to_c.ps1 -Source "D:\Downloads\TKO-Core" -IncludeGui
#>
param(
  [string]$Source = "",
  [string]$InstallDir = "C:\Program Files\TKO",
  [switch]$IncludeGui,
  [switch]$StartNow,
  [switch]$SkipStartupTask
)

$ErrorActionPreference = "Stop"

function Find-SourceDir {
  param([string]$Hint)
  $candidates = @()
  if ($Hint) { $candidates += $Hint }
  $candidates += @(
    (Join-Path (Get-Location) "TKO-Core"),
    (Join-Path (Get-Location) "dist\TKO-Core"),
    (Join-Path $PSScriptRoot "..\dist\TKO-Core"),
    (Get-Location).Path
  )
  foreach ($c in $candidates) {
    if (-not $c) { continue }
    $exe = Join-Path $c "TKO-Core.exe"
    if (Test-Path -LiteralPath $exe) {
      return (Resolve-Path -LiteralPath $c).Path
    }
  }
  return $null
}

Write-Host "=== TKO Core install to C: ===" -ForegroundColor Cyan

$src = Find-SourceDir -Hint $Source
if (-not $src) {
  Write-Host "ERROR: TKO-Core.exe not found."
  Write-Host "Extract the Windows Release zip first, then run:"
  Write-Host "  .\scripts\install_core_to_c.ps1 -Source `"C:\path\to\extracted\TKO-Core`""
  exit 1
}

Write-Host "Source: $src"
Write-Host "Target: $InstallDir"

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Write-Host "Copying Core files..."
Copy-Item -Path (Join-Path $src "*") -Destination $InstallDir -Recurse -Force

$coreExe = Join-Path $InstallDir "TKO-Core.exe"
if (-not (Test-Path -LiteralPath $coreExe)) {
  Write-Host "ERROR: copy failed — $coreExe missing"
  exit 1
}
Write-Host "Installed: $coreExe"

if ($IncludeGui) {
  $guiCandidates = @(
    (Join-Path (Split-Path $src -Parent) "TKO-GUI"),
    (Join-Path (Get-Location) "TKO-GUI"),
    (Join-Path $src "TKO-GUI.exe")
  )
  foreach ($g in $guiCandidates) {
    if (Test-Path -LiteralPath (Join-Path $g "TKO-GUI.exe")) {
      Copy-Item -Path (Join-Path $g "*") -Destination $InstallDir -Recurse -Force
      Write-Host "Merged GUI from $g"
      break
    }
    if ((Split-Path $g -Leaf) -eq "TKO-GUI.exe" -and (Test-Path -LiteralPath $g)) {
      Copy-Item -LiteralPath $g -Destination (Join-Path $InstallDir "TKO-GUI.exe") -Force
      Write-Host "Copied TKO-GUI.exe"
      break
    }
  }
}

$progData = Join-Path $env:PROGRAMDATA "TKO"
New-Item -ItemType Directory -Path $progData -Force | Out-Null
Write-Host "Data dir: $progData  (ipc.token after first Core run)"

$local = Join-Path $env:LOCALAPPDATA "TKO"
New-Item -ItemType Directory -Path $local -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $local "logs") -Force | Out-Null

if (-not $SkipStartupTask) {
  $tr = "`"$coreExe`" run"
  $tn = "TKO-Core"
  schtasks /Create /TN $tn /TR $tr /SC ONSTART /RU SYSTEM /RL HIGHEST /F
  if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: schtasks failed ($LASTEXITCODE). Core installed but not on boot."
  } else {
    Write-Host "Scheduled task '$tn' registered (ONSTART / SYSTEM)."
  }
}

Write-Host ""
Write-Host "=== Next steps ===" -ForegroundColor Green
Write-Host "1. & `"$coreExe`" setup"
Write-Host "2. & `"$coreExe`" run"
Write-Host "3. Token: $progData\ipc.token"
Write-Host "4. GUI: & `"$(Join-Path $InstallDir 'TKO-GUI.exe')`""
Write-Host "Core auto-starts when Windows boots."

if ($StartNow) {
  Start-Process -FilePath $coreExe -ArgumentList "run" -WorkingDirectory $InstallDir
}

Write-Host "Done."
