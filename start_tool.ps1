$ErrorActionPreference = "Stop"

$composeFile = Join-Path $PSScriptRoot "docker-compose.yml"

if (-not $env:CREDENTIAL_PASSWORD) {
    $env:CREDENTIAL_PASSWORD = Read-Host "Enter credential file password"
}

Write-Host "Building and starting validation tool..."
docker compose -f $composeFile up --build -d

Write-Host ""
Write-Host "Waiting for services..."
$deadline = (Get-Date).AddMinutes(2)
$uiReady = $false
$apiReady = $false

while ((Get-Date) -lt $deadline -and (-not ($uiReady -and $apiReady))) {
    if (-not $uiReady) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:3000" -TimeoutSec 3
            $uiReady = [int]$response.StatusCode -lt 500
        } catch {
            $uiReady = $false
        }
    }

    if (-not $apiReady) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8000/docs" -TimeoutSec 3
            $apiReady = [int]$response.StatusCode -lt 500
        } catch {
            $apiReady = $false
        }
    }

    if (-not ($uiReady -and $apiReady)) {
        Start-Sleep -Seconds 2
    }
}

Write-Host ""
Write-Host "Validation tool is ready."
Write-Host "UI:  http://localhost:3000"
Write-Host "API: http://localhost:8000"
Write-Host ""
Write-Host "To view logs: docker compose -f `"$composeFile`" logs -f"
