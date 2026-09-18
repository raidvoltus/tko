#Requires -Version 5.1
<#
.SYNOPSIS
  Build onedir TKO-Core + TKO-GUI, SHA256, release-manifest.json
#>
param(
    [switch]$Clean,
    [string]$SourceRoot = "",
    [string]$DistDir = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if (-not $SourceRoot) {
    $SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if ($env:GITHUB_WORKSPACE) { $SourceRoot = $env:GITHUB_WORKSPACE }
Set-Location $SourceRoot
if (-not $DistDir) { $DistDir = Join-Path $SourceRoot "dist" }

function Get-GitCommit {
    try { return (git rev-parse HEAD).Trim() } catch { return "unknown" }
}
function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

Write-Host "=== TKO Windows Release Build (onedir) ==="
Write-Host "SourceRoot=$SourceRoot DistDir=$DistDir"
& $Python --version
$env:PYTHONPATH = $SourceRoot

if ($Clean) {
    foreach ($d in @("build", "dist", "release")) {
        $p = Join-Path $SourceRoot $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
}
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $SourceRoot "requirements.txt")
& $Python -m pip install "pyinstaller>=6.3,<7"

Write-Host "Preflight imports..."
& $Python -c "import src.core.entry; import src.gui.entry; print('import OK')"
if ($LASTEXITCODE -ne 0) { throw "preflight import failed" }

Write-Host "Preflight tkinter/_tkinter (required for TKO-GUI freeze)..."
& $Python -c "import tkinter; import _tkinter; r=tkinter.Tk(); print('tcl', r.tk.exprstring('$tcl_library')); print('tk', r.tk.exprstring('$tk_library')); r.destroy(); print('tkinter OK')"
if ($LASTEXITCODE -ne 0) {
    throw "tkinter/_tkinter not available in build Python. Install official Python with Tcl/Tk (not embeddable package without tk)."
}

Write-Host "Building TKO-Core (onedir)..."
& $Python -m PyInstaller (Join-Path $SourceRoot "packaging\tko-core.spec") --noconfirm --clean --distpath $DistDir --workpath (Join-Path $SourceRoot "build\core")
if ($LASTEXITCODE -ne 0) { throw "Core build failed" }

Write-Host "Building TKO-GUI (onedir)..."
& $Python -m PyInstaller (Join-Path $SourceRoot "packaging\tko-gui.spec") --noconfirm --clean --distpath $DistDir --workpath (Join-Path $SourceRoot "build\gui")
if ($LASTEXITCODE -ne 0) { throw "GUI build failed" }

$core = Join-Path $DistDir "TKO-Core\TKO-Core.exe"
$gui  = Join-Path $DistDir "TKO-GUI\TKO-GUI.exe"
if (-not (Test-Path $core)) { throw "Missing $core" }
if (-not (Test-Path $gui))  { throw "Missing $gui" }

$coreSha = Get-FileSha256 $core
$guiSha  = Get-FileSha256 $gui
$sums = Join-Path $DistDir "SHA256SUMS"
@"
$coreSha  TKO-Core/TKO-Core.exe
$guiSha  TKO-GUI/TKO-GUI.exe
"@ | Set-Content -Path $sums -Encoding ascii

$commit = Get-GitCommit
$ver = "0.1.0"
if (Test-Path (Join-Path $SourceRoot "VERSION")) {
    $ver = (Get-Content (Join-Path $SourceRoot "VERSION") -Raw).Trim()
}
$pyVer = & $Python --version 2>&1
$manifest = [ordered]@{
    product = "TKO"
    version = $ver
    commit = $commit
    build_timestamp = (Get-Date).ToUniversalTime().ToString("o")
    os = [System.Environment]::OSVersion.VersionString
    architecture = $env:PROCESSOR_ARCHITECTURE
    python_build = "$pyVer"
    packager = "pyinstaller-onedir"
    artifacts = @(
        @{ path = "TKO-Core/TKO-Core.exe"; sha256 = $coreSha; size = (Get-Item $core).Length }
        @{ path = "TKO-GUI/TKO-GUI.exe";  sha256 = $guiSha;  size = (Get-Item $gui).Length }
    )
    signing = @{ status = "NOT_CONFIGURED" }
    microsoft_store_certified = $false
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $DistDir "release-manifest.json") -Encoding utf8

# release/ bundle layout
$rel = Join-Path $SourceRoot "release\TKO-Windows"
New-Item -ItemType Directory -Force -Path $rel | Out-Null
Copy-Item -Recurse -Force (Join-Path $DistDir "TKO-Core") (Join-Path $rel "TKO-Core")
Copy-Item -Recurse -Force (Join-Path $DistDir "TKO-GUI") (Join-Path $rel "TKO-GUI")
Copy-Item -Force $sums (Join-Path $rel "checksums.txt")
Copy-Item -Force (Join-Path $DistDir "release-manifest.json") (Join-Path $rel "RELEASE_MANIFEST.json")

Write-Host "Build complete (onedir)."
Write-Host "  $core ($coreSha)"
Write-Host "  $gui ($guiSha)"
Get-ChildItem $DistDir -Force | ForEach-Object { Write-Host ("  dist/{0}" -f $_.Name) }
exit 0
