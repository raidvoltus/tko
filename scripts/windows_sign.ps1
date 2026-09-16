#Requires -Version 5.1
<#
.SYNOPSIS
  Sign TKO artifacts using Azure Trusted Signing via SignTool (OIDC / dlib).
.NOTES
  Requires Trusted Signing client tools + SignTool >= 10.0.2261.755
  No PFX on disk when using federated credential + dlib metadata file.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $FilePath,
    [Parameter(Mandatory)] [string] $Endpoint,
    [Parameter(Mandatory)] [string] $AccountName,
    [Parameter(Mandatory)] [string] $CertProfile,
    [string] $TimestampUrl = "http://timestamp.acs.microsoft.com",
    [string] $SignToolPath = "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $FilePath)) { throw "Artifact not found: $FilePath" }
if (-not (Test-Path $SignToolPath)) { throw "SignTool not found: $SignToolPath" }

$dlib = Join-Path $env:USERPROFILE ".trusted-signing\Azure.CodeSigning.Dlib.dll"
if (-not (Test-Path $dlib)) { throw "Trusted Signing dlib not found: $dlib" }

$meta = New-TemporaryFile
@"
{
  "Endpoint": "$Endpoint",
  "CodeSigningAccountName": "$AccountName",
  "CertificateProfileName": "$CertProfile"
}
"@ | Set-Content -Path $meta.FullName -Encoding utf8

Write-Host "[sign] file=$FilePath profile=$CertProfile"
try {
    & $SignToolPath sign /v /fd SHA256 /tr $TimestampUrl /td SHA256 /dlib $dlib /dmdf $meta.FullName $FilePath
    if ($LASTEXITCODE -ne 0) { throw "SignTool failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item -Force $meta.FullName -ErrorAction SilentlyContinue
}
Write-Host "[sign] OK"
