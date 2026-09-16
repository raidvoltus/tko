#Requires -Version 5.1
<#
.SYNOPSIS
  Security checks on Windows: token matrix (unit via python), dist secret scan, optional ACL.
#>
param(
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DistDir = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
if (-not $DistDir) { $DistDir = Join-Path $SourceRoot "dist" }
Set-Location $SourceRoot

Write-Host "=== IPC unit tests (Python) ==="
$env:TKO_IPC_DIR = Join-Path $env:TEMP ("tko_ipc_cert_" + [guid]::NewGuid().ToString("N"))
& $Python -m pytest tests/test_ipc_token.py tests/test_ipc_server.py -q --tb=line
if ($LASTEXITCODE -ne 0) { throw "IPC pytest failed" }

Write-Host "=== Dist secret scan (patterns only; no values printed) ==="
$patterns = @("ghp_", "api_secret", "BEGIN PRIVATE KEY", "TELEGRAM_BOT_TOKEN=")
$hits = 0
Get-ChildItem $DistDir -File -ErrorAction SilentlyContinue | ForEach-Object {
    # Only scan small text-like files; skip large EXEs binary full read
    if ($_.Extension -in @(".json", ".txt", ".md", ".sum", ".manifest")) {
        $c = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
        foreach ($p in $patterns) {
            if ($c -and $c.Contains($p)) {
                Write-Host "[FAIL] pattern $p in $($_.Name)"
                $hits++
            }
        }
    }
}
if ($hits -gt 0) { throw "Secret-like patterns in dist text artifacts" }
Write-Host "[PASS] no secret-like patterns in dist text artifacts"

$tokenPath = Join-Path $env:ProgramData "TKO\ipc.token"
if (Test-Path $tokenPath) {
    Write-Host "=== ACL sample (icacls) ==="
    icacls $tokenPath
    $acl = icacls $tokenPath 2>&1 | Out-String
    if ($acl -match "Everyone:\(F\)") {
        Write-Host "[FAIL] Everyone:(F) on ipc.token"
        exit 1
    }
    Write-Host "[PASS] no Everyone:(F) detected in icacls output"
} else {
    Write-Host "[NOT RUN] ACL — token not present yet (run smoke first)"
}

exit 0
