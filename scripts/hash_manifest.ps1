#Requires -Version 5.1
[CmdletBinding()]
param([string] $DistDir = "dist", [string] $OutFile = "certification/out/checksums.json")
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
$entries = @(Get-ChildItem $DistDir -File -ErrorAction Stop | ForEach-Object {
    [ordered]@{
        name = $_.Name
        size = $_.Length
        sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        built_at = $_.LastWriteTimeUtc.ToString("o")
    }
})
[ordered]@{
    schema = "tko.checksums/v1"
    commit = $env:GITHUB_SHA
    run_id = $env:GITHUB_RUN_ID
    files = $entries
} | ConvertTo-Json -Depth 6 | Set-Content $OutFile -Encoding utf8
Write-Host "[hash] $OutFile"
