# Three-way consistency check: remote branch, local commits, working tree.
#
# These are three different things, and conflating them is how a working tree
# ends up dozens of files away from the branch everyone else is pulling.
# `git status` alone only shows the third; `git log` alone only shows the first
# two. This reports all three and fails when they disagree.
#
# Exit codes: 0 in sync, 1 not in sync, 2 the remote is unusable.
param(
    [switch]$SkipFetch,
    # This machine's HTTPS goes through a local proxy that re-signs certificates,
    # so git's own CA bundle rejects it. Off by default: verification is only
    # dropped when the operator asks for it.
    [switch]$NoVerifyTls,
    [string]$Remote = 'origin',
    [string]$Branch = 'master'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

# Non-ASCII paths are C-quoted by git unless quotepath is off and the console
# decodes native output as UTF-8; without both, every filename is octal soup.
$previousEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:GitExit = 0

function Invoke-Git {
    param([string[]]$Arguments, [string[]]$Config = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $configArgs = @()
        foreach ($pair in @('core.quotepath=false', 'status.relativePaths=false') + $Config) {
            $configArgs += @('-c', $pair)
        }
        $output = & git @configArgs @Arguments 2>&1
        $script:GitExit = $LASTEXITCODE
        return @($output | Where-Object { $_ -notmatch '^warning:' })
    } finally {
        $ErrorActionPreference = $previous
    }
}

# A returned one-element array arrives as a scalar in PowerShell, so `[0]` would
# index the first *character*. Always take the first line explicitly.
function First-Line([object]$Output) {
    return (@($Output) | Select-Object -First 1)
}

function Write-Section([string]$Title) {
    Write-Host ''
    Write-Host $Title -ForegroundColor Cyan
}

$config = @()
if ($NoVerifyTls) { $config = @('http.sslVerify=false') }

$problems = @()

try {
    Invoke-Git -Arguments @('rev-parse', '--git-dir') | Out-Null
    if ($script:GitExit -ne 0) {
        Write-Host 'Not inside a git working tree.' -ForegroundColor Red
        exit 2
    }

    $current = First-Line (Invoke-Git -Arguments @('rev-parse', '--abbrev-ref', 'HEAD'))
    Write-Host "current branch: $current"
    Write-Host "compared with:  $Remote/$Branch"

    if (-not $SkipFetch) {
        Write-Section 'fetch'
        Invoke-Git -Arguments @('fetch', $Remote) -Config $config | Out-Null
        if ($script:GitExit -ne 0) {
            Write-Host "  could not reach $Remote" -ForegroundColor Yellow
            if (-not $NoVerifyTls) {
                Write-Host '  if the failure is a certificate error, a local proxy is' -ForegroundColor Yellow
                Write-Host '  re-signing HTTPS: re-run with -NoVerifyTls to accept that.' -ForegroundColor Yellow
            }
            exit 2
        }
        Write-Host '  remote tracking refs updated'
    }

    $upstream = "$Remote/$Branch"
    $head = First-Line (Invoke-Git -Arguments @('rev-parse', $upstream))
    if ($script:GitExit -ne 0 -or -not $head) {
        Write-Host "  $upstream does not exist; check the branch name" -ForegroundColor Red
        exit 2
    }

    Write-Section 'local commits vs remote'
    $counts = First-Line (Invoke-Git -Arguments @('rev-list', '--left-right', '--count', "$upstream...HEAD"))
    if ($counts) {
        $parts = $counts -split '\s+'
        $behind = [int]$parts[0]
        $ahead = [int]$parts[1]
        Write-Host "  behind: $behind    ahead: $ahead"
        if ($behind -gt 0) {
            $problems += "local commits are $behind behind ${upstream}: pull before working"
            Invoke-Git -Arguments @('log', '--oneline', "HEAD..$upstream") |
                Select-Object -First 10 | ForEach-Object { "    missing: $_" }
        }
        if ($ahead -gt 0) {
            $problems += "local commits are $ahead ahead of ${upstream}: push them"
            Invoke-Git -Arguments @('log', '--oneline', "$upstream..HEAD") |
                Select-Object -First 10 | ForEach-Object { "    unpushed: $_" }
        }
    }

    Write-Section 'working tree vs local commits'
    $status = @(Invoke-Git -Arguments @('status', '--porcelain'))
    if ($status.Count -eq 0) {
        Write-Host '  clean'
    } else {
        $modified = @($status | Where-Object { $_ -match '^ M|^M ' }).Count
        $untracked = @($status | Where-Object { $_ -match '^\?\?' }).Count
        Write-Host "  modified $modified, untracked $untracked, other $($status.Count - $modified - $untracked)"
        $problems += "$($status.Count) uncommitted path(s): they exist on this disk only"
        $status | Select-Object -First 15 | ForEach-Object { "    $_" }
        if ($status.Count -gt 15) { Write-Host "    ... and $($status.Count - 15) more" }
    }

    Write-Section 'working tree vs remote'
    $drift = @(Invoke-Git -Arguments @('diff', '--name-only', $upstream))
    if ($drift.Count -eq 0) {
        Write-Host "  identical to $upstream"
    } else {
        Write-Host "  $($drift.Count) file(s) differ from $upstream"
        $drift | Select-Object -First 15 | ForEach-Object { "    $_" }
        if ($drift.Count -gt 15) { Write-Host "    ... and $($drift.Count - 15) more" }
    }

    Write-Host ''
    if ($problems.Count -eq 0) {
        Write-Host 'IN SYNC: remote, local commits and working tree agree.' -ForegroundColor Green
        exit 0
    }
    Write-Host 'NOT IN SYNC:' -ForegroundColor Yellow
    $problems | ForEach-Object { Write-Host "  - $_" }
    exit 1
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
