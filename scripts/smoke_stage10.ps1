#Requires -Version 5.1
<#
  Post-build smoke checks for Stage 10 dual EXE (no live orders).
  Run from repo root after scripts\build_stage10.ps1 succeeds.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$core = Get-ChildItem -Recurse dist -Filter TKO-Core.exe -ErrorAction SilentlyContinue | Select-Object -First 1
$gui  = Get-ChildItem -Recurse dist -Filter TKO-GUI.exe  -ErrorAction SilentlyContinue | Select-Object -First 1
$rel  = Join-Path $Root "dist\RELEASE.txt"

if (-not $core) { throw "TKO-Core.exe not found under dist\" }
if (-not $gui)  { throw "TKO-GUI.exe not found under dist\" }
if (-not (Test-Path $rel)) { throw "dist\RELEASE.txt missing — run build_stage10.ps1 first" }

Write-Host "Found Core: $($core.FullName) ($([math]::Round($core.Length/1MB,2)) MB)"
Write-Host "Found GUI:  $($gui.FullName) ($([math]::Round($gui.Length/1MB,2)) MB)"
Write-Host "--- RELEASE.txt ---"
Get-Content $rel
Write-Host "-------------------"

# Ensure no obvious secret files bundled next to release
$bad = Get-ChildItem -Recurse dist -File | Where-Object {
    $_.Name -match '\.env$|\.pem$|\.key$|credentials|api_secret|secrets'
}
if ($bad) {
    Write-Error "Possible secret artifacts in dist:`n$($bad.FullName -join "`n")"
    exit 1
}

Write-Host ""
Write-Host "STATIC SMOKE PASS (files present, RELEASE.txt exists, no obvious secret filenames)."
Write-Host "MANUAL RUNTIME still required:"
Write-Host "  1) Start-Process $($core.FullName)"
Write-Host "  2) Start-Process $($gui.FullName)"
Write-Host "  3) Close GUI; confirm Core process still running"
Write-Host "  4) Do NOT place live exchange orders for this smoke"
