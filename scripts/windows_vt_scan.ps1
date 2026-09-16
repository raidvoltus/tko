#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $FilePath,
    [int] $MaxDetections = 0,
    [int] $TimeoutMin = 15
)
$ErrorActionPreference = "Stop"
$apiKey = $env:VT_API_KEY
if (-not $apiKey) {
    Write-Host "[vt] VT_API_KEY missing — status NOT RUN"
    exit 0
}
if (-not (Test-Path $FilePath)) { throw "File not found: $FilePath" }
$sizeMb = (Get-Item $FilePath).Length / 1MB
if ($sizeMb -gt 32) { throw "File > 32MB — use VT large upload flow" }

$upload = Invoke-RestMethod -Method Post -Uri "https://www.virustotal.com/api/v3/files" `
    -Headers @{ "x-apikey" = $apiKey } -Form @{ file = Get-Item $FilePath }
$analysisId = $upload.data.id
Write-Host "[vt] analysis=$analysisId"
$deadline = (Get-Date).AddMinutes($TimeoutMin)
do {
    Start-Sleep -Seconds 20
    $report = Invoke-RestMethod -Uri "https://www.virustotal.com/api/v3/analyses/$analysisId" -Headers @{ "x-apikey" = $apiKey }
    $status = $report.data.attributes.status
} while ($status -ne "completed" -and (Get-Date) -lt $deadline)
if ($status -ne "completed") { throw "VT analysis timeout" }
$stats = $report.data.attributes.stats
$detections = [int]$stats.malicious + [int]$stats.suspicious
$sha = (Get-FileHash $FilePath -Algorithm SHA256).Hash
New-Item -ItemType Directory -Force -Path "certification/out" | Out-Null
[ordered]@{
    file = (Resolve-Path $FilePath).Path
    sha256 = $sha
    analysis_id = $analysisId
    stats = $stats
    detections = $detections
    permalink = "https://www.virustotal.com/gui/file/$sha"
    scanned_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json -Depth 5 | Set-Content "certification/out/vt-report.json" -Encoding utf8
if ($detections -gt $MaxDetections) { throw "VirusTotal detections=$detections > $MaxDetections" }
Write-Host "[vt] PASS detections=$detections"
