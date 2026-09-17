#Requires -Version 5.1
<#
.SYNOPSIS
  Scan dist/release text artifacts for secret-like patterns. Does not print secret values.
#>
param([string]$Root = "")
$ErrorActionPreference = "Stop"
if (-not $Root) {
    if ($env:GITHUB_WORKSPACE) { $Root = $env:GITHUB_WORKSPACE }
    else { $Root = (Get-Location).Path }
}
$patterns = @(
    "ghp_[A-Za-z0-9]{20,}",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "AKIA[0-9A-Z]{16}",
    "api_secret\s*=\s*['\"][^'\"]{8,}",
    "TELEGRAM_BOT_TOKEN\s*="
)
$scanDirs = @("dist", "release") | ForEach-Object { Join-Path $Root $_ }
$hits = 0
foreach ($dir in $scanDirs) {
    if (-not (Test-Path $dir)) { continue }
    Get-ChildItem $dir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Extension -in @(".json", ".txt", ".md", ".yaml", ".yml", ".env", ".sum", ".manifest") -or $_.Name -eq "SHA256SUMS"
    } | ForEach-Object {
        $text = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
        if (-not $text) { return }
        foreach ($pat in $patterns) {
            if ($text -match $pat) {
                Write-Host "[FAIL] pattern matched in $($_.FullName.Replace($Root, '.'))"
                $hits++
            }
        }
    }
}
# ensure no ipc.token bundled
$tok = @(Get-ChildItem (Join-Path $Root "dist") -Recurse -Filter "ipc.token" -ErrorAction SilentlyContinue)
if ($tok.Count -gt 0) {
    Write-Host "[FAIL] ipc.token bundled in dist"
    $hits++
}
if ($hits -gt 0) { Write-Host "Secret scan FAILED ($hits)"; exit 1 }
Write-Host "[PASS] secret scan — no credential patterns in text artifacts"
exit 0
