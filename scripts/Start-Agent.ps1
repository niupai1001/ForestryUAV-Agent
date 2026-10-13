param([string]$Config, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $projectRoot 'workspace\launcher'

try {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Python environment missing: run uv sync --locked --group dev in the project directory first.'
    }
    $arguments = @(('"{0}"' -f (Join-Path $PSScriptRoot 'launch_agent.py')))
    if ($Config) {
        $arguments += '--config'
        $arguments += ('"{0}"' -f $Config)
    }
    if ($NoBrowser) { $arguments += '--no-browser' }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDirectory "bootstrap-$stamp-$PID.out.log") `
        -RedirectStandardError (Join-Path $logDirectory "bootstrap-$stamp-$PID.err.log")
    # Start-Process -Wait waits for the entire tree, including persistent servers.
    # Wait for the bootstrap only; Agent/Ollama must outlive it.
    $process.WaitForExit()
    exit $process.ExitCode
} catch {
    $message = "Unable to start ForestryUAV-Agent.`n$($_.Exception.Message)`nDocumentation: $projectRoot\docs\ONE-CLICK-START-zh.md"
    if (Test-Path -LiteralPath $logDirectory) {
        $message | Out-File -LiteralPath (Join-Path $logDirectory 'bootstrap-error.txt') -Encoding utf8
    }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, 'ForestryUAV-Agent') | Out-Null
    exit 1
}
