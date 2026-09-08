# Build portable one-folder TKO.exe (run on Windows 10+)
# Usage: powershell -File scripts/build_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Installing packaging deps..."
pip install -e ".[packaging]"

Write-Host "Running PyInstaller..."
pyinstaller packaging/TKO.spec --noconfirm --clean

# Mark portable
$Dist = Join-Path $Root "dist\TKO"
if (Test-Path $Dist) {
    New-Item -ItemType File -Path (Join-Path $Dist ".portable") -Force | Out-Null
    Write-Host "Build OK: $Dist"
    Write-Host "Copy the whole TKO folder to the target PC."
} else {
    Write-Error "dist/TKO not found"
}
