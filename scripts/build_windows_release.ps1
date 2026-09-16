#Requires -Version 5.1
<#
.SYNOPSIS
  Clean build TKO-Core.exe + TKO-GUI.exe, hashes, release-manifest.json
.NOTES
  Run on Windows 10/11 with Python 3.11 recommended.
  Does not delete user ProgramData or trading state.
#>
param(
    [switch]$Clean,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DistDir = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location $SourceRoot
if (-not $DistDir) { $DistDir = Join-Path $SourceRoot "dist" }

function Get-GitCommit {
    try { return (git rev-parse HEAD).Trim() } catch { return "unknown" }
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

Write-Host "=== TKO Windows Release Build ==="
Write-Host "SourceRoot: $SourceRoot"
$commit = Get-GitCommit
Write-Host "Commit: $commit"

$status = git status --porcelain 2>$null
if ($status) {
    Write-Warning "Working tree is not clean — release builds should use clean checkout"
}

if ($Clean) {
    foreach ($d in @("build", "dist", Join-Path $SourceRoot "certification\out")) {
        if (Test-Path $d) {
            Write-Host "Cleaning $d"
            Remove-Item -Recurse -Force $d
        }
    }
}

Write-Host "Installing dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $SourceRoot "requirements.txt")
& $Python -m pip install pyinstaller

Write-Host "Building TKO-Core.exe..."
& $Python -m PyInstaller (Join-Path $SourceRoot "packaging\tko-core.spec") --noconfirm --distpath $DistDir --workpath (Join-Path $SourceRoot "build\core")

Write-Host "Building TKO-GUI.exe..."
& $Python -m PyInstaller (Join-Path $SourceRoot "packaging\tko-gui.spec") --noconfirm --distpath $DistDir --workpath (Join-Path $SourceRoot "build\gui")

$core = Join-Path $DistDir "TKO-Core.exe"
$gui = Join-Path $DistDir "TKO-GUI.exe"
if (-not (Test-Path $core)) { throw "Missing $core" }
if (-not (Test-Path $gui)) { throw "Missing $gui" }

$coreSha = Get-FileSha256 $core
$guiSha = Get-FileSha256 $gui
$coreSize = (Get-Item $core).Length
$guiSize = (Get-Item $gui).Length

$sums = Join-Path $DistDir "SHA256SUMS"
@"
$coreSha  TKO-Core.exe
$guiSha  TKO-GUI.exe
"@ | Set-Content -Path $sums -Encoding ascii

$pyVer = & $Python --version 2>&1
$manifest = [ordered]@{
    product           = "TKO"
    version           = "0.1.0"
    commit            = $commit
    build_timestamp   = (Get-Date).ToUniversalTime().ToString("o")
    os                = [System.Environment]::OSVersion.VersionString
    architecture      = $env:PROCESSOR_ARCHITECTURE
    python_build      = "$pyVer"
    artifacts         = @(
        @{ filename = "TKO-Core.exe"; sha256 = $coreSha; size = $coreSize }
        @{ filename = "TKO-GUI.exe";  sha256 = $guiSha;  size = $guiSize }
    )
    signing           = @{ status = "NOT_CONFIGURED" }
}
$manifestPath = Join-Path $DistDir "release-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding utf8

Write-Host "Build complete."
Write-Host "  $core ($coreSha)"
Write-Host "  $gui ($guiSha)"
Write-Host "  $manifestPath"
