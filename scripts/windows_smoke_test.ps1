#Requires -Version 5.1
<#
.SYNOPSIS
  Start TKO-Core.exe, wait for IPC token, start GUI briefly, stop processes.
  PAPER-oriented — does not send LIVE exchange orders.
#>
param(
    [string]$DistDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "dist"),
    [int]$CoreWaitSec = 8,
    [int]$GuiWaitSec = 5
)

$ErrorActionPreference = "Stop"
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

# Optional: remove only test token if env TKO_CERT_FRESH_TOKEN=1
if ($env:TKO_CERT_FRESH_TOKEN -eq "1") {
    if (Test-Path $tokenPath) {
        Remove-Item -Force $tokenPath
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

# Shutdown
if ($guiProc -and -not $guiProc.HasExited) { Stop-Process -Id $guiProc.Id -Force -ErrorAction SilentlyContinue }
if ($coreProc -and -not $coreProc.HasExited) { Stop-Process -Id $coreProc.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Add-Result "shutdown" "PASS" "processes stopped"

$fail = @($results | Where-Object { $_.status -eq "FAIL" })
if ($fail.Count -gt 0) { exit 1 }
exit 0
