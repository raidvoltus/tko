#Requires -Version 5.1
[CmdletBinding()]
param(
    [string] $OutFile = "certification/out/sbom.cdx.json"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
python -m pip install --quiet "cyclonedx-bom==4.5.0"
python -m cyclonedx_py environment --output-format JSON --outfile $OutFile
if (-not (Test-Path $OutFile)) { throw "SBOM generation failed" }
$hash = (Get-FileHash $OutFile -Algorithm SHA256).Hash
Write-Host "[sbom] $OutFile sha256=$hash"
