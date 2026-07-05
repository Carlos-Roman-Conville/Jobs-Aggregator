#Requires -Version 5.1
<#
.SYNOPSIS
  Export autofill profile, start local API, launch Firefox with the extension loaded.

  One-time manual step (Firefox security): if web-ext is unavailable, the script opens
  about:debugging and prints the manifest path to load.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Split-Path -Parent $RepoRoot)

Write-Host "== Job Pipeline Autofill setup ==" -ForegroundColor Cyan

Write-Host "[1/4] Exporting autofill profile..."
python -c "from job_pipeline.autofill_profile import write_autofill_profile_json; print(write_autofill_profile_json())"
Copy-Item -Force "job_pipeline\autofill_profile.json" "browser_extension\default_profile.json"

$Firefox = "${env:ProgramFiles}\Mozilla Firefox\firefox.exe"
if (-not (Test-Path $Firefox)) {
    $Firefox = "${env:ProgramFiles(x86)}\Mozilla Firefox\firefox.exe"
}
if (-not (Test-Path $Firefox)) {
    throw "Firefox not found. Install Firefox and re-run."
}
Write-Host "Firefox: $Firefox"

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

$ExtDir = Join-Path (Get-Location) "browser_extension"
$Manifest = Join-Path $ExtDir "manifest.json"

Write-Host "[3/4] Launching Firefox with extension..."
$WebExt = Get-Command npx -ErrorAction SilentlyContinue
if ($WebExt) {
    Set-Location $ExtDir
    Write-Host "Using web-ext (opens Firefox with extension loaded)..."
    Start-Process -FilePath "npx" -ArgumentList @(
        "--yes", "web-ext", "run",
        "--source-dir", ".",
        "--firefox", $Firefox,
        "--keep-profile-changes"
    ) -WorkingDirectory $ExtDir
    Set-Location (Split-Path -Parent $ExtDir)
} else {
    Write-Host "web-ext not available - opening about:debugging for manual load."
    Start-Process $Firefox "about:debugging#/runtime/this-firefox"
    Write-Host ""
    Write-Host "MANUAL (once):" -ForegroundColor Yellow
    Write-Host "  Load Temporary Add-on -> select:"
    Write-Host "  $Manifest" -ForegroundColor Green
}

Write-Host "[4/4] Done."
Write-Host ""
Write-Host "On any job application page:" -ForegroundColor Cyan
Write-Host '  1. Click the Job Pipeline Autofill icon in Firefox toolbar'
Write-Host '  2. Click Fill this application'
Write-Host '  Profile is pre-loaded from default_profile.json (no sync required).'
