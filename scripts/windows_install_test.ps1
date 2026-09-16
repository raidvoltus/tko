#Requires -Version 5.1
<#
.SYNOPSIS
  Portable distribution checks (no MSI). Verifies dist layout.
#>
param(
    [string]$DistDir = ""
)

$ErrorActionPreference = "Continue"
$failed = 0

# Resolve dist directory robustly (GHA + local)
if (-not $DistDir -or $DistDir -eq "") {
    if ($env:GITHUB_WORKSPACE) {
        $DistDir = Join-Path $env:GITHUB_WORKSPACE "dist"
    } elseif ($PSScriptRoot) {
        $DistDir = Join-Path (Split-Path $PSScriptRoot -Parent) "dist"
    } else {
        $DistDir = Join-Path (Get-Location).Path "dist"
    }
}
$DistDir = [System.IO.Path]::GetFullPath($DistDir)
Write-Host "DistDir=$DistDir"
Write-Host "Exists=$(Test-Path $DistDir)"
if (Test-Path $DistDir) {
    Write-Host "Contents:"
    Get-ChildItem $DistDir -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $($_.Name) ($($_.Length) bytes)" }
} else {
    Write-Host "[FAIL] dist directory missing"
    exit 1
}

function Check([string]$name, [bool]$cond, [string]$evidence) {
    if ($cond) { Write-Host "[PASS] $name — $evidence" }
    else { Write-Host "[FAIL] $name — $evidence"; $script:failed++ }
}

$core = Join-Path $DistDir "TKO-Core.exe"
$gui = Join-Path $DistDir "TKO-GUI.exe"
$sums = Join-Path $DistDir "SHA256SUMS"
$manifest = Join-Path $DistDir "release-manifest.json"

Check "core_exe" (Test-Path $core) "TKO-Core.exe at $core"
Check "gui_exe" (Test-Path $gui) "TKO-GUI.exe at $gui"
Check "sha256sums" (Test-Path $sums) "SHA256SUMS"
Check "manifest" (Test-Path $manifest) "release-manifest.json"
Check "programdata_writable" (-not [string]::IsNullOrEmpty($env:ProgramData)) "PROGRAMDATA=$env:ProgramData"
Check "msi_na" $true "Portable EXE — MSI install NOT APPLICABLE"

$bad = @(Get-ChildItem -Path $DistDir -Recurse -Filter "ipc.token" -ErrorAction SilentlyContinue)
Check "no_bundled_ipc_token" ($bad.Count -eq 0) "ipc.token count=$($bad.Count)"

if ($failed -gt 0) {
    Write-Host "FAILED checks: $failed"
    exit 1
}
Write-Host "All dist layout checks passed."
exit 0
