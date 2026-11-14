# Check a running InstaRetriv AI instance (local or deployed).
#   .\scripts\check_setup.ps1                                   # http://127.0.0.1:$env:PORT (default 8000)
#   .\scripts\check_setup.ps1 -BaseUrl https://<service>.onrender.com
# Only calls the public /health and /ready endpoints; prints no secrets.
# Configuration warnings are listed in the dashboard (GET /setup/status, login required).
param(
    [string]$BaseUrl = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

if (-not $BaseUrl) {
    if ($Port -le 0) {
        if ($env:PORT) { $Port = [int]$env:PORT } else { $Port = 8000 }
    }
    $BaseUrl = "http://127.0.0.1:$Port"
}
$BaseUrl = $BaseUrl.TrimEnd("/")

$projectRoot = Split-Path -Parent $PSScriptRoot
if ($BaseUrl -match "^http://(127\.0\.0\.1|localhost)") {
    $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $envFile = Join-Path $projectRoot ".env"
    if (-not (Test-Path $pythonExe)) { Write-Host "[!] Missing venv python: $pythonExe" -ForegroundColor Yellow }
    if (-not (Test-Path $envFile)) { Write-Host "[!] Missing .env file (copy .env.example to .env)" -ForegroundColor Yellow }
}

function Get-JsonEndpoint([string]$Url) {
    # Returns @{ Code = <int>; Body = <object or $null> } and never throws for HTTP errors.
    try {
        $resp = Invoke-WebRequest -Method Get -Uri $Url -TimeoutSec 60 -UseBasicParsing
        return @{ Code = [int]$resp.StatusCode; Body = ($resp.Content | ConvertFrom-Json) }
    } catch {
        $response = $_.Exception.Response
        if ($null -eq $response) { throw }
        $code = [int]$response.StatusCode
        $body = $null
        try {
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            $body = $reader.ReadToEnd() | ConvertFrom-Json
        } catch {}
        return @{ Code = $code; Body = $body }
    }
}

Write-Host "Checking $BaseUrl (the first call can take ~1 min if a free Render service is waking up)" -ForegroundColor Cyan

try {
    $health = Get-JsonEndpoint "$BaseUrl/health"
} catch {
    Write-Host "[X] API not reachable at $BaseUrl" -ForegroundColor Red
    Write-Host "    Start it locally with: .\scripts\start_dev.ps1"
    exit 1
}

$h = $health.Body
if ($h.status -eq "ok") {
    Write-Host "[OK] /health: ok (env=$($h.env))" -ForegroundColor Green
} else {
    Write-Host "[!] /health: $($h.status) (env=$($h.env))" -ForegroundColor Yellow
}
if ($h.checks) {
    foreach ($prop in $h.checks.PSObject.Properties) {
        Write-Host "     $($prop.Name): $($prop.Value)"
    }
}

$ready = Get-JsonEndpoint "$BaseUrl/ready"
if ($ready.Code -eq 200) {
    Write-Host "[OK] /ready: HTTP 200 (metadata store reachable)" -ForegroundColor Green
} else {
    Write-Host "[X] /ready: HTTP $($ready.Code) (metadata store unavailable)" -ForegroundColor Red
    Write-Host "    Log in to the dashboard and read the warnings in the status panel (GET /setup/status)."
    exit 2
}

Write-Host "Next: open $BaseUrl/login, sign in, and check the status panel for warnings."
