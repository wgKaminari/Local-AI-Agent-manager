$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$downloadFolder = Join-Path $projectRoot 'private\tools'
New-Item -ItemType Directory -Force -Path $downloadFolder | Out-Null
$installerPath = Join-Path $downloadFolder 'OllamaSetup.exe'
$installDirectory = Join-Path $env:USERPROFILE 'AppData\Local\Programs\Ollama'
$ollamaExecutable = Join-Path $installDirectory 'ollama.exe'

if (-not (Test-Path -LiteralPath $ollamaExecutable)) {
    Write-Output 'Downloading Ollama from its official Windows download URL...'
    Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile $installerPath
    $installerSignature = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($installerSignature.Status -ne 'Valid' -or $installerSignature.SignerCertificate.Subject -notmatch 'Ollama') {
        throw 'The installer did not have a valid Ollama publisher signature. Installation was stopped.'
    }
    Write-Output 'Verified installer publisher. Installing the free local runtime...'
    $installation = Start-Process -FilePath $installerPath -ArgumentList @('/VERYSILENT', '/NORESTART', '/SP-', ('/DIR="' + $installDirectory + '"')) -PassThru -Wait -WindowStyle Hidden
    if ($installation.ExitCode -ne 0) { throw ('Ollama installation exited with code ' + $installation.ExitCode) }
}
if (-not (Test-Path -LiteralPath $ollamaExecutable)) { throw 'The Ollama executable was not found after installation.' }

$ollamaReady = $false
try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
    $ollamaReady = $true
} catch { }
if (-not $ollamaReady) {
    Start-Process -FilePath $ollamaExecutable -ArgumentList 'serve' -WindowStyle Hidden
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2
            $ollamaReady = $true
            break
        } catch { }
    }
}
if (-not $ollamaReady) { throw 'Ollama did not start. Open the Ollama app and retry.' }

Write-Output 'Downloading qwen3:4b (approximately 2.5 GB), with no paid API account...'
& $ollamaExecutable pull 'qwen3:4b'
if ($LASTEXITCODE -ne 0) { throw 'The local model download failed. Retry the setup to resume it.' }
Write-Output 'Local AI is installed. Your CV has not been sent to an external AI provider.'
