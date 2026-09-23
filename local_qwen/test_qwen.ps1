$ErrorActionPreference = 'Stop'

& "$PSScriptRoot\start_qwen.ps1"

$request = @{
    model = 'qwen3.5:4b'
    messages = @(
        @{
            role = 'user'
            content = 'Reply with exactly these two words: deployment successful'
        }
    )
    stream = $false
    think = $false
    options = @{
        num_ctx = 32768
        temperature = 0
    }
} | ConvertTo-Json -Depth 6

$response = Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:11434/api/chat' `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($request))

$response.message.content
