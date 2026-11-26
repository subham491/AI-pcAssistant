<#
.SYNOPSIS
  One-script setup for the voice PC co-worker on Windows 10/11.

.DESCRIPTION
  Safe to re-run: every step checks before it changes anything.
    1. Installs Python 3.12 and Git with winget if they are missing (user scope, no admin).
    2. Creates .venv in this folder and installs the app.
    3. Adds NVIDIA CUDA libraries for faster speech-to-text if an NVIDIA GPU is present.
    4. Creates your home folder (default: %USERPROFILE%\assistant) without overwriting anything.
    5. Downloads the wake word and speech models (one time, a few hundred MB).
    6. Adds a Start Menu shortcut (and a login shortcut with -Startup).
    7. Signs you in to Claude if needed, then runs the health check.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup.ps1
  powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Startup -AssistantHome D:\assistant
#>
[CmdletBinding()]
param(
    [string]$AssistantHome = (Join-Path $env:USERPROFILE "assistant"),
    [switch]$Startup,      # also start the assistant when you sign in to Windows
    [switch]$CpuOnly,      # skip the NVIDIA CUDA libraries even if a GPU is present
    [switch]$SkipModels,   # skip model downloads (they then download on first use)
    [switch]$SkipLogin
)

$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
$Venv = Join-Path $Repo ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$PyW = Join-Path $Venv "Scripts\pythonw.exe"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

function Find-Python {
    # Prefer the py launcher with 3.12, then any python 3.11+ on PATH.
    # Windows PowerShell 5.1 turns native stderr into a terminating error under "Stop",
    # even with 2>$null, so probing a version that is not installed would abort setup.
    $ErrorActionPreference = "Continue"
    if (Have "py") {
        foreach ($v in @("3.12", "3.11", "3.13")) {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        }
    }
    if (Have "python") {
        $ok = & python -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
        if ($ok -eq "True") { return (& python -c "import sys; print(sys.executable)").Trim() }
    }
    return $null
}

if ($env:OS -ne "Windows_NT") { throw "This setup is for Windows." }

# ---------------------------------------------------------------- 1. Python + Git
Step "Checking Python and Git"
$BasePy = Find-Python
if (-not $BasePy) {
    if (-not (Have "winget")) { throw "Python 3.12 is missing and winget is not available. Install Python 3.12 from python.org, then re-run." }
    Warn "Installing Python 3.12 (user scope)..."
    Invoke-Checked "winget" @("install", "-e", "--id", "Python.Python.3.12", "--scope", "user", "--accept-package-agreements", "--accept-source-agreements")
    Refresh-Path
    $BasePy = Find-Python
    if (-not $BasePy) { throw "Python installed but not found. Open a new PowerShell window and re-run setup." }
}
Ok "Python: $BasePy"

if (-not (Have "git")) {
    if (-not (Have "winget")) { throw "Git is missing and winget is not available. Install Git for Windows, then re-run." }
    Warn "Installing Git for Windows (Claude Code uses Git Bash for its shell tool)..."
    Invoke-Checked "winget" @("install", "-e", "--id", "Git.Git", "--accept-package-agreements", "--accept-source-agreements")
    Refresh-Path
}
if (Have "git") { Ok "Git: $((git --version) -join '')" } else { Warn "Git not on PATH yet; open a new window after setup." }

# ---------------------------------------------------------------- 2. venv + app
Step "Installing the app into $Venv"
if (-not (Test-Path $Py)) { Invoke-Checked $BasePy @("-m", "venv", $Venv) }
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "-e", ($Repo + "[app]"))
Ok "Installed."

# ---------------------------------------------------------------- 3. GPU
Step "Checking for an NVIDIA GPU"
if (-not $CpuOnly -and (Have "nvidia-smi")) {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null) -join "; "
    Ok "Found: $gpu"
    Warn "Installing CUDA libraries for faster-whisper (about 1 GB)..."
    Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*")
} else {
    Ok "No NVIDIA GPU (or -CpuOnly): speech-to-text runs on the CPU. Set stt_model = ""small.en"" if it feels slow."
}

# ---------------------------------------------------------------- 4. home folder
Step "Home folder: $AssistantHome"
Invoke-Checked $Py @("-m", "assistant", "init", "--home", $AssistantHome)
$defaultHome = Join-Path $env:USERPROFILE "assistant"
if ($AssistantHome -ne $defaultHome) {
    [Environment]::SetEnvironmentVariable("PC_ASSISTANT_HOME", $AssistantHome, "User")
    $env:PC_ASSISTANT_HOME = $AssistantHome
    Ok "Set PC_ASSISTANT_HOME for your user."
}

# ---------------------------------------------------------------- 5. models
if (-not $SkipModels) {
    Step "Downloading models (one time)"
    Invoke-Checked $Py @("-c", "import openwakeword.utils as u; u.download_models(['hey_jarvis']); print('    wake word model ready')")
    Warn "Downloading the speech model (about 1.6 GB, one time)..."
    Invoke-Checked $Py @("-c", "from faster_whisper.utils import download_model; from assistant.config import load_config; from assistant.voice.stt import resolve_model; m = resolve_model(load_config().voice.stt_model); download_model(m); print('    speech model ready: ' + m)")
}

# ---------------------------------------------------------------- 6. shortcuts
Step "Creating shortcuts"
$shell = New-Object -ComObject WScript.Shell
function New-Shortcut($path) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $PyW
    $lnk.Arguments = "-m assistant run"
    $lnk.WorkingDirectory = $AssistantHome
    $lnk.Description = "Voice PC co-worker"
    $lnk.Save()
}
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "PC Assistant.lnk"
New-Shortcut $startMenu
Ok "Start Menu: PC Assistant"
$startupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "PC Assistant.lnk"
if ($Startup) {
    New-Shortcut $startupLnk
    Ok "Starts when you sign in (remove: delete $startupLnk)"
}

# ---------------------------------------------------------------- 7. sign in + check
if (-not $SkipLogin) {
    $creds = Join-Path $env:USERPROFILE ".claude\.credentials.json"
    if (-not (Test-Path $creds)) {
        Step "Sign in to Claude"
        Write-Host "    Claude Code opens next. Type /login, finish in the browser, then type /exit."
        $answer = Read-Host "    Sign in now? [Y/n]"
        if ($answer -notmatch "^[nN]") { & $Py -m assistant login --home $AssistantHome }
    } else {
        Ok "Already signed in to Claude."
    }
}

Step "Health check"
& $Py -m assistant doctor --home $AssistantHome

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
Write-Host "  First test (no mic, no GUI):  $Py -m assistant text"
Write-Host "  Full app:                     Start Menu > PC Assistant   (or: $Py -m assistant run)"
Write-Host "  Tip: in VS Code turn on File > Auto Save so the assistant always sees your latest code."
