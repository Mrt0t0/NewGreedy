param([switch]$Update, [switch]$Uninstall, [switch]$KeepScrape)

$Version  = "v1.7.7"
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
python -m pip install -q --break-system-packages -r requirements.txt 2>$null; python -m pip install -q -r requirements.txt 2>$null
Log "Dependencies installed."

New-Item -ItemType Directory -Force -Path "$Dest\static" | Out-Null
New-Item -ItemType Directory -Force -Path "$Dest\torrents" | Out-Null

$preserve = @("stats.json", "torrent_registry.json", "control.json", "purge_pending.json")
$backup   = @{}
$oldCfg   = $null
if ($Update) {
  foreach ($f in $preserve) {
    $p = Join-Path $Dest $f
    if (Test-Path $p) { $backup[$f] = Get-Content -Raw -Encoding UTF8 $p }
  }
  $cfgPath = Join-Path $Dest "config.ini"
  if (Test-Path $cfgPath) {
    $oldCfg = Join-Path $env:TEMP "newgreedy-config-previous.ini"
    Copy-Item -Force $cfgPath $oldCfg
  }
}

$programFiles = @("newgreedy.py", "newgreedy_addon.py", "newgreedy_web.py", "_version.py",
                  "client_profiles.py", "updater.py", "config_merge.py", "requirements.txt",
                  "README.md", "LICENSE", "install.ps1", "install.sh", "install-macos.sh",
                  "uninstall.sh", "Dockerfile", "docker-compose.yml", "config.ini")
foreach ($f in $programFiles) {
  if (Test-Path $f) { Copy-Item -Force $f (Join-Path $Dest $f) }
}
Copy-Item -Force ".\static\*" (Join-Path $Dest "static")

if ($Update) {
  foreach ($f in $backup.Keys) {
    Set-Content -Path (Join-Path $Dest $f) -Value $backup[$f] -Encoding UTF8 -NoNewline
  }
  if ($oldCfg) {
    Log "Merging config.ini - your settings into the $Version template..."
    $mergeArgs = @("$Dest\config_merge.py", "--shipped", "$Dest\config.ini",
                   "--current", $oldCfg, "--out", "$Dest\config.ini")
    if ($KeepScrape) { $mergeArgs += "--keep-scrape" }
    python @mergeArgs
    if ($LASTEXITCODE -eq 0) {
      Log "config.ini merged."
    } else {
      Copy-Item -Force $oldCfg "$Dest\config.ini"
      Log "WARNING: merge failed - your previous config.ini was kept untouched."
    }
    Remove-Item -Force $oldCfg -ErrorAction SilentlyContinue
  }
  Log "Stats, registry, control file and torrents\ preserved."
}
Log "Files copied to $Dest"

if (-not $Update) {
  Log "Trusting mitmproxy CA certificate..."
  python -c "from mitmproxy.certs import CertStore; CertStore.from_store(r'$env:USERPROFILE\.mitmproxy', 'mitmproxy')" 2>$null
  $caPath = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.p12"
  if (Test-Path $caPath) {
    certutil -addstore -user Root $caPath 2>$null
    Log "CA certificate trusted in user store."
  } else {
    Log "WARNING: CA cert not found - trust manually via mitmproxy docs."
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
Log "  Watch  : $Dest\torrents  (drop .torrent files here)"
Log "  Proxy  : 127.0.0.1:3456"
Log "  Web UI : http://localhost:8080"
Write-Host ""
Write-Host "Open Web UI : http://localhost:8080" -ForegroundColor Cyan
