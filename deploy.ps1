# Builds the Lambda package and uploads it directly to the mbta-server function.
# Usage: .\deploy.ps1

$python312 = "C:\Users\abbyk\AppData\Local\Programs\Python\Python312"
$buildDir = "$PSScriptRoot\.aws-sam\build\MbtaServerFunction"
$zipPath = "$env:TEMP\mbta-deploy.zip"
$functionName = "mbta-server"

$env:PATH = "$python312;" + $env:PATH

Write-Host "Building..." -ForegroundColor Cyan
sam build
if ($LASTEXITCODE -ne 0) { Write-Host "sam build failed" -ForegroundColor Red; exit 1 }

Write-Host "Zipping..." -ForegroundColor Cyan
Compress-Archive -Path "$buildDir\*" -DestinationPath $zipPath -Force

Write-Host "Uploading to Lambda..." -ForegroundColor Cyan
aws lambda update-function-code --function-name $functionName --zip-file "fileb://$zipPath" --query "[FunctionName,LastUpdateStatus]" --output text
if ($LASTEXITCODE -ne 0) { Write-Host "Lambda upload failed" -ForegroundColor Red; exit 1 }

Write-Host "Done. Allow a few seconds for the update to finish." -ForegroundColor Green
