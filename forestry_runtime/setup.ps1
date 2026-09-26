param(
    [switch]$RotateRuntimeKey,
    [switch]$SkipBuild,
    [switch]$OpenBrowser,
    [switch]$SkipModelPull
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# ---------------------------------------------------------------------------
# Prerequisites on a freshly provisioned machine are only: Docker Desktop,
# Ollama, and a Python 3 interpreter for the host bridge. Node and npm are not
# requirements any more -- the React workbench is compiled inside the image by
# the `frontend` stage of the Dockerfile. That matters on Windows because
# `npm.ps1` is refused by the default execution policy on many machines, which
# used to make a correct deployment look like a broken one.
# ---------------------------------------------------------------------------

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

# `docker compose up` and `ollama pull` write ordinary progress lines to stderr.
# With $ErrorActionPreference = 'Stop' PowerShell turns a native command's stderr
# into a terminating error, so a successful deployment aborted and exited 1.
# Neither `2>&1` nor `2>$null` avoids that; relaxing the preference only for the
# call does, and the explicit exit-code check still catches real failures.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        # Probes whose output is irrelevant (for example `docker image inspect`,
        # which prints an entire image manifest on success) set this so a
        # successful check does not scroll the operator's terminal.
        [switch]$Quiet
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Out-Host keeps the command's own output visible while leaving the
        # pipeline carrying only the exit code this function is documented to
        # return; without it a caller comparing the result to 0 would instead
        # compare a stream of progress lines.
        if ($Quiet) {
            & $FilePath @Arguments | Out-Null
        } else {
            & $FilePath @Arguments | Out-Host
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Resolve-Python {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $prefix = @()
        if ($candidate -eq 'py') { $prefix = @('-3') }
        $probe = Invoke-Native -FilePath $command.Source -Arguments ($prefix + @('-c', 'import sys;print(sys.version_info[0])'))
        if ($probe -eq 0) {
            return [pscustomobject]@{ Exe = $command.Source; Prefix = $prefix }
        }
    }
    return $null
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

# `compose.yaml` bind-mounts this ledger to rank domain guides. The file is a
# verification record rather than source, so a fresh clone does not contain it;
# Docker would then create a *directory* with that name and the mount would fail
# on every later start. An explicit placeholder keeps the mount valid and makes
# the Runtime report "no reviewed claims" instead of an unavailable ledger.
$guideDir = Join-Path $PSScriptRoot 'evaluation\grounded_v1'
$guideFile = Join-Path $guideDir 'GUIDE_VERIFICATION.md'
if (Test-Path -LiteralPath $guideFile -PathType Container) {
    if ((Get-ChildItem -LiteralPath $guideFile -Force | Measure-Object).Count -eq 0) {
        Remove-Item -LiteralPath $guideFile -Force
    } else {
        Write-Warning "Expected a file at $guideFile but found a non-empty directory; leaving it untouched."
    }
}
New-Item -ItemType Directory -Force -Path $guideDir | Out-Null
if (-not (Test-Path -LiteralPath $guideFile)) {
    Set-Content -LiteralPath $guideFile -Encoding ascii -Value @(
        '# Guide verification ledger',
        '',
        'This deployment has no reviewed guide claims. Every guide therefore',
        'keeps its unreviewed relevance weight; nothing is asserted as verified.'
    )
}

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
$ollamaModel = if ($envValues['OLLAMA_MODEL']) { $envValues['OLLAMA_MODEL'] } else { 'qwen3.8:27b' }
$remoteSensingEnabled = ($envValues['REMOTE_SENSING_PLUGINS_ENABLED'] -eq 'true')

# --- Host bridge -----------------------------------------------------------

$python = Resolve-Python
if (-not $python) {
    throw @'
A Python 3 interpreter is required to run host_bridge/server.py, which owns
authorized host-file access and Docker job containers. Install Python 3.11+
from https://www.python.org/downloads/ and select "Add python.exe to PATH",
then run start.cmd again. Docker alone is not enough for this component.
'@
}

function Test-HostBridge {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8011/health' -Headers @{ Authorization = 'Bearer ' + $env:HOST_BRIDGE_KEY } -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}

$bridgePidFile = Join-Path $dataHostRoot 'host-bridge.pid'
if (Test-Path -LiteralPath $bridgePidFile) {
    $oldBridgePid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $bridgePidFile -Raw).Trim(), [ref]$oldBridgePid)) {
        $oldBridge = Get-Process -Id $oldBridgePid -ErrorAction SilentlyContinue
        if ($oldBridge -and $oldBridge.Path -eq $python.Exe) {
            Stop-Process -Id $oldBridgePid -Force
            Start-Sleep -Milliseconds 300
        }
    }
    Remove-Item -LiteralPath $bridgePidFile -Force
}

if (-not (Test-HostBridge)) {
    $bridgeLog = Join-Path $dataHostRoot 'host-bridge.log'
    $bridgeError = Join-Path $dataHostRoot 'host-bridge-error.log'
    $bridgeProcess = Start-Process -FilePath $python.Exe -ArgumentList ($python.Prefix + @('host_bridge/server.py')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $bridgeLog -RedirectStandardError $bridgeError -PassThru
    Set-Content -LiteralPath $bridgePidFile -Value $bridgeProcess.Id -Encoding ascii
    $bridgeDeadline = (Get-Date).AddSeconds(20)
    while (-not (Test-HostBridge) -and (Get-Date) -lt $bridgeDeadline) { Start-Sleep -Milliseconds 250 }
    if (-not (Test-HostBridge)) { throw "Host bridge did not start; inspect $bridgeError" }
}

# --- Ollama and the model the Runtime actually calls ------------------------

function Test-OllamaApi {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Resolve-OllamaExecutable {
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )
    return ($candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
}

$ollamaExe = Resolve-OllamaExecutable

if (-not (Test-OllamaApi)) {
    if (-not $ollamaExe) {
        throw @'
Ollama is not running and ollama.exe was not found. The Agent has no reasoning
without it. Install Ollama from https://ollama.com/download, start it, then run
start.cmd again.
'@
    }
    Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
    $ollamaDeadline = (Get-Date).AddSeconds(30)
    while (-not (Test-OllamaApi) -and (Get-Date) -lt $ollamaDeadline) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-OllamaApi)) {
        throw 'Ollama did not become ready on http://127.0.0.1:11434'
    }
}

