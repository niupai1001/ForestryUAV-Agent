# Hand arm B over to arm A without a human watching the last two slots.
#
# Two things must happen in order: arm B's remaining supervised slots must finish, and
# only then may the container be recreated with arm A's environment. Recreating it
# earlier cancels whatever is running -- which is not an Agent outcome, and would turn
# good measurement time into `infra_error` slots that have to be collected again.
#
#   powershell -NoProfile -File scripts/supervise-handover.ps1

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$roots = @("evaluation/work/arm-b", "evaluation/work/arm-b-p2")
$cases = "capability.inventory capability.raster_stats capability.ndvi " +
         "capability.chm capability.supervised capability.recompute"

function Get-Graded([string]$root) {
    if (-not (Test-Path $root)) { return 0 }
    return (Get-ChildItem $root -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName "record.json") } |
            Measure-Object).Count
}

$target = 36 + 12   # arm-b slots plus the shard's supervised/recompute half
Write-Host "waiting for arm B to finish ($target graded slots across both roots)"
while ($true) {
    $done = (Get-Graded "evaluation/work/arm-b") + (Get-Graded "evaluation/work/arm-b-p2")
    $running = (Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
                Where-Object { $_.CommandLine -match 'run_baseline' } |
                Measure-Object).Count
    Write-Host ("  {0:HH:mm:ss}  graded {1}/{2}  collectors {3}" -f (Get-Date), $done, $target, $running)
    if ($running -eq 0) { break }
    Start-Sleep -Seconds 120
}

Write-Host "arm B collectors have stopped; switching the container to arm A"
python -m evaluation.ab_experiment --root evaluation/work --up a
python -m evaluation.ab_experiment --root evaluation/work --check |
    Select-String -Pattern '"arm"|"ready"' | Select-Object -First 3

# The two roots own disjoint trial numbers, named by absolute number in a shard file.
# Passing `--repeats` per root instead would renumber the shard's slots ("repeat 1"
# of the *supervised* subset is global slot 1, not 4), which is how a parallel root
# once produced `capability-recompute-4` holding the normal condition.
$shardDir = Join-Path $projectRoot "evaluation/shards"
foreach ($spec in @(
    @{ root = "evaluation/work/arm-a";    cases = "capability.inventory capability.raster_stats capability.ndvi capability.chm"; args = "" },
    @{ root = "evaluation/work/arm-a-p2"; cases = "capability.supervised capability.recompute"; args = "--shard '$shardDir/arm-a-p2-supervised-recompute.json'" }
)) {
    Start-Process powershell -ArgumentList @(
        "-NoProfile", "-Command",
        "cd '$projectRoot'; `$env:PYTHONIOENCODING='utf-8'; " +
        "python -m evaluation.run_baseline --root $($spec.root) " +
        "--tracks agent --cases $($spec.cases) $($spec.args)"
    )
    Write-Host "started collector for $($spec.root)"
}
Write-Host "progress: python evaluation/watch_progress.py"
