# Download the ngrok CLI into .\ngrok (git-ignored) for local Twilio testing.
#   .\scripts\setup_ngrok.ps1             # tunnel hint for $env:PORT or 8000
#   .\scripts\setup_ngrok.ps1 -Port 9000
# After installing, add your ngrok authtoken once (not stored in this repo):
#   .\ngrok\ngrok.exe config add-authtoken <your-token>
param(
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

if ($Port -le 0) {
    if ($env:PORT) { $Port = [int]$env:PORT } else { $Port = 8000 }
}

$ngrokUrl = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
$ngrokDir = Join-Path (Split-Path -Parent $PSScriptRoot) "ngrok"
$ngrokZip = Join-Path $ngrokDir "ngrok.zip"
$ngrokExe = Join-Path $ngrokDir "ngrok.exe"

if (Test-Path $ngrokExe) {
    Write-Host "ngrok already installed: $ngrokExe" -ForegroundColor Green
} else {
    Write-Host "Setting up ngrok..." -ForegroundColor Cyan
    if (-not (Test-Path $ngrokDir)) {
        New-Item -ItemType Directory -Path $ngrokDir | Out-Null
    }

    Write-Host "Downloading ngrok..." -ForegroundColor Yellow
    try {
        Invoke-WebRequest -Uri $ngrokUrl -OutFile $ngrokZip -UseBasicParsing
    } catch {
        Write-Host "Failed to download ngrok: $_" -ForegroundColor Red
        Write-Host "You can download it manually from https://ngrok.com/download" -ForegroundColor Yellow
        exit 1
    }

    Expand-Archive -Path $ngrokZip -DestinationPath $ngrokDir -Force
    Remove-Item $ngrokZip -Force

    if (-not (Test-Path $ngrokExe)) {
        Write-Host "Error: ngrok.exe not found after extraction" -ForegroundColor Red
        exit 1
    }
    Write-Host "ngrok installed: $ngrokExe" -ForegroundColor Green
}

Write-Host ""
Write-Host "Start a tunnel to the local app (port $Port):" -ForegroundColor Cyan
Write-Host "  & `"$ngrokExe`" http $Port" -ForegroundColor Yellow
Write-Host "Then set PUBLIC_BASE_URL in .env to the https forwarding URL, restart the app, and"
Write-Host "point the Twilio sandbox 'When a message comes in' webhook to <that URL>/webhook (HTTP POST)."
