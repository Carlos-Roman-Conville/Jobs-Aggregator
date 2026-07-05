param(
    [switch]$SkipPipInstall,
    [switch]$SkipDocker,
    [switch]$NoLaunch,
    [switch]$SkipExtensionRefresh,
    [int]$ApiPort = 8000,
    [int]$DashboardPort = 8501
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Ensure-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Missing required command '$name'. $hint"
    }
}

function Set-EnvIfEmpty($key, $value) {
    $current = [Environment]::GetEnvironmentVariable($key, "Process")
    if (-not $current -or [string]::IsNullOrWhiteSpace($current)) {
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

function Load-DotEnv($path) {
    if (-not (Test-Path $path)) { return }
    $lines = Get-Content -Path $path -ErrorAction SilentlyContinue
    foreach ($ln in $lines) {
        if (-not $ln) { continue }
        $trim = $ln.Trim()
        if ($trim.StartsWith("#")) { continue }
        $eq = $trim.IndexOf("=")
        if ($eq -lt 1) { continue }
        $k = $trim.Substring(0, $eq).Trim()
        $v = $trim.Substring($eq + 1).Trim()
        if ($k) {
            [Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
}

function Ensure-PostgresContainer($containerName, $hostPort, $db, $password) {
    Ensure-Command "docker" "Install Docker Desktop and ensure it is running."

    $existing = docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $containerName }
    if (-not $existing) {
        Write-Host "Creating postgres container '$containerName' on port $hostPort..." -ForegroundColor Yellow
        docker run --name $containerName -e POSTGRES_PASSWORD=$password -e POSTGRES_DB=$db -p "${hostPort}:5432" -d postgres:16 | Out-Null
        Start-Sleep -Seconds 2
        return
    }

    $running = docker ps --format "{{.Names}}" | Where-Object { $_ -eq $containerName }
    if (-not $running) {
        Write-Host "Starting existing postgres container '$containerName'..." -ForegroundColor Yellow
        docker start $containerName | Out-Null
        Start-Sleep -Seconds 2
    }
}

function Get-DockerMappedHostPort($containerName, $containerPort) {
    try {
        $line = docker port $containerName $containerPort 2>$null
        if (-not $line) { return $null }
        $m = [regex]::Match($line, ":(\d+)\s*$")
        if ($m.Success) {
            return $m.Groups[1].Value
        }
    }
    catch {
        return $null
    }
    return $null
}

function Start-NewTerminal($title, $command) {
    $escaped = $command.Replace('"', '\"')
    $arg = "-NoExit -Command `"& { `$Host.UI.RawUI.WindowTitle = '$title'; $escaped }`""
    Start-Process powershell -ArgumentList $arg | Out-Null
}

Write-Step "Preparing environment"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

Ensure-Command "python" "Install Python 3.10+."
Load-DotEnv (Join-Path $repoRoot ".env")

Set-EnvIfEmpty "POSTGRES_HOST" "127.0.0.1"
Set-EnvIfEmpty "POSTGRES_PORT" "5433"
Set-EnvIfEmpty "POSTGRES_DB" "postgres"
Set-EnvIfEmpty "POSTGRES_USER" "postgres"
Set-EnvIfEmpty "POSTGRES_PASSWORD" "yourpassword"

$pgHost = $env:POSTGRES_HOST
$pgPort = $env:POSTGRES_PORT
$pgDb = $env:POSTGRES_DB
$pgUser = $env:POSTGRES_USER
$pgPassword = $env:POSTGRES_PASSWORD

# If .env still points at 5432 but postgres-ai is running on another host port
# (common local setup), auto-switch to the mapped docker host port.
try {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $mapped = Get-DockerMappedHostPort -containerName "postgres-ai" -containerPort "5432/tcp"
        if ($mapped) {
            if ($pgPort -eq "5432" -and $mapped -ne "5432") {
                Write-Host "Detected postgres-ai on host port $mapped; overriding POSTGRES_PORT from 5432." -ForegroundColor Yellow
                $pgPort = $mapped
                [Environment]::SetEnvironmentVariable("POSTGRES_PORT", $mapped, "Process")
            }
        }
    }
}
catch {
    # non-fatal; keep configured env
}

Write-Host "Using DB: $pgUser@$pgHost`:$pgPort/$pgDb"

if (-not $SkipPipInstall) {
    Write-Step "Installing/updating Python dependencies"
    python -m pip install -r requirements.txt
}

if (-not $SkipDocker) {
    Write-Step "Ensuring Postgres container is running"
    try {
        Ensure-PostgresContainer -containerName "postgres-ai" -hostPort $pgPort -db $pgDb -password $pgPassword
    }
    catch {
        Write-Warning "Could not auto-manage Docker Postgres: $($_.Exception.Message)"
        Write-Warning "Continuing. If DB is down, API/dashboard will fail until it is available."
    }
}

Write-Step "Running quick schema check"
python -c "from dotenv import load_dotenv; load_dotenv(); from job_pipeline.service import ensure_schema; print(ensure_schema())"

if (-not $SkipExtensionRefresh) {
    Write-Step "Refreshing autofill profile + bundled extension default"
    try {
        python -c "from dotenv import load_dotenv; load_dotenv(); from job_pipeline.autofill_profile import write_autofill_profile_json; print(write_autofill_profile_json())"
        $src = Join-Path $repoRoot "job_pipeline\autofill_profile.json"
        $dst = Join-Path $repoRoot "browser_extension\default_profile.json"
        if (Test-Path $src) {
            Copy-Item -Force $src $dst
            Write-Host "Bundled extension default refreshed: $dst" -ForegroundColor Green
        }
    } catch {
        Write-Warning "Could not refresh autofill profile: $($_.Exception.Message)"
    }
}

Write-Step "Killing stale uvicorn / streamlit processes on target ports"
try {
    foreach ($p in @($ApiPort, $DashboardPort)) {
        $owners = (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
        foreach ($id in $owners) {
            try {
                Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped process $id on port $p" -ForegroundColor Yellow
            } catch {}
        }
    }
    Start-Sleep -Seconds 1
} catch {}

Write-Step "Starting API and Dashboard in new terminals"

$apiCmd = @"
Set-Location '$repoRoot'
`$env:POSTGRES_HOST='$pgHost'
`$env:POSTGRES_PORT='$pgPort'
`$env:POSTGRES_DB='$pgDb'
`$env:POSTGRES_USER='$pgUser'
`$env:POSTGRES_PASSWORD='$pgPassword'
python -m uvicorn api_server:app --host 127.0.0.1 --port $ApiPort --reload
"@

$dashCmd = @"
Set-Location '$repoRoot'
`$env:POSTGRES_HOST='$pgHost'
`$env:POSTGRES_PORT='$pgPort'
`$env:POSTGRES_DB='$pgDb'
`$env:POSTGRES_USER='$pgUser'
`$env:POSTGRES_PASSWORD='$pgPassword'
python -m streamlit run job_dashboard.py --server.port $DashboardPort
"@

if (-not $NoLaunch) {
    Start-NewTerminal -title "Job Pipeline API" -command $apiCmd
    Start-NewTerminal -title "Job Pipeline Dashboard" -command $dashCmd
} else {
    Write-Host "NoLaunch mode enabled - not opening new terminals."
}

Write-Step "Launcher done"
Write-Host "API:       http://127.0.0.1:$ApiPort/health"
Write-Host "Dashboard: http://127.0.0.1:$DashboardPort"
Write-Host ""
Write-Host "Browser extension (one-time install, then auto-loads with Chrome):"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\browser_extension\install_chrome.ps1"
Write-Host "After editing extension JS files: reload at chrome://extensions" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Tip: to skip pip install next time, run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\launch_all.ps1 -SkipPipInstall"
