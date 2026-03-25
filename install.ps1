# ============================================================
#  NewGreedy v1.3 -- install.ps1  (Windows)
#  Run in PowerShell as Administrator:
#    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#    .\install.ps1
# ============================================================
param(
    [switch]$Update,
    [string]$InstallDir = "$env:LOCALAPPDATA\NewGreedy",
    [string]$Port       = "3456"
)

$ErrorActionPreference = "Stop"
$RepoUrl  = "https://github.com/Mrt0t0/NewGreedy.git"
$TaskName = "NewGreedy"

function Write-Info  { param($m) Write-Host "[INFO]  $m" -ForegroundColor Green }
function Write-Warn  { param($m) Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Write-Err   { param($m) Write-Host "[ERROR] $m" -ForegroundColor Red }
function Write-Title { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }

# -- Python check ---------------------------------------------
function Assert-Python {
    Write-Title "Checking Python"
    try {
        $ver = python --version 2>&1
        Write-Info "Found: $ver"
    } catch {
        Write-Err "Python not found. Download from https://python.org (3.9+)"
        exit 1
    }
}

# -- Git check ------------------------------------------------
function Assert-Git {
    Write-Title "Checking Git"
    try {
        $ver = git --version 2>&1
        Write-Info "Found: $ver"
    } catch {
        Write-Warn "Git not found. Download from https://git-scm.com"
        Write-Warn "Alternatively, download the ZIP from GitHub and extract manually."
        exit 1
    }
}

# -- Install Python dependencies -------------------------------
function Install-PythonDeps {
    Write-Title "Installing Python dependencies"
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet mitmproxy requests
    Write-Info "mitmproxy and requests installed."
}

# -- Clone or update repo --------------------------------------
function Install-Files {
    Write-Title "Installing NewGreedy files"
    if (Test-Path "$InstallDir\.git") {
        Write-Info "Existing installation found -- pulling latest..."
        git -C $InstallDir pull --ff-only
    } elseif (Test-Path $InstallDir) {
        Write-Warn "$InstallDir exists but is not a git repo -- copying files."
        Copy-Item -Force newgreedy.py       "$InstallDir\"
        Copy-Item -Force newgreedy_addon.py "$InstallDir\"
    } else {
        Write-Info "Cloning from $RepoUrl..."
        git clone --depth 1 $RepoUrl $InstallDir
    }

    # Preserve config.ini
    if (-not (Test-Path "$InstallDir\config.ini")) {
        Copy-Item "$InstallDir\config.ini" "$InstallDir\config.ini" -ErrorAction SilentlyContinue
        Write-Info "config.ini created."
    } else {
        Write-Info "config.ini already exists -- not overwritten."
    }
    Write-Info "Files installed to $InstallDir"
}

# -- Generate mitmproxy CA -------------------------------------
function Install-CA {
    Write-Title "Generating mitmproxy CA certificate"
    $caPath = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.p12"
    $caPem  = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"

    if (-not (Test-Path $caPem)) {
        Write-Info "Running mitmdump briefly to generate CA..."
        $proc = Start-Process -FilePath "mitmdump" -ArgumentList "--quiet" `
                    -PassThru -WindowStyle Hidden
        Start-Sleep 4
        $proc | Stop-Process -Force -ErrorAction SilentlyContinue
    }

    if (Test-Path $caPem) {
        Write-Info "Installing CA into Windows certificate store..."
        # Convert PEM to CER for certutil
        $cerPath = "$env:TEMP\mitmproxy-newgreedy.cer"
        Copy-Item $caPem $cerPath
        certutil -addstore -f "ROOT" $cerPath | Out-Null
        Write-Info "CA installed into Trusted Root Certification Authorities."
    } else {
        Write-Warn "CA not generated. Run 'mitmdump' manually once, then re-run this script."
    }
}

# -- Create scheduled task -------------------------------------
function Register-Task {
    Write-Title "Creating Windows Scheduled Task"

    $action  = New-ScheduledTaskAction `
                   -Execute "python" `
                   -Argument "`"$InstallDir\newgreedy.py`"" `
                   -WorkingDirectory $InstallDir

    $trigger = New-ScheduledTaskTrigger -AtLogon

    $settings = New-ScheduledTaskSettingsSet `
                    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
                    -RestartCount 3 `
                    -RestartInterval (New-TimeSpan -Minutes 1)

    $principal = New-ScheduledTaskPrincipal `
                     -UserId "$env:USERDOMAIN\$env:USERNAME" `
                     -LogonType Interactive `
                     -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action   $action `
        -Trigger  $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep 2

    $state = (Get-ScheduledTask -TaskName $TaskName).State
    if ($state -eq "Running") {
        Write-Info "Task '$TaskName' started (port $Port)."
    } else {
        Write-Warn "Task state: $state -- check Task Scheduler for details."
    }
}

# -- Update only -----------------------------------------------
function Update-NewGreedy {
    Write-Title "Updating NewGreedy"

    if (-not (Test-Path "$InstallDir\.git")) {
        Write-Err "$InstallDir is not a git repository. Run install first."
        exit 1
    }

    Write-Info "Pulling latest from GitHub..."
    git -C $InstallDir fetch origin
    git -C $InstallDir pull --ff-only

    Write-Info "Upgrading Python dependencies..."
    python -m pip install --quiet --upgrade mitmproxy requests

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask  -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep 2
        Start-ScheduledTask -TaskName $TaskName
        Write-Info "Task '$TaskName' restarted."
    } else {
        Write-Warn "Scheduled task not found -- restart NewGreedy manually."
    }

    Write-Info "Update complete."
}

# -- Print summary ---------------------------------------------
function Print-Summary {
    Write-Host ""
    Write-Host "+----------------------------------------------------------+" -ForegroundColor Green
    Write-Host "|     NewGreedy v1.3 -- Installation complete (Windows)    |" -ForegroundColor Green
    Write-Host "+----------------------------------------------------------+" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Proxy port (HTTP + HTTPS) : 127.0.0.1:$Port"
    Write-Host "  Install directory         : $InstallDir"
    Write-Host "  Config file               : $InstallDir\config.ini"
    Write-Host "  Log file                  : $InstallDir\newgreedy.log"
    Write-Host ""
    Write-Host "Configure qBittorrent:" -ForegroundColor Cyan
    Write-Host "  Settings -> Connection -> Proxy"
    Write-Host "    Type : HTTP"
    Write-Host "    Host : 127.0.0.1"
    Write-Host "    Port : $Port"
    Write-Host "    [x] Use proxy for tracker communication"
    Write-Host ""
    Write-Host "Useful commands:" -ForegroundColor Cyan
    Write-Host "  View logs   : Get-Content $InstallDir\newgreedy.log -Wait"
    Write-Host "  Update      : .\install.ps1 -Update"
    Write-Host "  Stop task   : Stop-ScheduledTask -TaskName $TaskName"
    Write-Host "  Start task  : Start-ScheduledTask -TaskName $TaskName"
    Write-Host ""
}

# -- Entry point -----------------------------------------------
if ($Update) {
    Update-NewGreedy
    exit 0
}

Assert-Python
Assert-Git
Install-PythonDeps
Install-Files
Install-CA
Register-Task
Print-Summary
