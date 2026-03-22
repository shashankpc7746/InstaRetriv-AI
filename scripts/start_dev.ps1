param(
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path $pythonExe)) {
    Write-Error "Python venv not found at $pythonExe"
}

if (-not (Test-Path $envFile)) {
    Write-Error ".env not found. Create it from .env.example first."
}

Set-Location $projectRoot

if ($Reload) {
    & $pythonExe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
} else {
    & $pythonExe run.py
}