function Get-OllamaModelNames {
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
    return @($tags.models | ForEach-Object { $_.name })
}

$installedModels = Get-OllamaModelNames
$modelInstalled = $false
foreach ($name in $installedModels) {
    if ($name -eq $ollamaModel -or $name -eq ($ollamaModel + ':latest')) { $modelInstalled = $true }
}
if (-not $modelInstalled) {
    if ($SkipModelPull) {
        Write-Warning "Model '$ollamaModel' is not installed and -SkipModelPull was given; the workbench will answer with a model error until you run: ollama pull $ollamaModel"
    } elseif (-not $ollamaExe) {
        Write-Warning "Model '$ollamaModel' is not installed and ollama.exe was not found; run: ollama pull $ollamaModel"
    } else {
        Write-Host "Pulling Ollama model '$ollamaModel' (several GB on first run)..." -ForegroundColor Cyan
        $pullExit = Invoke-Native -FilePath $ollamaExe -Arguments @('pull', $ollamaModel)
        if ($pullExit -ne 0) {
            throw "Could not pull '$ollamaModel'. Pull it manually (ollama pull $ollamaModel) and run start.cmd again."
        }
    }
}

# --- Docker Desktop ---------------------------------------------------------

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

# Code jobs run `python:3.12-slim` by default. Pulling it here keeps the first
# code_run from stalling inside a job container the operator cannot see. This is
# a convenience, not a correctness requirement, so a failure only warns.
$jobImageExit = Invoke-Native -FilePath 'docker' -Arguments @('image', 'inspect', $env:AGENT_JOB_IMAGE) -Quiet
if ($jobImageExit -ne 0) {
    Write-Host "Pulling job image '$($env:AGENT_JOB_IMAGE)'..." -ForegroundColor Cyan
    $jobImagePullExit = Invoke-Native -FilePath 'docker' -Arguments @('pull', $env:AGENT_JOB_IMAGE)
    if ($jobImagePullExit -ne 0) {
        Write-Warning "Could not pull '$($env:AGENT_JOB_IMAGE)'; code jobs will pull it on first use."
    }
}

# --- Runtime ----------------------------------------------------------------

$composeArgs = @('-f', 'compose.yaml')
if ($remoteSensingEnabled) {
    $composeArgs += @('-f', 'compose.remote-sensing.yaml')
}

function Invoke-Compose {
    param([string[]]$Arguments)
    return (Invoke-Native -FilePath 'docker' -Arguments (@('compose') + $Arguments))
}

if ($SkipBuild) {
    $composeExit = Invoke-Compose ($composeArgs + @('up', '-d', '--no-build', '--force-recreate', '--remove-orphans', 'runtime'))
} else {
    $composeExit = Invoke-Compose ($composeArgs + @('up', '-d', '--build', '--remove-orphans'))
}
if ($composeExit -ne 0) { throw 'Runtime deployment failed' }

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

# The scorecard viewer is part of the deployment, not an optional extra: the workbench
# has a button for it, and a button that only works after the operator types a command
# in a terminal is not a button. Started here with the same detached pattern as the
# host bridge, so it is already up the first time it is clicked.
function Test-Scorecard {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8012/api/health' -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}

$scorecardUrl = 'http://127.0.0.1:8012/'
if (-not (Test-Scorecard)) {
    $scorecardPidFile = Join-Path $dataHostRoot 'scorecard.pid'
    $scorecardLog = Join-Path $dataHostRoot 'scorecard.log'
    $scorecardError = Join-Path $dataHostRoot 'scorecard-error.log'
    $scorecardProcess = Start-Process -FilePath $python.Exe `
        -ArgumentList ($python.Prefix + @('-m', 'evaluation.dashboard', '--no-open')) `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
        -RedirectStandardOutput $scorecardLog -RedirectStandardError $scorecardError -PassThru
    Set-Content -LiteralPath $scorecardPidFile -Value $scorecardProcess.Id -Encoding ascii
    $scorecardDeadline = (Get-Date).AddSeconds(20)
    while (-not (Test-Scorecard) -and (Get-Date) -lt $scorecardDeadline) {
        Start-Sleep -Milliseconds 250
    }
    if (Test-Scorecard) {
        Write-Host "Scorecard viewer is ready at $scorecardUrl" -ForegroundColor Green
    } else {
        # A missing scorecard must not fail a working deployment; the workbench tells
        # the operator what is wrong when the button is clicked.
        Write-Warning "Scorecard viewer did not start; inspect $scorecardError"
    }
}

if (-not $remoteSensingEnabled) {
    Write-Host 'Remote-sensing plugins are off. Set REMOTE_SENSING_PLUGINS_ENABLED=true in .env to enable raster, UAV audit and PROSAIL tools.' -ForegroundColor DarkGray
}

if ($OpenBrowser) {
    Start-Process $workbenchUrl
}
