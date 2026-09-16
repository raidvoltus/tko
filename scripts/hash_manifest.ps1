#Requires -Version 5.1
<#
.SYNOPSIS
  SHA256 manifest for dist artifacts.
#>
[CmdletBinding()]
param(
    [string] $DistDir = "",
    [string] $OutFile = ""
)

$ErrorActionPreference = "Stop"

if (-not $DistDir) {
    if ($env:GITHUB_WORKSPACE) { $DistDir = Join-Path $env:GITHUB_WORKSPACE "dist" }
    else { $DistDir = Join-Path (Get-Location).Path "dist" }
}
$DistDir = [System.IO.Path]::GetFullPath($DistDir)

if (-not $OutFile) {
    $root = if ($env:GITHUB_WORKSPACE) { $env:GITHUB_WORKSPACE } else { (Get-Location).Path }
    $OutFile = Join-Path $root "certification\out\checksums.json"
}
$OutFile = [System.IO.Path]::GetFullPath($OutFile)

Write-Host "[hash] DistDir=$DistDir"
Write-Host "[hash] OutFile=$OutFile"

if (-not (Test-Path $DistDir)) {
    Write-Error "DistDir not found: $DistDir"
    exit 1
}

$outDir = Split-Path $OutFile -Parent
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$files = @(Get-ChildItem -Path $DistDir -File -ErrorAction SilentlyContinue)
Write-Host "[hash] file count=$($files.Count)"
foreach ($f in $files) {
    Write-Host ("  {0} ({1} bytes)" -f $f.Name, $f.Length)
}

if ($files.Count -eq 0) {
    Write-Error "No files in DistDir"
    exit 1
}

$entries = @()
foreach ($f in $files) {
    $sha = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $entries += [pscustomobject]@{
        name     = $f.Name
        size     = $f.Length
        sha256   = $sha
        built_at = $f.LastWriteTimeUtc.ToString("o")
    }
}

$payload = [pscustomobject]@{
    schema = "tko.checksums/v1"
    commit = $env:GITHUB_SHA
    run_id = $env:GITHUB_RUN_ID
    files  = $entries
}

$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $OutFile -Encoding utf8
Write-Host "[hash] wrote $OutFile"
exit 0
