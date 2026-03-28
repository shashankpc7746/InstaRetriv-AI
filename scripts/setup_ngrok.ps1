# Setup ngrok for InstaRetriv AI
# Downloads ngrok CLI and configures it for local tunneling

$ngrokUrl = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
$ngrokDir = "$PSScriptRoot\..\ngrok"
$ngrokZip = "$ngrokDir\ngrok.zip"

Write-Host "Setting up ngrok..." -ForegroundColor Cyan

# Create ngrok directory
if (-not (Test-Path $ngrokDir)) {
    New-Item -ItemType Directory -Path $ngrokDir | Out-Null
    Write-Host "Created directory: $ngrokDir" -ForegroundColor Green
}

# Download ngrok
Write-Host "Downloading ngrok..." -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri $ngrokUrl -OutFile $ngrokZip -ErrorAction Stop
    Write-Host "Downloaded ngrok" -ForegroundColor Green
} catch {
    Write-Host "Failed to download ngrok: $_" -ForegroundColor Red
    Write-Host "You can manually download from: https://ngrok.com/download" -ForegroundColor Yellow
    exit 1
}

# Extract ngrok
Write-Host "Extracting ngrok..." -ForegroundColor Yellow
Expand-Archive -Path $ngrokZip -DestinationPath $ngrokDir -Force
Write-Host "Extracted ngrok" -ForegroundColor Green

# Clean up zip
Remove-Item $ngrokZip -Force

$ngrokExe = "$ngrokDir\ngrok.exe"

if (Test-Path $ngrokExe) {
    Write-Host "ngrok installed successfully!" -ForegroundColor Green
    Write-Host "Location: $ngrokExe" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To start ngrok tunnel on port 8000, run:" -ForegroundColor Cyan
    Write-Host "$ngrokExe http 8000" -ForegroundColor Yellow
} else {
    Write-Host "Error: ngrok.exe not found after extraction" -ForegroundColor Red
}
