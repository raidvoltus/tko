#Requires -Version 5.1
<#
.SYNOPSIS
  WACK gate for TKO. Unpackaged PyInstaller EXE is NOT a supported Store package format.
#>
param(
    [string]$DistDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "dist"),
    [switch]$ForceAttempt
)

Write-Host "TKO ships portable PyInstaller EXE (TKO-Core.exe / TKO-GUI.exe), not MSIX/APPX."
Write-Host "Windows App Certification Kit (WACK) is NOT APPLICABLE for this package format."
Write-Host "Status: NOT APPLICABLE"
Write-Host "Do not interpret this script as Microsoft Store certification."

if ($ForceAttempt) {
    Write-Warning "ForceAttempt set but WACK is still documented NOT APPLICABLE — no fake PASS."
}

# Write marker for certification report consumers
$outDir = Join-Path (Split-Path $DistDir -Parent) "certification\out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
@{
    status = "NOT_APPLICABLE"
    reason = "Unpackaged PyInstaller EXE; WACK targets Store package formats"
    microsoft_store_certified = $false
} | ConvertTo-Json | Set-Content (Join-Path $outDir "wack-summary.json") -Encoding utf8

exit 0
