#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONUTF8 = "1"
Write-Host "== Ruff =="
python -m ruff check src tests main.py
if ($LASTEXITCODE -ne 0) { throw "ruff failed" }
Write-Host "== Pytest =="
python -m pytest
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
Write-Host "== Build (existing dual EXE) =="
powershell -ExecutionPolicy Bypass -File scripts/build_windows_release.ps1 -Clean
Write-Host "== ALL GREEN =="
