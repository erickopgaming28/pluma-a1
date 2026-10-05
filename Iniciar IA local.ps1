# Start installed local providers without downloading or loading model weights.
$ErrorActionPreference = 'Stop'

function Test-AiServer([string]$Address) {
    try {
        $null = Invoke-RestMethod -Uri $Address -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

$ollamaAddress = 'http://127.0.0.1:11434/api/version'
$ollamaExecutable = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-AiServer $ollamaAddress) -and (Test-Path -LiteralPath $ollamaExecutable)) {
    try {
        # Bind only to this computer even if a system setting allows LAN access.
        $env:OLLAMA_HOST = '127.0.0.1:11434'
        Start-Process -FilePath $ollamaExecutable -ArgumentList 'serve' -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $PSScriptRoot '.diagnostic-ollama-server.log') `
            -RedirectStandardError (Join-Path $PSScriptRoot '.diagnostic-ollama-error.log')
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            if (Test-AiServer $ollamaAddress) { break }
            Start-Sleep -Milliseconds 300
        }
    } catch {
        Write-Host 'No se pudo abrir Ollama. Abre su aplicacion para usar la IA.'
    }
}
if (Test-AiServer $ollamaAddress) { Write-Host 'Ollama local listo.' }
else { Write-Host 'Ollama no esta disponible. La edicion del dibujo sigue disponible.' }

$lmAddress = 'http://127.0.0.1:1234/api/v1/models'
$lmExecutable = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'
if (-not (Test-AiServer $lmAddress) -and (Test-Path -LiteralPath $lmExecutable)) {
    try {
        & $lmExecutable server start --port 1234 --bind 127.0.0.1
    } catch {
        Write-Host 'No se pudo abrir LM Studio / Bionic. Abre su servidor local.'
    }
}
if (Test-AiServer $lmAddress) { Write-Host 'LM Studio / Bionic local listo.' }
else { Write-Host 'LM Studio / Bionic no esta disponible. Puedes elegir Ollama o editar sin IA.' }
