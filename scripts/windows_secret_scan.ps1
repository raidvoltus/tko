#Requires -Version 5.1
param([string]$Root = "")
$ErrorActionPreference = "Continue"
if (-not $Root) {
    if ($env:GITHUB_WORKSPACE) { $Root = $env:GITHUB_WORKSPACE }
    else { $Root = (Get-Location).Path }
}
Write-Host "[secret-scan] root=$Root"
$patterns = @(
    "ghp_[A-Za-z0-9]{20,}",
    "github_pat_[A-Za-z0-9_]{20,}",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "AKIA[0-9A-Z]{16}"
)
$hits = 0
$dirs = @("dist", "release") | ForEach-Object { Join-Path $Root $_ }
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        Write-Host "[secret-scan] skip missing $dir"
        continue
    }
    Get-ChildItem $dir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Extension -match '\.(json|txt|md|ya?ml|env|sum)$' -or $_.Name -in @("SHA256SUMS", "checksums.txt")
    } | ForEach-Object {
        try {
            $text = Get-Content $_.FullName -Raw -ErrorAction Stop
        } catch { return }
        if (-not $text) { return }
        foreach ($pat in $patterns) {
            if ([regex]::IsMatch($text, $pat)) {
                $rel = $_.FullName.Replace($Root, ".")
                Write-Host "[FAIL] secret-like pattern in $rel"
                $hits++
            }
        }
    }
}
$tok = @()
if (Test-Path (Join-Path $Root "dist")) {
    $tok = @(Get-ChildItem (Join-Path $Root "dist") -Recurse -Filter "ipc.token" -ErrorAction SilentlyContinue)
}
if ($tok.Count -gt 0) {
    Write-Host "[FAIL] ipc.token found under dist"
    $hits++
}
if ($hits -gt 0) {
    Write-Host "[secret-scan] FAILED hits=$hits"
    exit 1
}
Write-Host "[PASS] secret scan clean"
exit 0
