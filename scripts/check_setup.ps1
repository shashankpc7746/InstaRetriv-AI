$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path $pythonExe)) {
    Write-Host "[X] Missing venv python: $pythonExe"
    exit 1
}

if (-not (Test-Path $envFile)) {
    Write-Host "[X] Missing .env file"
    exit 1
}

Set-Location $projectRoot

try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
    Write-Host "[OK] API is up: $($health.status)"
} catch {
    Write-Host "[X] API not reachable at http://127.0.0.1:8000"
    Write-Host "    Start it with: .\scripts\start_dev.ps1"
    exit 1
}

try {
    $status = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/setup/status" -TimeoutSec 5
    Write-Host "[OK] Twilio SID set: $($status.twilio_sid_set)"
    Write-Host "[OK] Twilio token set: $($status.twilio_auth_token_set)"
    Write-Host "[OK] Public base URL set: $($status.public_base_url_set)"
    Write-Host "[OK] Signature validation enabled: $($status.require_twilio_signature)"
} catch {
    Write-Host "[X] Could not read /setup/status"
    exit 1
}
