#Requires -Version 5.1
param(
    [string]$DistDir = "",
    [int]$CoreWaitSec = 8,
    [int]$GuiWaitSec = 5
)
$ErrorActionPreference = "Stop"
if (-not $DistDir) {
    if ($env:GITHUB_WORKSPACE) { $DistDir = Join-Path $env:GITHUB_WORKSPACE "dist" }
    elseif ($PSScriptRoot) { $DistDir = Join-Path (Split-Path $PSScriptRoot -Parent) "dist" }
    else { $DistDir = Join-Path (Get-Location) "dist" }
}
$DistDir = [System.IO.Path]::GetFullPath($DistDir)
Write-Host "Smoke DistDir=$DistDir"
$core = Join-Path $DistDir "TKO-Core.exe"
$gui = Join-Path $DistDir "TKO-GUI.exe"
$tokenPath = Join-Path $env:ProgramData "TKO\ipc.token"

if (-not (Test-Path $core)) { throw "Core EXE missing: $core" }
if (-not (Test-Path $gui)) { throw "GUI EXE missing: $gui" }

$results = @()
function Add-Result($id, $status, $evidence) {
    $script:results += [pscustomobject]@{ id = $id; status = $status; evidence = $evidence }
    Write-Host "[$status] $id — $evidence"
}

if ($env:TKO_CERT_FRESH_TOKEN -eq "1") {
    if (Test-Path $tokenPath) {
        Remove-Item -Force $tokenPath -ErrorAction SilentlyContinue
        Write-Host "Removed existing test token for fresh bootstrap"
    }
}

$coreProc = Start-Process -FilePath $core -PassThru -WindowStyle Hidden
Start-Sleep -Seconds $CoreWaitSec

if ($coreProc.HasExited) {
    Add-Result "core_stay_alive" "FAIL" "Core exited early code=$($coreProc.ExitCode)"
} else {
    Add-Result "core_stay_alive" "PASS" "pid=$($coreProc.Id)"
}

if (Test-Path $tokenPath) {
    $len = (Get-Item $tokenPath).Length
    if ($len -ge 32) {
        Add-Result "ipc_bootstrap" "PASS" "token exists length=$len (value not logged)"
    } else {
        Add-Result "ipc_bootstrap" "FAIL" "token too short"
    }
} else {
    Add-Result "ipc_bootstrap" "FAIL" "token missing at $tokenPath"
}

$guiProc = $null
try {
    $guiProc = Start-Process -FilePath $gui -PassThru
    Start-Sleep -Seconds $GuiWaitSec
    if ($guiProc.HasExited -and $guiProc.ExitCode -ne 0) {
        Add-Result "gui_start" "FAIL" "exit=$($guiProc.ExitCode)"
    } else {
        Add-Result "gui_start" "PASS" "pid=$($guiProc.Id)"
    }
} catch {
    Add-Result "gui_start" "FAIL" $_.Exception.Message
}

if ($guiProc -and -not $guiProc.HasExited) { Stop-Process -Id $guiProc.Id -Force -ErrorAction SilentlyContinue }
if ($coreProc -and -not $coreProc.HasExited) { Stop-Process -Id $coreProc.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Add-Result "shutdown" "PASS" "processes stopped"

$fail = @($results | Where-Object { $_.status -eq "FAIL" })
if ($fail.Count -gt 0) { exit 1 }
exit 0
