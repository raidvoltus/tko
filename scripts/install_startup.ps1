#Requires -RunAsAdministrator
param(
  [string]$CoreExe = "C:\Program Files\TKO\TKO-Core.exe"
)
if (-not (Test-Path $CoreExe)) {
  Write-Host "Core exe not found: $CoreExe — install binaries first."
  exit 1
}
schtasks /Create /TN "TKO-Core" /TR "`"$CoreExe`" run" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
Write-Host "Registered TKO-Core ONSTART. GUI is optional and must not stop Core."
