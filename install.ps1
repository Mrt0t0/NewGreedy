param([switch]$Update, [switch]$Uninstall, [switch]$TrustCA, [switch]$KeepDefaults, [switch]$KeepScrape)

Set-Location $PSScriptRoot
$Version  = ((Select-String -Path "_version.py" -Pattern '^VERSION\s*=\s*"(.*)"').Matches[0].Groups[1].Value)
$Dest     = "$env:LOCALAPPDATA\NewGreedy"
$TaskName = "NewGreedy"
$LogFile  = "$Dest\install.log"
$CaDir    = "$env:USERPROFILE\.mitmproxy"
$CaPem    = "$CaDir\mitmproxy-ca-cert.pem"

function Log($msg) {
  Write-Host "[NewGreedy $Version] $msg"
  Add-Content -Path $LogFile -Value "$(Get-Date -f 'HH:mm:ss') $msg" -ErrorAction SilentlyContinue
}

function CaThumbprint {
  if (-not (Test-Path $CaPem)) { return $null }
  try { return (New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CaPem)).Thumbprint }
  catch { return $null }
}

if ($Uninstall) {
  Log "Uninstalling..."
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  # the CA must not stay trusted once its program is gone - this CA only, by its thumbprint:
  # deleting by name would also remove the certificate of any other mitmproxy setup
  $thumb = CaThumbprint
  if ($thumb) {
    certutil -delstore -user Root $thumb 2>$null | Out-Null
    Log "mitmproxy CA $thumb removed from the user Root store."
  }
  if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
  Log "Uninstalled."
  exit 0
}

Log "Starting install (update=$Update)..."

$pyVer = ""
if (Get-Command python -ErrorAction SilentlyContinue) { $pyVer = (& python --version 2>&1) | Out-String }
if ($pyVer -match 'Python 3\.') {
  Log "Python found: $($pyVer.Trim())"
} else {
  Log "Python not found. Downloading installer..."
  $pyInstaller = "$env:TEMP\python-installer.exe"
  Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $pyInstaller
  # run it only when Windows confirms it is the installer signed by the PSF
  $sig = Get-AuthenticodeSignature $pyInstaller
  if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
    Remove-Item -Force $pyInstaller -ErrorAction SilentlyContinue
    Log "ERROR: the downloaded Python installer is not signed by the Python Software Foundation ($($sig.Status)) - aborting."
    exit 1
  }
  Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1" -Wait
  Remove-Item -Force $pyInstaller -ErrorAction SilentlyContinue
  # the installer edits PATH for new sessions only
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
  Log "Python installed."
}
$PyExe = (Get-Command python).Source

Log "Installing Python dependencies..."
& $PyExe -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Log "ERROR: pip install failed."; exit 1 }
Log "Dependencies installed."

New-Item -ItemType Directory -Force -Path "$Dest\static" | Out-Null
New-Item -ItemType Directory -Force -Path "$Dest\torrents" | Out-Null

# an installed config.ini is always merged, never replaced; data files are never copied
$oldCfg = $null
$cfgPath = Join-Path $Dest "config.ini"
if (Test-Path $cfgPath) {
  $oldCfg = Join-Path $env:TEMP "newgreedy-config-previous.ini"
  Copy-Item -Force $cfgPath $oldCfg
}

$programFiles = Get-Content "MANIFEST.txt" | Where-Object { $_.Trim() -and -not $_.TrimStart().StartsWith("#") }
foreach ($f in $programFiles) {
  $f = $f.Trim()
  if (Test-Path $f) { Copy-Item -Force $f (Join-Path $Dest $f) }
}
Copy-Item -Force "config.ini" $cfgPath
Copy-Item -Force ".\static\*" (Join-Path $Dest "static")

if ($oldCfg) {
  Log "Merging config.ini - your settings into the $Version template..."
  $mergeArgs = @("$Dest\config_merge.py", "--shipped", $cfgPath, "--current", $oldCfg, "--out", $cfgPath)
  if ($KeepDefaults -or $KeepScrape) { $mergeArgs += "--keep-defaults" }
  & $PyExe @mergeArgs
  if ($LASTEXITCODE -eq 0) {
    Log "config.ini merged."
  } else {
    Copy-Item -Force $oldCfg $cfgPath
    Log "WARNING: merge failed - your previous config.ini was kept untouched."
  }
  Remove-Item -Force $oldCfg -ErrorAction SilentlyContinue
  Log "Stats, registry, control file and torrents\ preserved."
}
Log "Files copied to $Dest"

if (-not (Test-Path $CaPem)) {
  Log "Generating mitmproxy CA..."
  & $PyExe -c "from mitmproxy.certs import CertStore; CertStore.from_store(r'$CaDir', 'mitmproxy', 2048)" 2>$null
}
if ($TrustCA -and (Test-Path "$CaDir\mitmproxy-ca-cert.p12")) {
  certutil -addstore -user Root "$CaDir\mitmproxy-ca-cert.p12" 2>$null | Out-Null
  Log "CA certificate trusted in the user store (thumbprint $(CaThumbprint))."
} elseif (Test-Path $CaPem) {
  Log "CA NOT added to the user Root store (pass -TrustCA to do so)."
  Log "  HTTPS trackers need the torrent client to trust $CaPem"
}

# a normal user task: the proxy needs no administrator rights
$action   = New-ScheduledTaskAction -Execute $PyExe -Argument "`"$Dest\newgreedy.py`"" -WorkingDirectory $Dest
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Register-ScheduledTask -Action $action -Trigger $trigger -Settings $settings `
  -TaskName $TaskName -Description "NewGreedy $Version" -RunLevel Limited -Force | Out-Null
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $TaskName
Log "Scheduled task registered (runs at logon, without administrator rights) and started."

Log "Installation complete."
Log "  Proxy  : 127.0.0.1:3456"
Log "  Web UI : http://localhost:8080"
Log "  Watch  : $Dest\torrents  (drop .torrent files here)"
Log "  Check  : `"$PyExe`" `"$Dest\newgreedy.py`" --check-config"
Write-Host ""
Write-Host "Open Web UI : http://localhost:8080" -ForegroundColor Cyan
