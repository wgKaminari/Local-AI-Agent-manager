<#
.SYNOPSIS
    Install or update Personal Job Agent on Windows.

.DESCRIPTION
    First install (PowerShell):
        irm https://raw.githubusercontent.com/wgKaminari/Local-AI-Agent-manager/main/install.ps1 | iex

    The app is installed to %LOCALAPPDATA%\PersonalJobAgent (app + a private
    Python environment) with Start menu and desktop shortcuts. Your profile,
    vacancies and drafts stay in %USERPROFILE%\.norway-job-agent and are never
    changed by installing or updating.

    The app itself runs this script to update: it waits for the app to close,
    installs the new version next to the old one, switches over, and reopens
    the app. If anything fails, the previous version is kept and reopened.

    A developer checkout (a git clone) is updated with git pull instead.
#>
[CmdletBinding()]
param(
    # Release tag to install, such as v0.7.1. Default: the latest release.
    [string]$Tag = '',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'PersonalJobAgent'),
    # Process to wait for before replacing files (the running app).
    [int]$WaitPid = 0,
    # Update this git checkout with git pull instead of installing a release.
    [string]$Checkout = '',
    # Python that runs the checkout (git mode).
    [string]$Python = '',
    # Install from a local source zip instead of downloading (tests, offline).
    [string]$SourceZip = '',
    [switch]$NoShortcuts,
    [switch]$NoLaunch,
    # No questions and no pause at the end (automated runs).
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Much faster downloads in Windows PowerShell 5.1.
$Repo = 'wgKaminari/Local-AI-Agent-manager'
$UserAgent = 'PersonalJobAgent-installer'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Note([string]$Message) { Write-Host "    $Message" }

function Ask([string]$Question) {
    if ($Quiet) { return $false }
    $answer = Read-Host "$Question [y/N]"
    return $answer -match '^(y|yes|j|ja)$'
}

function Wait-ForApp {
    if ($WaitPid -le 0) { return }
    Step 'Waiting for Personal Job Agent to close'
    $process = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
    if ($process -and -not $process.WaitForExit(60000)) {
        throw 'The app did not close within a minute. Close it and run the update again.'
    }
    Start-Sleep -Milliseconds 500  # Let Windows release the app's files.
}

function Get-PythonVersion([string[]]$Command) {
    try {
        $exe = $Command[0]
        $rest = @()
        if ($Command.Length -gt 1) { $rest = $Command[1..($Command.Length - 1)] }
        $output = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) { return [version]($output | Select-Object -First 1) }
    } catch { }
    return $null
}

function Find-Python {
    foreach ($candidate in @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3'), @('python'), @('python3'))) {
        $version = Get-PythonVersion $candidate
        if ($version -and $version -ge [version]'3.12') { return ,$candidate }
    }
    return $null
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Require-Python {
    $python = Find-Python
    if ($python) { return ,$python }
    Write-Host 'Python 3.12 or newer is required and was not found.' -ForegroundColor Yellow
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask 'Install Python 3.12 for your user now with winget?')) {
        Step 'Installing Python 3.12 (winget)'
        winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements | Out-Host
        Refresh-Path
        $python = Find-Python
        if ($python) { return ,$python }
    }
    throw 'Install Python 3.12 or newer from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH"), then run this installer again.'
}

function Get-LatestTag {
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = $UserAgent }
        if ($release.tag_name) { return $release.tag_name }
    } catch { }
    return ''
}

function Get-Source([string]$Destination) {
    # Returns the extracted source folder and the version label.
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $zip = Join-Path $Destination 'source.zip'
    if ($SourceZip) {
        Copy-Item -LiteralPath $SourceZip -Destination $zip
        $label = if ($Tag) { $Tag } else { 'local' }
    } else {
        $label = if ($Tag) { $Tag } else { Get-LatestTag }
        $url = if ($label) { "https://github.com/$Repo/archive/refs/tags/$label.zip" } else { "https://github.com/$Repo/archive/refs/heads/main.zip" }
        if (-not $label) { $label = 'main' }
        Step "Downloading Personal Job Agent $label"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip -Headers @{ 'User-Agent' = $UserAgent }
    }
    $extracted = Join-Path $Destination 'extracted'
    try {
        Expand-Archive -LiteralPath $zip -DestinationPath $extracted -Force
    } catch {
        throw 'The download is damaged or incomplete. Check your internet connection and try again.'
    }
    $root = Get-ChildItem -LiteralPath $extracted -Directory | Select-Object -First 1
    if (-not $root -or -not (Test-Path -LiteralPath (Join-Path $root.FullName 'run.py'))) {
        throw 'The downloaded archive does not contain Personal Job Agent.'
    }
    return @($root.FullName, $label)
}

