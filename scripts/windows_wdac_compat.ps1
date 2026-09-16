#Requires -Version 5.1
<#
.SYNOPSIS
  WDAC compatibility placeholder. Full simulation requires WDACConfig module + policy.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $FilePath
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $FilePath)) { throw "File not found: $FilePath" }
New-Item -ItemType Directory -Force -Path "certification/out" | Out-Null
$signed = $false
try {
    $sig = Get-AuthenticodeSignature -FilePath $FilePath
    $signed = ($sig.Status -eq "Valid")
} catch {}
[ordered]@{
    file = (Resolve-Path $FilePath).Path
    signed = $signed
    status = $(if ($signed) { "PASS" } else { "NOT RUN" })
    note = "Full WDAC simulation requires enterprise policy + WDACConfig; unsigned builds remain NOT RUN for L4.wdac"
    simulated_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json -Depth 5 | Set-Content "certification/out/wdac-report.json" -Encoding utf8
Write-Host "[wdac] report written (signed=$signed)"
