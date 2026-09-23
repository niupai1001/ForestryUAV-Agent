$ErrorActionPreference = 'Stop'

& "$PSScriptRoot\start_qwen.ps1"

$env:OLLAMA_HOST = '127.0.0.1:11434'
& 'E:\AI\Ollama\ollama.exe' run 'qwen3.5:4b'
