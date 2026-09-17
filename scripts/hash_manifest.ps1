#Requires -Version 5.1
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
if (-not (Test-Path $DistDir)) { Write-Error "DistDir missing"; exit 1 }
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile -Parent) | Out-Null

# Prefer EXE paths + top-level text; also hash all files under onedir roots (bounded)
$targets = @()
foreach ($rel in @("TKO-Core\TKO-Core.exe", "TKO-GUI\TKO-GUI.exe", "SHA256SUMS", "release-manifest.json")) {
    $p = Join-Path $DistDir $rel
    if (Test-Path $p) { $targets += Get-Item $p }
}
if ($targets.Count -eq 0) {
    $targets = @(Get-ChildItem $DistDir -File)
}
$entries = @()
foreach ($f in $targets) {
    $sha = (Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $rel = $f.FullName.Substring($DistDir.Length).TrimStart('\','/')
    $entries += [pscustomobject]@{ name = $rel; size = $f.Length; sha256 = $sha }
    Write-Host ("  {0} {1}" -f $rel, $sha)
}
[pscustomobject]@{
    schema = "tko.checksums/v1"
    commit = $env:GITHUB_SHA
    run_id = $env:GITHUB_RUN_ID
    files = $entries
} | ConvertTo-Json -Depth 6 | Set-Content $OutFile -Encoding utf8
Write-Host "[hash] wrote $OutFile"
exit 0
