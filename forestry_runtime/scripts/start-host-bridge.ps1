# Start the host bridge with the deployment settings this repository needs.
#
# Two settings are easy to lose and both fail *quietly*:
#
#   HOST_BRIDGE_KEY          the shared secret the Runtime authenticates with
#   RUNTIME_DATA_HOST_ROOT   the host path of the runtime-owned data directory
#   AGENT_JOB_IMAGE          the image that runs model-written code
#
# Without the last one the bridge falls back to `python:3.12-slim`, which has no
# numpy and no rasterio.  Every capability task then fails inside the job image,
# and the failure looks like the Agent refusing to do straightforward raster work
# (`ModuleNotFoundError: No module named 'numpy'`) rather than a deployment
# mistake.  The image must be the same one the Runtime itself is built from, so
# that "the library is available" means the same thing in both places.
#
#   powershell -NoProfile -File scripts/start-host-bridge.ps1
#   (or `pwsh -File ...` where PowerShell 7 is installed)
#
# Leaves the bridge running in the foreground; run it as a managed background job.

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path $envFile)) {
    throw ".env is missing; copy .env.example and fill in the deployment settings."
}
Get-Content $envFile -Encoding UTF8 |
    Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=' } |
    ForEach-Object {
        $pair = $_ -split '=', 2
        Set-Item -Path ("Env:" + $pair[0].Trim()) -Value $pair[1].Trim()
    }

if (-not $env:AGENT_JOB_IMAGE) {
    $env:AGENT_JOB_IMAGE = "forestry_runtime-runtime:latest"
}
$env:PYTHONIOENCODING = "utf-8"

$existing = docker images --format "{{.Repository}}:{{.Tag}}" |
    Where-Object { $_ -eq $env:AGENT_JOB_IMAGE }
if (-not $existing) {
    throw ("Job image '$($env:AGENT_JOB_IMAGE)' does not exist. Build it first: " +
           "docker compose -f compose.yaml -f compose.eval.yaml up -d --build runtime")
}

Write-Host "host bridge: job image = $($env:AGENT_JOB_IMAGE), data root = $($env:RUNTIME_DATA_HOST_ROOT)"
python host_bridge/server.py
