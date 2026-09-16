#Requires -Version 5.1
<#
.SYNOPSIS
  Verify Authenticode signature + timestamp + chain. Required gate when signing is configured.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $FilePath,
    [string] $SignToolPath = "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $FilePath)) { throw "Artifact not found: $FilePath" }

Write-Host "[verify] file=$FilePath"
if (Test-Path $SignToolPath) {
    & $SignToolPath verify /pa /all /v /tw $FilePath
    if ($LASTEXITCODE -ne 0) { throw "signtool verify failed" }
} else {
    Write-Warning "SignTool missing — using Get-AuthenticodeSignature only"
}

$sig = Get-AuthenticodeSignature -FilePath $FilePath
if ($sig.Status -ne "Valid") { throw "Authenticode status: $($sig.Status)" }
if ($null -eq $sig.TimeStamperCertificate) { throw "Missing RFC3161 timestamp" }

$chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
$chain.ChainPolicy.RevocationMode = "Online"
if (-not $chain.Build($sig.SignerCertificate)) { throw "Chain build failed" }

$result = [ordered]@{
    file        = (Resolve-Path $FilePath).Path
    sha256      = (Get-FileHash $FilePath -Algorithm SHA256).Hash
    signer      = $sig.SignerCertificate.Subject
    thumbprint  = $sig.SignerCertificate.Thumbprint
    timestamp   = $sig.TimeStamperCertificate.Subject
    status      = "VALID"
    verified_at = (Get-Date).ToUniversalTime().ToString("o")
}
$result | ConvertTo-Json -Depth 5 | Set-Content "$FilePath.sigverify.json" -Encoding utf8
Write-Host "[verify] OK -> $FilePath.sigverify.json"
