#Requires -Version 5.1
<#
.SYNOPSIS
  Actual EXE smoke: Core + GUI onedir, IPC bootstrap, PAPER-oriented (no LIVE orders).
#>
param(
    [string]$DistDir = "",
    [int]$CoreWaitSec = 10,
    [int]$GuiWaitSec = 6
)
$ErrorActionPreference = "Stop"
if (-not $DistDir) {
    if ($env:GITHUB_WORKSPACE) { $DistDir = Join-Path $env:GITHUB_WORKSPACE "dist" }
    elseif ($PSScriptRoot) { $DistDir = Join-Path (Split-Path $PSScriptRoot -Parent) "dist" }
    else { $DistDir = Join-Path (Get-Location) "dist" }
}
$DistDir = [System.IO.Path]::GetFullPath($DistDir)
Write-Host "Smoke DistDir=$DistDir"

# Support onedir and legacy onefile layouts
$core = Join-Path $DistDir "TKO-Core\TKO-Core.exe"
$gui  = Join-Path $DistDir "TKO-GUI\TKO-GUI.exe"
if (-not (Test-Path $core)) { $core = Join-Path $DistDir "TKO-Core.exe" }
if (-not (Test-Path $gui))  { $gui  = Join-Path $DistDir "TKO-GUI.exe" }

if (-not (Test-Path $core)) { throw "Core EXE missing under $DistDir" }
if (-not (Test-Path $gui))  { throw "GUI EXE missing under $DistDir" }

$tokenPath = Join-Path $env:ProgramData "TKO\ipc.token"
$results = @()
function Add-Result($id, $status, $evidence) {
    $script:results += [pscustomobject]@{ id = $id; status = $status; evidence = $evidence }
    Write-Host "[$status] $id — $evidence"
}

if ($env:TKO_CERT_FRESH_TOKEN -eq "1") {
    if (Test-Path $tokenPath) { Remove-Item -Force $tokenPath -ErrorAction SilentlyContinue }
}

Add-Result "core_exists" "PASS" $core
Add-Result "gui_exists" "PASS" $gui

$coreProc = Start-Process -FilePath $core -WorkingDirectory (Split-Path $core -Parent) -PassThru -WindowStyle Hidden
Start-Sleep -Seconds $CoreWaitSec

if ($coreProc.HasExited) {
    Add-Result "core_stay_alive" "FAIL" "exited code=$($coreProc.ExitCode)"
} else {
    Add-Result "core_stay_alive" "PASS" "pid=$($coreProc.Id)"
}

if (Test-Path $tokenPath) {
    $len = (Get-Item $tokenPath).Length
    if ($len -ge 32) { Add-Result "ipc_bootstrap" "PASS" "token length=$len (value not logged)" }
    else { Add-Result "ipc_bootstrap" "FAIL" "token too short" }
} else {
    Add-Result "ipc_bootstrap" "FAIL" "missing $tokenPath"
}

$guiProc = $null
try {
    $guiProc = Start-Process -FilePath $gui -WorkingDirectory (Split-Path $gui -Parent) -PassThru
    Start-Sleep -Seconds $GuiWaitSec
    if ($guiProc.HasExited -and $guiProc.ExitCode -ne 0) {
        Add-Result "gui_start" "FAIL" "exit=$($guiProc.ExitCode)"
    } else {
        Add-Result "gui_start" "PASS" "pid=$($guiProc.Id)"
    }
} catch {
    Add-Result "gui_start" "FAIL" $_.Exception.Message
}

# Restart Core briefly
if ($coreProc -and -not $coreProc.HasExited) { Stop-Process -Id $coreProc.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
$core2 = Start-Process -FilePath $core -WorkingDirectory (Split-Path $core -Parent) -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 5
if ($core2.HasExited) { Add-Result "core_restart" "FAIL" "exited" }
else {
    Add-Result "core_restart" "PASS" "pid=$($core2.Id)"
    # token should persist (not regenerate)
    if (Test-Path $tokenPath) { Add-Result "token_persist" "PASS" "token still present after restart" }
    else { Add-Result "token_persist" "FAIL" "token missing after restart" }
}

if ($guiProc -and -not $guiProc.HasExited) { Stop-Process -Id $guiProc.Id -Force -ErrorAction SilentlyContinue }
if ($core2 -and -not $core2.HasExited) { Stop-Process -Id $core2.Id -Force -ErrorAction SilentlyContinue }
Add-Result "shutdown" "PASS" "stopped"

$fail = @($results | Where-Object { $_.status -eq "FAIL" })
$report = Join-Path $DistDir "smoke-results.json"
$results | ConvertTo-Json -Depth 4 | Set-Content $report -Encoding utf8
Write-Host "Smoke report: $report"
if ($fail.Count -gt 0) { exit 1 }
exit 0
