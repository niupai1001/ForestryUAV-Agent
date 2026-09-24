# Build a deployable snapshot of this working tree.
#
# `git clone` only reproduces what has been committed. This repository is
# regularly ahead of its last commit (new evaluation cases, knowledge guides,
# runtime changes still uncommitted), and a deployment built from the committed
# state would be a different system from the one that was actually verified. This
# script packages the *working tree* -- tracked files plus untracked,
# non-ignored files -- so the target machine receives exactly what is here now.
#
# Ignored paths (.env, data/, node_modules, frontend/dist, evaluation/work,
# test-output, .venv-prosail) are deliberately absent: secrets and machine
# specific state must not travel, and the frontend is rebuilt inside the image.
param(
    [string]$OutputDirectory,
    [switch]$OpenFolder
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is required to enumerate the files to package'
}

# The tree contains non-ASCII filenames. Two things have to agree for those to
# survive the round trip: git must stop C-quoting them (`core.quotepath=false`)
# and Windows PowerShell 5.1 must decode the native output as UTF-8 instead of
# the OEM code page. Without the pair, `git ls-files` returns escaped octal names
# and every later Test-Path fails with "Illegal characters in path".
$previousEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $repoRoot = (& git rev-parse --show-toplevel).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
        throw "Not inside a git working tree: $projectRoot"
    }

    # `--show-prefix` yields "forestry_runtime/" when this directory is a
    # subdirectory of the repository; the release keeps that same relative layout
    # so the extract matches a clone.
    $prefix = (& git rev-parse --show-prefix).Trim()
    $projectPrefix = $prefix.TrimEnd('/')
    if (-not $projectPrefix) {
        throw 'Run this script from the project subdirectory of the repository'
    }

    # `--full-name` makes ls-files report repository-relative paths regardless of
    # the current directory, which is what the staging copy needs to key off.
    $allFiles = & git -C $repoRoot -c core.quotepath=false ls-files --cached --others --exclude-standard --full-name -- $projectPrefix
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed' }
    if (-not $allFiles) { throw "No files found under $projectPrefix" }

    $dirty = @(& git -C $repoRoot status --porcelain -- $projectPrefix)
    $commit = (& git -C $repoRoot rev-parse --short HEAD).Trim()
} finally {
    [Console]::OutputEncoding = $previousEncoding
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot 'release'
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$zipPath = Join-Path $OutputDirectory ("forestry-agent-$stamp.zip")
$stage = Join-Path $env:TEMP ("forestry-release-$stamp")
$stagingRoot = Join-Path $stage 'forestry_runtime'
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

$prefixWithSeparator = $projectPrefix + '/'
$copied = 0
$missing = @()
try {
    foreach ($file in $allFiles) {
        $relative = $file
        if ($relative.StartsWith($prefixWithSeparator)) {
            $relative = $relative.Substring($prefixWithSeparator.Length)
        }
        $source = Join-Path $projectRoot ($relative -replace '/', '\')
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            # Tracked but deleted in the working tree: the deletion is part of
            # the current state, so the file is simply not packaged.
            $missing += $relative
            continue
        }
        $target = Join-Path $stagingRoot ($relative -replace '/', '\')
        $targetDirectory = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $targetDirectory)) {
            New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
        }
        Copy-Item -LiteralPath $source -Destination $target -Force
        $copied++
    }

    $provenance = @(
        'Forestry Agent deployment snapshot',
        "built:       $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
        "source:      $projectRoot",
        "git commit:  $commit",
        "working tree: " + $(if ($dirty.Count -gt 0) { "modified ($($dirty.Count) entries not committed)" } else { 'clean' }),
        "files:       $copied",
        '',
        'Contents are the working tree at build time, including untracked files but',
        'excluding everything in .gitignore. .env is generated on first start,',
        'data/ and knowledge/ must be copied separately if they are wanted.'
    )
    Set-Content -LiteralPath (Join-Path $stage 'RELEASE.txt') -Value $provenance -Encoding ascii

    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    # Entries are written by hand rather than with Compress-Archive because that
    # cmdlet records Windows backslash separators, which are outside the ZIP
    # specification. Windows tolerates them, but an archive that unpacks into
    # files literally named "forestry_runtime\setup.ps1" on any other platform is
    # not a deployment artifact worth shipping.
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stagingFull = (Resolve-Path -LiteralPath $stage).Path.TrimEnd('\')
    $archive = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($item in Get-ChildItem -LiteralPath $stage -Recurse -File -Force) {
            $entryName = $item.FullName.Substring($stagingFull.Length).TrimStart('\') -replace '\\', '/'
            $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
            $entryStream = $entry.Open()
            try {
                $fileStream = [System.IO.File]::OpenRead($item.FullName)
                try { $fileStream.CopyTo($entryStream) } finally { $fileStream.Dispose() }
            } finally { $entryStream.Dispose() }
        }
    } finally {
        $archive.Dispose()
    }
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}

$sizeMb = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1)
Write-Host "Wrote $zipPath ($copied files, $sizeMb MB, commit $commit)" -ForegroundColor Green
if ($dirty.Count -gt 0) {
    Write-Warning "The working tree has $($dirty.Count) uncommitted change(s); this snapshot contains them, which a plain 'git clone' would not."
}
if ($missing.Count -gt 0) {
    Write-Warning "$($missing.Count) tracked file(s) are deleted in the working tree and were not packaged."
}

if ($OpenFolder) { Invoke-Item $OutputDirectory }
