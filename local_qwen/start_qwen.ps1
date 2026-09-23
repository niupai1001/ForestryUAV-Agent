$ErrorActionPreference = 'Stop'

$ollamaExe = 'E:\AI\Ollama\ollama.exe'
$modelDir = 'E:\AI\OllamaModels'
$logDir = 'E:\AI\OllamaLogs'
$hostAddress = '127.0.0.1:11434'

if (-not (Test-Path -LiteralPath $ollamaExe)) {
    throw "Ollama executable was not found at $ollamaExe"
}

New-Item -ItemType Directory -Force -Path $modelDir, $logDir | Out-Null

$env:OLLAMA_MODELS = $modelDir
$env:OLLAMA_HOST = $hostAddress
$env:OLLAMA_CONTEXT_LENGTH = '32768'

try {
    $version = Invoke-RestMethod -Uri "http://$hostAddress/api/version" -TimeoutSec 2
    Write-Host "Ollama is already running (version $($version.version))."
    exit 0
} catch {
    # Start the local-only API server without opening a console window.
}

$process = Start-Process `
    -FilePath $ollamaExe `
    -ArgumentList 'serve' `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'server.out.log') `
    -RedirectStandardError (Join-Path $logDir 'server.err.log') `
    -PassThru

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $version = Invoke-RestMethod -Uri "http://$hostAddress/api/version" -TimeoutSec 2
        Write-Host "Ollama started (PID $($process.Id), version $($version.version))."
        Write-Host "API: http://$hostAddress"
        exit 0
    } catch {
        # Continue waiting while the server initializes.
    }
}

throw "Ollama did not start. Check $logDir\server.err.log"
