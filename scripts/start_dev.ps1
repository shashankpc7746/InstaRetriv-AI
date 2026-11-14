# Start InstaRetriv AI locally.
#   .\scripts\start_dev.ps1               # no auto-reload
#   .\scripts\start_dev.ps1 -Reload       # auto-reload on code changes
#   .\scripts\start_dev.ps1 -Port 9000    # custom port (default: $env:PORT or 8000)
param(
    [switch]$Reload,
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

if ($Port -le 0) {
    if ($env:PORT) { $Port = [int]$env:PORT } else { $Port = 8000 }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path $pythonExe)) {
    Write-Host "[X] Python venv not found at $pythonExe" -ForegroundColor Red
    Write-Host "    Create it: python -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.txt"
    exit 1
}

if (-not (Test-Path $envFile)) {
    Write-Host "[X] .env not found. Create it first: Copy-Item .env.example .env" -ForegroundColor Red
    exit 1
}

Set-Location $projectRoot

$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", "$Port", "--proxy-headers")
if ($Reload) {
    $uvicornArgs += "--reload"
    Write-Host "Starting on http://${BindHost}:$Port (auto-reload ON)" -ForegroundColor Cyan
} else {
    Write-Host "Starting on http://${BindHost}:$Port (auto-reload OFF; use -Reload to enable)" -ForegroundColor Cyan
}

& $pythonExe @uvicornArgs
exit $LASTEXITCODE
