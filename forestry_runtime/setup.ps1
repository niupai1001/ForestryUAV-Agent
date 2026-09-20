param(
    [switch]$RotateRuntimeKey,
    [switch]$SkipBuild,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function New-HexSecret {
    $secretBytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($secretBytes)
    $value = -join ($secretBytes | ForEach-Object { $_.ToString('x2') })
    $rng.Dispose()
    return $value
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $lines = if (Test-Path -LiteralPath '.env') { @(Get-Content -LiteralPath '.env' -Encoding UTF8) } else { @() }
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match ('^' + [regex]::Escape($Name) + '=')) {
            $found = $true
            "$Name=$Value"
        } else { $line }
    }
    if (-not $found) { $updated = @($updated) + @("$Name=$Value") }
    Set-Content -LiteralPath '.env' -Value $updated -Encoding utf8
}

if ($RotateRuntimeKey -or -not (Test-Path -LiteralPath '.env') -or -not (Select-String -LiteralPath '.env' -Pattern '^RUNTIME_API_KEY=' -Quiet)) {
    Set-EnvValue 'RUNTIME_API_KEY' (New-HexSecret)
}
if ($RotateRuntimeKey -or -not (Select-String -LiteralPath '.env' -Pattern '^UI_SESSION_KEY=' -Quiet)) {
    Set-EnvValue 'UI_SESSION_KEY' (New-HexSecret)
}
if ($RotateRuntimeKey -or -not (Select-String -LiteralPath '.env' -Pattern '^HOST_BRIDGE_KEY=' -Quiet)) {
    Set-EnvValue 'HOST_BRIDGE_KEY' (New-HexSecret)
}
$dataHostRoot = (Join-Path $PSScriptRoot 'data')
New-Item -ItemType Directory -Force -Path $dataHostRoot | Out-Null
Set-EnvValue 'RUNTIME_DATA_HOST_ROOT' ($dataHostRoot.Replace('\', '/'))
$knowledgeHostRoot = (Join-Path $PSScriptRoot 'knowledge')
New-Item -ItemType Directory -Force -Path $knowledgeHostRoot | Out-Null
Set-EnvValue 'KNOWLEDGE_HOST_ROOT' ($knowledgeHostRoot.Replace('\', '/'))

$envValues = @{}
foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
    if ($line -match '^([^#=]+)=(.*)$') { $envValues[$matches[1]] = $matches[2] }
}
$env:HOST_BRIDGE_KEY = $envValues['HOST_BRIDGE_KEY']
$env:RUNTIME_DATA_HOST_ROOT = $envValues['RUNTIME_DATA_HOST_ROOT']
$env:AGENT_JOB_IMAGE = if ($envValues['AGENT_JOB_IMAGE']) { $envValues['AGENT_JOB_IMAGE'] } else { 'python:3.12-slim' }
$env:AGENT_JOB_CPUS = if ($envValues['AGENT_JOB_CPUS']) { $envValues['AGENT_JOB_CPUS'] } else { '4' }
$env:AGENT_JOB_MEMORY = if ($envValues['AGENT_JOB_MEMORY']) { $envValues['AGENT_JOB_MEMORY'] } else { '6g' }
$env:AGENT_JOB_PIDS = if ($envValues['AGENT_JOB_PIDS']) { $envValues['AGENT_JOB_PIDS'] } else { '256' }
$env:AGENT_MAX_ACTIVE_JOBS = if ($envValues['AGENT_MAX_ACTIVE_JOBS']) { $envValues['AGENT_MAX_ACTIVE_JOBS'] } else { '1' }

function Test-HostBridge {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8011/health' -Headers @{ Authorization = 'Bearer ' + $env:HOST_BRIDGE_KEY } -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}

$pythonCommand = Get-Command python -ErrorAction Stop
$bridgePidFile = Join-Path $dataHostRoot 'host-bridge.pid'
if (Test-Path -LiteralPath $bridgePidFile) {
    $oldBridgePid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $bridgePidFile -Raw).Trim(), [ref]$oldBridgePid)) {
        $oldBridge = Get-Process -Id $oldBridgePid -ErrorAction SilentlyContinue
        if ($oldBridge -and $oldBridge.Path -eq $pythonCommand.Source) {
            Stop-Process -Id $oldBridgePid -Force
            Start-Sleep -Milliseconds 300
        }
    }
    Remove-Item -LiteralPath $bridgePidFile -Force
}

if (-not (Test-HostBridge)) {
    $bridgeLog = Join-Path $dataHostRoot 'host-bridge.log'
    $bridgeError = Join-Path $dataHostRoot 'host-bridge-error.log'
    $bridgeProcess = Start-Process -FilePath $pythonCommand.Source -ArgumentList @('host_bridge/server.py') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $bridgeLog -RedirectStandardError $bridgeError -PassThru
    Set-Content -LiteralPath $bridgePidFile -Value $bridgeProcess.Id -Encoding ascii
    $bridgeDeadline = (Get-Date).AddSeconds(20)
    while (-not (Test-HostBridge) -and (Get-Date) -lt $bridgeDeadline) { Start-Sleep -Milliseconds 250 }
    if (-not (Test-HostBridge)) { throw "Host bridge did not start; inspect $bridgeError" }
}

function Test-OllamaApi {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-OllamaApi)) {
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCommand) {
        throw 'Ollama is not running and ollama.exe is not available in PATH'
    }
    Start-Process -FilePath $ollamaCommand.Source -ArgumentList 'serve' -WindowStyle Hidden
    $ollamaDeadline = (Get-Date).AddSeconds(30)
    while (-not (Test-OllamaApi) -and (Get-Date) -lt $ollamaDeadline) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-OllamaApi)) {
        throw 'Ollama did not become ready on http://127.0.0.1:11434'
    }
}

