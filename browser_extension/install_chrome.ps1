#Requires -Version 5.1
<#
.SYNOPSIS
  Export autofill profile, start local API, open Chrome at chrome://extensions
  and print the path to load.

  Chrome requires one manual click to load an unpacked extension (chrome://extensions
  -> Load unpacked -> pick the browser_extension folder). This script does
  everything else.

.NOTES
  If Chrome shows a manifest error mentioning "background.scripts is not allowed",
  rename manifest.chrome.json over manifest.json (see README) and retry.
#>
$ErrorActionPreference = "Stop"
$ExtDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ExtDir
Set-Location $RepoRoot

Write-Host "== Job Pipeline Autofill setup (Chrome) ==" -ForegroundColor Cyan

Write-Host "[1/4] Exporting autofill profile..."
python -c "from job_pipeline.autofill_profile import write_autofill_profile_json; print(write_autofill_profile_json())"
Copy-Item -Force "job_pipeline\autofill_profile.json" "browser_extension\default_profile.json"

# Find Chrome — check common install locations, then PATH.
$ChromeCandidates = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LocalAppData}\Google\Chrome\Application\chrome.exe"
)
$Chrome = $null
foreach ($p in $ChromeCandidates) {
    if (Test-Path $p) { $Chrome = $p; break }
}
if (-not $Chrome) {
    $cmd = Get-Command "chrome.exe" -ErrorAction SilentlyContinue
    if ($cmd) { $Chrome = $cmd.Source }
}
if (-not $Chrome) {
    throw "Chrome not found. Install Chrome or open chrome://extensions manually and Load unpacked at $ExtDir"
}
Write-Host "Chrome: $Chrome"

$ApiPort = 8000
$ApiRunning = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/health" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ApiRunning = $true }
} catch {}

if (-not $ApiRunning) {
    Write-Host "[2/4] Starting API on port $ApiPort (background)..."
    Start-Process -WindowStyle Hidden python -ArgumentList @(
        "-m", "uvicorn", "api_server:app", "--host", "127.0.0.1", "--port", "$ApiPort"
    ) -WorkingDirectory (Get-Location)
    Start-Sleep -Seconds 3
} else {
    Write-Host "[2/4] API already running on port $ApiPort."
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health" -TimeoutSec 5
    Write-Host "API health ok=$($health.ok)"
} catch {
    Write-Warning "API not responding yet. Extension still works via bundled default_profile.json."
}

Write-Host "[3/4] Launching Chrome at chrome://extensions..."
Start-Process $Chrome "chrome://extensions"

Write-Host "[4/4] One manual step:" -ForegroundColor Yellow
Write-Host "  1. Toggle 'Developer mode' ON (top-right of chrome://extensions)"
Write-Host "  2. Click 'Load unpacked'"
Write-Host "  3. Select this folder:" -NoNewline; Write-Host " $ExtDir" -ForegroundColor Green
Write-Host ""
Write-Host "On any job application page:" -ForegroundColor Cyan
Write-Host '  1. Click the Job Pipeline Autofill icon in Chrome toolbar (puzzle-piece -> pin it for easy access)'
Write-Host '  2. Click "Fill this application"'
Write-Host '  Profile is pre-loaded from default_profile.json (no sync required).'
Write-Host ""
Write-Host "If Chrome shows 'background.scripts is not allowed':" -ForegroundColor DarkYellow
Write-Host "  Copy-Item -Force browser_extension\manifest.chrome.json browser_extension\manifest.json"
Write-Host "  Then click 'Reload' next to the extension on chrome://extensions"
