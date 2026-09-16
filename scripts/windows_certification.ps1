#Requires -Version 5.1
<#
.SYNOPSIS
  Orchestrate TKO Windows release certification gates.
#>
param(
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DistDir = "",
    [string]$ExpectedCommit = "",
    [string]$ExpectedVersion = "0.1.0",
    [string]$Python = "python",
    [switch]$RunWack,
    [switch]$RunDefender,
    [switch]$SmokeTest,
    [switch]$SkipBuild,
    [switch]$Strict
)

$ErrorActionPreference = "Continue"
if (-not $DistDir) { $DistDir = Join-Path $SourceRoot "dist" }
Set-Location $SourceRoot
$outDir = Join-Path $SourceRoot "certification\out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$tests = New-Object System.Collections.Generic.List[object]
$criticalFail = $false

function Record($id, $name, $category, $status, $command, $evidence) {
    $tests.Add([ordered]@{
        id = $id; name = $name; category = $category
        status = $status; command = $command; evidence = $evidence
    }) | Out-Null
    Write-Host ("[{0}] {1}: {2}" -f $status, $id, $evidence)
    if ($status -in @("FAIL") -and $category -eq "critical") { $script:criticalFail = $true }
    if ($Strict -and $status -in @("FAIL", "BLOCKED") -and $category -eq "critical") { $script:criticalFail = $true }
}

# Source integrity
$commit = (git rev-parse HEAD 2>$null)
$porcelain = (git status --porcelain 2>$null)
if ($ExpectedCommit -and $commit -ne $ExpectedCommit) {
    Record "source_commit" "Expected commit" "critical" "FAIL" "git rev-parse HEAD" "got $commit expected $ExpectedCommit"
} else {
    Record "source_commit" "Git commit" "critical" "PASS" "git rev-parse HEAD" "$commit"
}
if ($porcelain) {
    Record "source_clean" "Clean tree" "critical" "FAIL" "git status --porcelain" "dirty"
} else {
    Record "source_clean" "Clean tree" "critical" "PASS" "git status --porcelain" "clean"
}

# Level A
Write-Host "=== pytest ==="
& $Python -m pytest -q --tb=line --ignore=tests/test_ml.py 2>&1 | Tee-Object (Join-Path $outDir "pytest.log")
if ($LASTEXITCODE -eq 0) {
    Record "pytest" "Unit tests" "critical" "PASS" "pytest" "exit 0"
} else {
    Record "pytest" "Unit tests" "critical" "FAIL" "pytest" "exit $LASTEXITCODE"
}

Write-Host "=== ruff ==="
& $Python -m ruff check . 2>&1 | Tee-Object (Join-Path $outDir "ruff.log")
if ($LASTEXITCODE -eq 0) {
    Record "ruff" "Ruff" "high" "PASS" "ruff check ." "exit 0"
} elseif ($LASTEXITCODE -eq 9009 -or $LASTEXITCODE -ne 0) {
    # try install
    & $Python -m pip install ruff -q
    & $Python -m ruff check . 2>&1 | Tee-Object (Join-Path $outDir "ruff.log")
    if ($LASTEXITCODE -eq 0) {
        Record "ruff" "Ruff" "high" "PASS" "ruff check ." "exit 0 after install"
    } else {
        Record "ruff" "Ruff" "high" "FAIL" "ruff check ." "exit $LASTEXITCODE"
    }
}

# Build
if (-not $SkipBuild) {
    Write-Host "=== build ==="
    powershell -ExecutionPolicy Bypass -File (Join-Path $SourceRoot "scripts\build_windows_release.ps1") -Clean -SourceRoot $SourceRoot -DistDir $DistDir -Python $Python
    if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $DistDir "TKO-Core.exe"))) {
        Record "pyinstaller" "PyInstaller build" "critical" "PASS" "build_windows_release.ps1" "EXEs present"
    } else {
        Record "pyinstaller" "PyInstaller build" "critical" "FAIL" "build_windows_release.ps1" "missing EXE or build failed"
    }
} else {
    Record "pyinstaller" "PyInstaller build" "critical" "NOT RUN" "SkipBuild" "skipped"
}

# Install layout
powershell -ExecutionPolicy Bypass -File (Join-Path $SourceRoot "scripts\windows_install_test.ps1") -DistDir $DistDir
if ($LASTEXITCODE -eq 0) {
    Record "dist_layout" "Dist layout" "critical" "PASS" "windows_install_test.ps1" "ok"
} else {
    Record "dist_layout" "Dist layout" "critical" "FAIL" "windows_install_test.ps1" "failed"
}