function Install-Release {
    $app = Join-Path $InstallRoot 'app'
    $venv = Join-Path $InstallRoot 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    $staging = Join-Path $InstallRoot ('staging-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    try {
        $source, $label = Get-Source $staging
        if (-not (Test-Path -LiteralPath $venvPython)) {
            $python = Require-Python
            Step 'Creating a private Python environment'
            $exe = $python[0]
            $rest = @()
            if ($python.Length -gt 1) { $rest = $python[1..($python.Length - 1)] }
            & $exe @rest -m venv $venv | Out-Host
            if ($LASTEXITCODE -ne 0) { throw 'Python could not create the app environment.' }
        }
        Step 'Installing dependencies'
        & $venvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $source 'requirements.txt') | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Installing the dependencies failed. Check your internet connection and try again.' }
        Wait-ForApp
        Step 'Switching to the new version'
        $old = Join-Path $InstallRoot 'app.old'
        if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
        if (Test-Path -LiteralPath $app) { Rename-Item -LiteralPath $app -NewName 'app.old' }
        try {
            Move-Item -LiteralPath $source -Destination $app
        } catch {
            if (Test-Path -LiteralPath $old) { Rename-Item -LiteralPath $old -NewName 'app' }
            throw
        }
        if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }
        $record = [ordered]@{ tag = $label; installed_at = (Get-Date).ToString('o'); repository = $Repo }
        $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot 'install.json') -Encoding UTF8
        if (-not $NoShortcuts) {
            Step 'Adding Start menu and desktop shortcuts'
            & $venvPython (Join-Path $app 'run.py') shortcuts | Out-Null
            if ($LASTEXITCODE -ne 0) { Note 'Shortcuts could not be created; open the app with the command below.' }
        }
        return @($app, (Join-Path $venv 'Scripts\pythonw.exe'), $label)
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Update-Checkout {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'git is not installed, so this checkout cannot be updated automatically.' }
    $pythonExe = if ($Python) { $Python } else { (Find-Python)[0] }
    Wait-ForApp
    Step "Updating $Checkout with git pull"
    git -C $Checkout pull --ff-only | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'git pull failed. Open the project folder and resolve it with git.' }
    Step 'Installing dependencies'
    & $pythonExe -m pip install --disable-pip-version-check --quiet -r (Join-Path $Checkout 'requirements.txt') | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Installing the dependencies failed. Check your internet connection and try again.' }
    $windowed = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $windowed)) { $windowed = $pythonExe }
    return @($Checkout, $windowed, 'latest')
}

function Start-App([string]$App, [string]$PythonW) {
    if ($NoLaunch) { return }
    Step 'Opening Personal Job Agent'
    Start-Process -FilePath $PythonW -ArgumentList @(('"' + (Join-Path $App 'run.py') + '"'), 'gui') -WorkingDirectory $App
}

function Offer-Ollama {
    if ($Quiet -or $WaitPid -gt 0 -or (Get-Command ollama -ErrorAction SilentlyContinue) -or (Test-Path (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'))) { return }
    Write-Host ''
    Write-Host 'Cover letters and answers are written by a free local AI (Ollama), which is not installed.' -ForegroundColor Yellow
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask 'Install Ollama now with winget (about 1 GB)?')) {
        winget install --exact --id Ollama.Ollama --accept-package-agreements --accept-source-agreements | Out-Host
    } else {
        Note 'You can install it later from https://ollama.com/download/windows or from the app''s Settings.'
    }
}

$failed = $false
try {
    if ($Checkout) {
        $app, $pythonw, $label = Update-Checkout
    } else {
        $app, $pythonw, $label = Install-Release
    }
    Offer-Ollama
    Start-App $app $pythonw
    Write-Host ''
    Write-Host "Personal Job Agent $label is ready." -ForegroundColor Green
    Note "Open it from the Start menu or desktop, or run: `"$pythonw`" `"$(Join-Path $app 'run.py')`" gui"
} catch {
    $failed = $true
    Write-Host ''
    Write-Host "Install or update failed: $($_.Exception.Message)" -ForegroundColor Red
    # An update never leaves you without the app: reopen the version you had.
    $previous = if ($Checkout) { $Checkout } else { Join-Path $InstallRoot 'app' }
    $previousPython = if ($Checkout -and $Python) { Join-Path (Split-Path -Parent $Python) 'pythonw.exe' } else { Join-Path $InstallRoot 'venv\Scripts\pythonw.exe' }
    if ($WaitPid -gt 0 -and (Test-Path -LiteralPath (Join-Path $previous 'run.py')) -and (Test-Path -LiteralPath $previousPython)) {
        Note 'Reopening the version you had.'
        Start-App $previous $previousPython
    }
}
# Run through "irm ... | iex" the script shares your PowerShell window:
# never exit it. Started as a file (by the app's updater), close by itself
# after a success and wait for Enter after a failure, so the message is read.
if ($PSCommandPath) {
    if ($failed -and -not $Quiet) { Read-Host 'Press Enter to close this window' | Out-Null }
    elseif (-not $Quiet) { Start-Sleep -Seconds 3 }
    if ($failed) { exit 1 }
}
