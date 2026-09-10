#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$commit = (git rev-parse HEAD)
$pyver  = (python --version 2>&1)
$pyi    = (pyinstaller --version)

Remove-Item -Recurse -Force dist\TKO-Core, dist\TKO-GUI, build -ErrorAction SilentlyContinue
pyinstaller --clean --distpath dist packaging\TKO-Core.spec
pyinstaller --clean --distpath dist packaging\TKO-GUI.spec

$core = Get-ChildItem -Recurse dist -Filter TKO-Core.exe | Select-Object -First 1
$gui  = Get-ChildItem -Recurse dist -Filter TKO-GUI.exe  | Select-Object -First 1
if (-not $core -or -not $gui) { throw "Build missing Core or GUI exe" }
$coreHash = (Get-FileHash $core.FullName -Algorithm SHA256).Hash
$guiHash  = (Get-FileHash $gui.FullName  -Algorithm SHA256).Hash
@"
BUILD COMMIT: $commit
PYTHON:       $pyver
PYINSTALLER:  $pyi
CORE PATH:    $($core.FullName)
GUI PATH:     $($gui.FullName)
CORE SHA256:  $coreHash
GUI  SHA256:  $guiHash
BUILT AT:     $(Get-Date -Format o)
NOTE: Credentials are NOT bundled. Core loads from OS keyring/DPAPI store.
"@ | Out-File dist\RELEASE.txt -Encoding utf8
Write-Host "Artifacts:"; Get-ChildItem -Recurse dist | Select-Object FullName