# Security
powershell -ExecutionPolicy Bypass -File (Join-Path $SourceRoot "scripts\windows_security_test.ps1") -SourceRoot $SourceRoot -DistDir $DistDir -Python $Python
if ($LASTEXITCODE -eq 0) {
    Record "security" "Security tests" "critical" "PASS" "windows_security_test.ps1" "ok"
} else {
    Record "security" "Security tests" "critical" "FAIL" "windows_security_test.ps1" "failed"
}

# Smoke
if ($SmokeTest) {
    powershell -ExecutionPolicy Bypass -File (Join-Path $SourceRoot "scripts\windows_smoke_test.ps1") -DistDir $DistDir
    if ($LASTEXITCODE -eq 0) {
        Record "smoke" "EXE smoke" "critical" "PASS" "windows_smoke_test.ps1" "ok"
    } else {
        Record "smoke" "EXE smoke" "critical" "FAIL" "windows_smoke_test.ps1" "failed"
    }
} else {
    Record "smoke" "EXE smoke" "critical" "NOT RUN" "SmokeTest switch off" "enable -SmokeTest on Windows"
}

# WACK
if ($RunWack) {
    powershell -ExecutionPolicy Bypass -File (Join-Path $SourceRoot "scripts\windows_wack.ps1") -DistDir $DistDir
    Record "wack" "WACK" "conditional" "NOT APPLICABLE" "windows_wack.ps1" "unpackaged EXE"
} else {
    Record "wack" "WACK" "conditional" "NOT APPLICABLE" "skipped" "unpackaged EXE"
}

# Defender optional
if ($RunDefender) {
    $mp = Get-Command Start-MpScan -ErrorAction SilentlyContinue
    if ($mp) {
        try {
            Start-MpScan -ScanPath $DistDir -ScanType CustomScan -ErrorAction Stop
            Record "defender" "Defender scan" "high" "PASS" "Start-MpScan" "completed"
        } catch {
            Record "defender" "Defender scan" "high" "BLOCKED" "Start-MpScan" $_.Exception.Message
        }
    } else {
        Record "defender" "Defender scan" "high" "BLOCKED" "Start-MpScan" "cmdlet unavailable"
    }
} else {
    Record "defender" "Defender scan" "high" "NOT RUN" "" "pass -RunDefender"
}

# Signing
Record "signing" "Code signing" "critical" "BLOCKED" "signtool" "PRODUCTION CODE SIGNING NOT CONFIGURED"

# Final gate
$statuses = $tests | ForEach-Object { $_.status }
$final = "RELEASE BLOCKED"
$crit = $tests | Where-Object { $_.category -eq "critical" }
$critBad = @($crit | Where-Object { $_.status -in @("FAIL", "BLOCKED", "NOT RUN") })
if ($critBad.Count -eq 0) {
    $final = "RELEASE READY"
} else {
    $final = "NOT RELEASE READY"
}

$report = [ordered]@{
    product = "TKO"
    version = $ExpectedVersion
    commit = "$commit"
    platform = "windows"
    architecture = $env:PROCESSOR_ARCHITECTURE
    tests = @($tests)
    artifacts = @()
    signing = @{ status = "NOT_CONFIGURED" }
    wack = @{ status = "NOT_APPLICABLE" }
    security = @{}
    ipc = @{}
    runtime = @{}
    final_gate = $final
    microsoft_store_certified = $false
    disclaimer = "Internal TKO certification only — not Microsoft Store certification"
}
if (Test-Path (Join-Path $DistDir "release-manifest.json")) {
    $report.artifacts = Get-Content (Join-Path $DistDir "release-manifest.json") -Raw | ConvertFrom-Json
}

$reportPath = Join-Path $outDir "certification-report.json"
$report | ConvertTo-Json -Depth 8 | Set-Content $reportPath -Encoding utf8
$md = Join-Path $outDir "certification-report.md"
@"
# TKO Certification Report
Commit: $commit
Final gate: $final
Microsoft Store certified: false
"@ | Set-Content $md -Encoding utf8

Write-Host "=== FINAL GATE: $final ==="
Write-Host "Report: $reportPath"

if ($Strict -and $final -ne "RELEASE READY") { exit 1 }
if ($criticalFail) { exit 1 }
exit 0
