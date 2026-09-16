#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ArtifactPath,
    [string] $OutFile = "certification/out/provenance.json"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
$sha = (Get-FileHash $ArtifactPath -Algorithm SHA256).Hash.ToLower()
$prov = [ordered]@{
    _type = "https://in-toto.io/Statement/v1"
    subject = @(@{ name = (Split-Path $ArtifactPath -Leaf); digest = @{ sha256 = $sha } })
    predicateType = "https://slsa.dev/provenance/v1"
    predicate = [ordered]@{
        buildDefinition = [ordered]@{
            buildType = "https://github.com/raidvoltus/tko/buildtypes/pyinstaller@v1"
            externalParameters = @{ os = "windows"; python = "3.11" }
        }
        runDetails = [ordered]@{
            builder = @{ id = "https://github.com/raidvoltus/tko/actions/runs/$($env:GITHUB_RUN_ID)" }
            metadata = @{ invocationId = "$($env:GITHUB_RUN_ID)"; startedOn = (Get-Date).ToUniversalTime().ToString("o") }
        }
    }
}
$prov | ConvertTo-Json -Depth 8 | Set-Content $OutFile -Encoding utf8
Write-Host "[slsa] $OutFile"
