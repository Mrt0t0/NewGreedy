# install.ps1 — NewGreedy v1.4 Windows installer
# Usage: .\install.ps1

param([switch]$Update)

$ErrorActionPreference = "Stop"
$InstallDir = "$env:USERPROFILE\NewGreedy"

Write-Host "NewGreedy v1.4 Windows Installer" -ForegroundColor Cyan

# Check Python 3.9+
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python -or ($python.Version -lt "3.9")) {
    Write-Host "❌ Python 3.9+ requis — https://python.org/downloads" -ForegroundColor Red
    exit 1
}

if ($Update) {
    Set-Location $InstallDir
    git pull origin main
    Write-Host "✅ GitHub → Mis à jour" -ForegroundColor Green
} else {
    # Clone
    if (Test-Path $InstallDir) {
        Write-Host "📁 $InstallDir existe déjà" -ForegroundColor Yellow
        Set-Location $InstallDir
        git pull origin main
    } else {
        git clone https://github.com/Mrt0t0/NewGreedy.git $InstallDir
        Set-Location $InstallDir
    }
}

# Dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Auto‑start Task Scheduler (nécessite admin)
if (([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    $TaskName = "NewGreedy"
    $Action = New-ScheduledTaskAction -Execute "python" -Argument "$InstallDir\newgreedy.py"
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Force
    Write-Host "✅ Auto‑start configuré (Task Scheduler)" -ForegroundColor Green
}

Write-Host "`n🚀 Démarrer : python newgreedy.py" -ForegroundColor Green
Write-Host "📊 Logs : tail -f newgreedy.log (ou Get-Content newgreedy.log -Tail 20 -Wait)" -ForegroundColor Cyan
Write-Host "`nAdmin requis pour auto‑start. Relance en tant qu'admin si besoin." -ForegroundColor Yellow
