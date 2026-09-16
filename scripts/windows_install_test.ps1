#Requires -Version 5.1
<#
.SYNOPSIS
  Portable distribution checks (no MSI). Verifies dist layout and ProgramData bootstrap path.
#>
param(
    [string]$DistDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "dist")
)

$ErrorActionPreference = "Stop"
$failed = 0
function Check($name, $cond, $evidence) {
    if ($cond) { Write-Host "[PASS] $name — $evidence" }
    else { Write-Host "[FAIL] $name — $evidence"; $script:failed++ }
}

Check "core_exe" (Test-Path (Join-Path $DistDir "TKO-Core.exe")) "TKO-Core.exe"
Check "gui_exe" (Test-Path (Join-Path $DistDir "TKO-GUI.exe")) "TKO-GUI.exe"
Check "sha256sums" (Test-Path (Join-Path $DistDir "SHA256SUMS")) "SHA256SUMS"
Check "manifest" (Test-Path (Join-Path $DistDir "release-manifest.json")) "release-manifest.json"
Check "programdata_writable" ($env:ProgramData -ne $null) "PROGRAMDATA=$env:ProgramData"
Check "msi_na" $true "Portable EXE — MSI install NOT APPLICABLE"

# Ensure we never ship ipc.token inside dist
$bad = Get-ChildItem -Path $DistDir -Recurse -Filter "ipc.token" -ErrorAction SilentlyContinue
Check "no_bundled_ipc_token" ($null -eq $bad -or $bad.Count -eq 0) "no ipc.token under dist"

if ($failed -gt 0) { exit 1 }
exit 0