function Test-DockerApi {
    try {
        & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Start-DockerDesktopAndWait([string]$Executable) {
    Start-Process -FilePath $Executable -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-DockerApi) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
    }
    return (Test-DockerApi)
}

function Repair-DockerStaleSocket {
    $dockerRootCandidate = Join-Path $env:LOCALAPPDATA 'Docker'
    $backendLog = Join-Path $dockerRootCandidate 'log\host\com.docker.backend.exe.log'
    if (-not (Test-Path -LiteralPath $backendLog)) {
        return $false
    }
    $recentLog = (Get-Content -LiteralPath $backendLog -Tail 120) -join "`n"
    if ($recentLog -notmatch 'file cannot be accessed|无法访问') {
        return $false
    }
    if (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^(Docker Desktop|com\.docker\.backend)$' }) {
        return $false
    }

    $sailorSocket = Join-Path $dockerRootCandidate 'run\sailor-ingest.sock'
    $secretsSocket = Join-Path $env:LOCALAPPDATA 'docker-secrets-engine\engine.sock'
    if ($recentLog -match 'sailor-ingest\.sock' -and (Test-Path -LiteralPath $sailorSocket)) {
        $expectedParent = (Resolve-Path -LiteralPath $dockerRootCandidate).Path.TrimEnd('\')
        $directory = (Resolve-Path -LiteralPath (Join-Path $expectedParent 'run')).Path.TrimEnd('\')
        $expectedLeaf = 'run'
    } elseif ($recentLog -match 'docker-secrets-engine[/\\]engine\.sock' -and (Test-Path -LiteralPath $secretsSocket)) {
        $expectedParent = (Resolve-Path -LiteralPath $env:LOCALAPPDATA).Path.TrimEnd('\')
        $directory = (Resolve-Path -LiteralPath (Join-Path $expectedParent 'docker-secrets-engine')).Path.TrimEnd('\')
        $expectedLeaf = 'docker-secrets-engine'
    } else {
        return $false
    }

    if ((Split-Path -Parent $directory).TrimEnd('\') -ne $expectedParent -or (Split-Path -Leaf $directory) -ne $expectedLeaf) {
        throw 'Refusing to repair an unexpected Docker runtime directory'
    }
    $backupLeaf = $expectedLeaf + '.stale-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    $backupRoot = Join-Path $expectedParent $backupLeaf
    if ((Split-Path -Parent $backupRoot).TrimEnd('\') -ne $expectedParent -or (Test-Path -LiteralPath $backupRoot)) {
        throw 'Refusing to overwrite a Docker runtime backup'
    }
    Rename-Item -LiteralPath $directory -NewName $backupLeaf
    New-Item -ItemType Directory -Path $directory | Out-Null
    Write-Warning "Docker left an inaccessible Unix socket. The runtime-only directory was preserved at $backupRoot and recreated."
    return $true
}

if (-not (Test-DockerApi)) {
    $dockerDesktopCandidates = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
    )
    $dockerDesktop = $dockerDesktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($dockerDesktop) {
        $dockerReady = $false
        for ($attempt = 0; $attempt -lt 3 -and -not $dockerReady; $attempt++) {
            $dockerReady = Start-DockerDesktopAndWait $dockerDesktop
            if (-not $dockerReady -and -not (Repair-DockerStaleSocket)) {
                break
            }
        }
    }
    if (-not (Test-DockerApi)) {
        throw 'Docker Desktop Linux engine is unavailable. Runtime files and the host bridge are ready, but container deployment and code jobs require Docker to start successfully.'
    }
}

if (-not $SkipBuild) {
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCommand) {
        throw 'npm is required to build the React workbench'
    }
    Push-Location (Join-Path $PSScriptRoot 'frontend')
    try {
        & $npmCommand.Source ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
        & $npmCommand.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally {
        Pop-Location
    }
}

$composeArgs = @('-f', 'compose.yaml')
if ($envValues['REMOTE_SENSING_PLUGINS_ENABLED'] -eq 'true') {
    $composeArgs += @('-f', 'compose.remote-sensing.yaml')
}
# `docker compose up` writes ordinary progress lines ("Container X Recreate") to
# stderr. With $ErrorActionPreference = 'Stop' PowerShell turns a native command's
# stderr into a terminating error, so the script aborted *after* a successful
# deployment and exited 1. Neither `2>&1` nor `2>$null` avoids that; relaxing the
# preference only for this call does, and the explicit exit-code check below still
# catches real failures.
function Invoke-Compose {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker compose @Arguments
    } finally {
        $ErrorActionPreference = $previous
    }
}

if ($SkipBuild) {
    Invoke-Compose ($composeArgs + @('up', '-d', '--no-build', '--force-recreate', '--remove-orphans', 'runtime'))
} else {
    Invoke-Compose ($composeArgs + @('up', '-d', '--build', '--remove-orphans'))
}
if ($LASTEXITCODE -ne 0) { throw 'Runtime deployment failed' }

$workbenchUrl = 'http://127.0.0.1:8010/'
$runtimeDeadline = (Get-Date).AddSeconds(60)
$runtimeHealth = $null
while (-not $runtimeHealth -and (Get-Date) -lt $runtimeDeadline) {
    try {
        $runtimeHealth = Invoke-RestMethod -Uri ($workbenchUrl + 'health') -TimeoutSec 2
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $runtimeHealth -or $runtimeHealth.status -ne 'ok') {
    docker compose @composeArgs logs --tail 80 runtime
    throw "Runtime did not become healthy at $workbenchUrl"
}

Write-Host "Forestry Agent $($runtimeHealth.version) is ready at $workbenchUrl" -ForegroundColor Green
if ($OpenBrowser) {
    Start-Process $workbenchUrl
}
