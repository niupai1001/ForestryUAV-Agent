# Resume the 72-run capability whose collection was interrupted.
#
# Collection is wall-clock bound: each trial runs a local model to completion, so a
# dropped session, a stopped bridge or a reboot leaves the experiment paused rather
# than corrupt. Recovery is always the same three steps, in this order:
#
#   1. the host bridge, which must carry the package-complete job image;
#   2. the container, which must be on the right *arm* (the arm is a container
#      setting, so it must be restored before anything is collected);
#   3. the collectors, one per evidence root, each resuming from the slots already
#      on disk.
#
# `run_baseline` reuses every completed slot whose fingerprint still matches, so
# running this does not spend a model call on work that is already done. A slot with
# evidence but no record is a review item and aborts the run by design; see the
# handover document for the two ways out.
#
#   powershell -NoProfile -File scripts/resume-collection.ps1 -Arm b
#
# `-Arm a` first recreates the container with arm A's environment. Both arms must
# have been frozen against the same configuration; `ab_experiment --check` reports
# whether they were.

param(
    [ValidateSet("a", "b")]
    [string]$Arm = "b",
    [switch]$SkipContainer
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONIOENCODING = "utf-8"

$cases = "capability.inventory capability.raster_stats capability.ndvi " +
         "capability.chm capability.supervised capability.recompute"

function Show-Arm {
    python -m evaluation.ab_experiment --root evaluation/work --check |
        Select-String -Pattern '"arm"|"ready"' | Select-Object -First 3
}

Write-Host "== 1. host bridge ==" -ForegroundColor Cyan
$bridgeUp = (Get-NetTCPConnection -State Listen -LocalPort 8011 -ErrorAction SilentlyContinue |
             Measure-Object).Count -gt 0
if ($bridgeUp) {
    Write-Host "already listening on 8011"
} else {
    Write-Host "starting the bridge in a new window"
    Start-Process powershell -ArgumentList @(
        "-NoProfile", "-NoExit", "-File", (Join-Path $PSScriptRoot "start-host-bridge.ps1")
    )
    Start-Sleep -Seconds 6
}
python scripts/probe_job_image.py
if ($LASTEXITCODE -ne 0) {
    throw "the job image cannot import what the task set needs; fix AGENT_JOB_IMAGE first"
}

Write-Host "== 2. container arm ==" -ForegroundColor Cyan
if (-not $SkipContainer) {
    python -m evaluation.ab_experiment --root evaluation/work --up $Arm |
        Select-String -Pattern 'returncode|arm' | Select-Object -First 3
}
Show-Arm

Write-Host "== 3. collectors ==" -ForegroundColor Cyan
Write-Host "arm-$Arm main root, and the parallel shard when the arm is B"
if ($Arm -eq "b") {
    Start-Process powershell -ArgumentList @(
        "-NoProfile", "-Command",
        "cd '$projectRoot'; `$env:PYTHONIOENCODING='utf-8'; " +
        "python -m evaluation.run_baseline --root evaluation/work/arm-b " +
        "--tracks agent --cases $cases --repeats 1 2 3"
    )
    Start-Process powershell -ArgumentList @(
        "-NoProfile", "-Command",
        "cd '$projectRoot'; `$env:PYTHONIOENCODING='utf-8'; " +
        "python -m evaluation.run_baseline --root evaluation/work/arm-b-p2 " +
        "--tracks agent --cases capability.supervised capability.recompute --repeats 1 2 3"
    )
} else {
    Start-Process powershell -ArgumentList @(
        "-NoProfile", "-Command",
        "cd '$projectRoot'; `$env:PYTHONIOENCODING='utf-8'; " +
        "python -m evaluation.run_baseline --root evaluation/work/arm-a " +
        "--tracks agent --cases $cases --repeats 1 2 3"
    )
}
Write-Host "collectors started; progress: python evaluation/watch_progress.py"
