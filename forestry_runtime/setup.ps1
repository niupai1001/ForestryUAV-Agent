$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.env')) {
    $secretBytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($secretBytes)
    $runtimeKey = -join ($secretBytes | ForEach-Object { $_.ToString('x2') })
    Set-Content -LiteralPath '.env' -Value "RUNTIME_API_KEY=$runtimeKey" -Encoding ascii
    $rng.Dispose()
}
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Runtime deployment failed' }
docker cp openwebui_pipe.py open-webui:/tmp/forestry_runtime_pipe.py
docker cp .env open-webui:/tmp/forestry_runtime.env
docker cp scripts/install_openwebui.py open-webui:/tmp/install_forestry_runtime.py
docker exec open-webui python /tmp/install_forestry_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Open WebUI integration failed' }
docker cp openwebui_cleanup.py open-webui:/tmp/forestry_cleanup.py
docker cp scripts/install_lifecycle.py open-webui:/tmp/install_lifecycle.py
docker cp .env open-webui:/tmp/forestry_runtime.env
docker exec open-webui python /tmp/install_lifecycle.py
if ($LASTEXITCODE -ne 0) { throw 'Chat asset lifecycle installation failed' }
docker restart open-webui
if ($LASTEXITCODE -ne 0) { throw 'Open WebUI restart failed' }
Write-Host 'Refresh Open WebUI and choose Forestry Runtime.'
