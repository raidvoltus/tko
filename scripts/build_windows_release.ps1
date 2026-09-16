#Requires -Version 5.1
<#
.SYNOPSIS
  Clean build TKO-Core.exe + TKO-GUI.exe, hashes, release-manifest.json
#>
param(
    [switch]$Clean,
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DistDir = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
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
Write-Host "PYTHONPATH: $env:PYTHONPATH"
Write-Host "Commit: $(Get-GitCommit)"
& $Python --version

if ($Clean) {
    foreach ($d in @("build", "dist")) {
        $p = Join-Path $SourceRoot $d
        if (Test-Path $p) {
            Write-Host "Cleaning $p"
            Remove-Item -Recurse -Force $p
        }
    }
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $SourceRoot "build") | Out-Null

Write-Host "Installing build deps..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $SourceRoot "requirements.txt")
& $Python -m pip install "pyinstaller>=6.3,<7"

# Preflight imports
$env:PYTHONPATH = $SourceRoot
Write-Host "Preflight import src.core.entry..."
& $Python -c "import src.core.entry; print('core entry OK')"
if ($LASTEXITCODE -ne 0) { throw "import src.core.entry failed" }
Write-Host "Preflight import src.gui.entry..."
& $Python -c "import src.gui.entry; print('gui entry OK')"
if ($LASTEXITCODE -ne 0) { throw "import src.gui.entry failed" }

Write-Host "Building TKO-Core.exe..."
& $Python -m PyInstaller `
    (Join-Path $SourceRoot "packaging\tko-core.spec") `
    --noconfirm --clean `
    --distpath $DistDir `
    --workpath (Join-Path $SourceRoot "build\core") `
   
if ($LASTEXITCODE -ne 0) { throw "PyInstaller Core failed exit=$LASTEXITCODE" }

Write-Host "Building TKO-GUI.exe..."
& $Python -m PyInstaller `
    (Join-Path $SourceRoot "packaging\tko-gui.spec") `
    --noconfirm --clean `
    --distpath $DistDir `
    --workpath (Join-Path $SourceRoot "build\gui") `
   
if ($LASTEXITCODE -ne 0) { throw "PyInstaller GUI failed exit=$LASTEXITCODE" }

$core = Join-Path $DistDir "TKO-Core.exe"
$gui = Join-Path $DistDir "TKO-GUI.exe"
if (-not (Test-Path $core)) { throw "Missing $core" }
if (-not (Test-Path $gui)) { throw "Missing $gui" }

$coreSha = Get-FileSha256 $core
$guiSha = Get-FileSha256 $gui
@"
$coreSha  TKO-Core.exe
$guiSha  TKO-GUI.exe
"@ | Set-Content -Path (Join-Path $DistDir "SHA256SUMS") -Encoding ascii

$commit = Get-GitCommit
$pyVer = & $Python --version 2>&1
$manifest = [ordered]@{
    product = "TKO"
    version = "0.1.0"
    commit = $commit
    build_timestamp = (Get-Date).ToUniversalTime().ToString("o")
    os = [System.Environment]::OSVersion.VersionString
    architecture = $env:PROCESSOR_ARCHITECTURE
    python_build = "$pyVer"
    artifacts = @(
        @{ filename = "TKO-Core.exe"; sha256 = $coreSha; size = (Get-Item $core).Length }
        @{ filename = "TKO-GUI.exe"; sha256 = $guiSha; size = (Get-Item $gui).Length }
    )
    signing = @{ status = "NOT_CONFIGURED" }
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $DistDir "release-manifest.json") -Encoding utf8

Write-Host "Build complete."
Write-Host "  $core ($coreSha)"
Write-Host "  $gui ($guiSha)"
Write-Host "Dist listing:"
Get-ChildItem $DistDir | ForEach-Object { Write-Host ("  {0} {1}" -f $_.Name, $_.Length) }
if (-not (Test-Path $core)) { Write-Error "core missing after build"; exit 1 }
if (-not (Test-Path $gui)) { Write-Error "gui missing after build"; exit 1 }
exit 0
