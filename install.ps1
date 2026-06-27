param([switch]$Update, [switch]$Uninstall)

$Version  = "v1.7.5"
$Dest     = "$env:LOCALAPPDATA\NewGreedy"
$TaskName = "NewGreedy"
$LogFile  = "$Dest\install.log"

function Log($msg) {
  Write-Host "[NewGreedy $Version] $msg"
  Add-Content -Path $LogFile -Value "$(Get-Date -f 'HH:mm:ss') $msg" -ErrorAction SilentlyContinue
}

if ($Uninstall) {
  Log "Uninstalling..."
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
  Log "Uninstalled."
  exit 0
}

Log "Starting install (update=$Update)..."

try {
  $pyVer = python --version 2>&1
  Log "Python found: $pyVer"
} catch {
  Log "Python not found. Downloading installer..."
  $pyInstaller = "$env:TEMP\python-installer.exe"
  Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $pyInstaller
  Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1" -Wait
  Log "Python installed."
}

Log "Installing Python dependencies..."
python -m pip install -q -r requirements.txt
Log "Dependencies installed."

New-Item -ItemType Directory -Force -Path "$Dest\static" | Out-Null
Copy-Item -Recurse -Force ".\*" $Dest
Log "Files copied to $Dest"

if (-not $Update) {
  Log "Trusting mitmproxy CA certificate..."
  python -c "from mitmproxy.certs import CertStore; CertStore.from_store(r'$env:USERPROFILE\.mitmproxy', 'mitmproxy')" 2>$null
  $caPath = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.p12"
  if (Test-Path $caPath) {
    certutil -addstore -user Root $caPath 2>$null
    Log "CA certificate trusted in user store."
  } else {
    Log "WARNING: CA cert not found — trust manually via mitmproxy docs."
  }
}

if (-not $Update) {
  $action   = New-ScheduledTaskAction -Execute "python" -Argument "`"$Dest\newgreedy.py`""
  $trigger  = New-ScheduledTaskTrigger -AtLogOn
  $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -Action $action -Trigger $trigger -Settings $settings `
    -TaskName $TaskName -Description "NewGreedy $Version" -RunLevel Highest -Force | Out-Null
  Log "Scheduled task registered (runs at logon)."
  Start-ScheduledTask -TaskName $TaskName
  Log "NewGreedy started."
} else {
  Stop-ScheduledTask  -TaskName $TaskName -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  Start-ScheduledTask -TaskName $TaskName
  Log "NewGreedy restarted."
}

Log "Installation complete."
Log "  Proxy  : 127.0.0.1:3456"
Log "  Web UI : http://localhost:8080"
Write-Host ""
Write-Host "Open Web UI : http://localhost:8080" -ForegroundColor Cyan
