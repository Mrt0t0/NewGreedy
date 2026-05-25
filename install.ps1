# NewGreedy v1.6.0 — Automated installer for Windows
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Update

param([switch]$Update)
$ErrorActionPreference = "Stop"
$VERSION = "1.6.0"
$Dest    = "$env:LOCALAPPDATA\NewGreedy"
$CertDir = "$env:USERPROFILE\.mitmproxy"
$CertP12 = "$CertDir\mitmproxy-ca-cert.p12"
$CertPem = "$CertDir\mitmproxy-ca-cert.pem"

function OK   { param($m) Write-Host "[OK] $m"   -ForegroundColor Green  }
function INFO { param($m) Write-Host "[->] $m"   -ForegroundColor Cyan   }
function WARN { param($m) Write-Host "[!]  $m"   -ForegroundColor Yellow }
function FAIL { param($m) Write-Host "[X]  $m"   -ForegroundColor Red; exit 1 }

Write-Host "NewGreedy v$VERSION — Windows Installer" -ForegroundColor White
Write-Host "──────────────────────────────────────────"

# ── 1. Check Python 3.9+ ─────────────────────────────────────────────────
INFO "Checking Python..."
$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(sys.version_info[:2])" 2>$null
        $ok  = & $cmd -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $PythonCmd = $cmd; break }
    } catch {}
}

if (-not $PythonCmd) {
    WARN "Python 3.9+ not found — attempting automatic install via winget..."
    try {
        winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $PythonCmd = "python"
        OK "Python installed via winget"
    } catch {
        FAIL "Could not auto-install Python.`nPlease install manually from https://python.org then re-run this script.`nIMPORTANT: check 'Add Python to PATH' during install."
    }
}
OK "Python found: $(& $PythonCmd --version)"

# ── 2. Upgrade pip ────────────────────────────────────────────────────────
INFO "Upgrading pip..."
& $PythonCmd -m pip install --upgrade pip -q
OK "pip upgraded"

# ── 3. Install Python dependencies ───────────────────────────────────────
INFO "Installing Python packages (mitmproxy, fastapi, uvicorn)..."
& $PythonCmd -m pip install -q -r requirements.txt
OK "Python packages installed"

# ── 4. Copy files ─────────────────────────────────────────────────────────
if (-not (Test-Path $Dest) -or $Update) {
    INFO "Copying files to $Dest..."
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    Copy-Item -Recurse -Force ".\*" $Dest
    OK "Files copied to $Dest"
}

# ── 5. Generate mitmproxy CA certificate ─────────────────────────────────
INFO "Generating mitmproxy CA certificate..."
if (-not (Test-Path $CertDir)) { New-Item -ItemType Directory -Force -Path $CertDir | Out-Null }

if (-not (Test-Path $CertP12)) {
    # Generate cert by running mitmdump briefly
    $job = Start-Job {
        param($py, $port)
        & $py -m mitmproxy.tools.main mitmdump --listen-port $port 2>$null
    } -ArgumentList $PythonCmd, 19090
    Start-Sleep -Seconds 3
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
}

if (Test-Path $CertP12) {
    OK "CA certificate generated: $CertP12"
} else {
    WARN "Certificate not yet generated."
    WARN "Run 'python newgreedy.py' once manually, then re-run: .\install.ps1 -Update"
}

# ── 6. Trust CA certificate in Windows store ─────────────────────────────
if (Test-Path $CertP12) {
    INFO "Installing CA certificate in Windows Trusted Root store..."
    try {
        $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2
        $cert.Import($CertP12)
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
            [System.Security.Cryptography.X509Certificates.StoreName]::Root,
            [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine
        )
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        $store.Add($cert)
        $store.Close()
        OK "Certificate trusted in Windows Trusted Root store"
    } catch {
        WARN "Could not auto-trust certificate: $_"
        WARN "Manual steps: double-click $CertP12 → Local Machine → Trusted Root Certification Authorities"
    }
} elseif (Test-Path $CertPem) {
    INFO "Importing PEM certificate via certutil..."
    try {
        certutil -addstore -f "ROOT" $CertPem | Out-Null
        OK "Certificate trusted via certutil"
    } catch {
        WARN "certutil failed — import $CertPem manually."
    }
}

# ── 7. Register Windows Scheduled Task (auto-start at logon) ─────────────
if (-not $Update) {
    INFO "Registering Windows Scheduled Task (start at logon)..."
    try {
        $taskName = "NewGreedy"
        $action   = New-ScheduledTaskAction `
            -Execute $PythonCmd `
            -Argument "`"$Dest\newgreedy.py`"" `
            -WorkingDirectory $Dest
        $trigger  = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $principal= New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force | Out-Null
        OK "Scheduled Task '$taskName' registered (starts at logon)"
    } catch {
        WARN "Could not register Scheduled Task: $_"
    }
} else {
    INFO "Restarting NewGreedy..."
    try {
        Stop-Process -Name "python" -ErrorAction SilentlyContinue
        Start-Process -FilePath $PythonCmd -ArgumentList "`"$Dest\newgreedy.py`"" -WorkingDirectory $Dest -WindowStyle Hidden
        OK "NewGreedy restarted"
    } catch {
        WARN "Could not restart automatically — start manually."
    }
}

# ── 8. Start NewGreedy now ────────────────────────────────────────────────
INFO "Starting NewGreedy..."
try {
    Start-Process -FilePath $PythonCmd `
        -ArgumentList "`"$Dest\newgreedy.py`"" `
        -WorkingDirectory $Dest `
        -WindowStyle Hidden
    OK "NewGreedy started in background"
} catch {
    WARN "Could not start automatically — run manually: python `"$Dest\newgreedy.py`""
}

# ── 9. Summary ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "──────────────────────────────────────────"
Write-Host "NewGreedy v$VERSION installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Proxy:   127.0.0.1:3456"   -ForegroundColor Cyan
Write-Host "  Web UI:  http://localhost:8080" -ForegroundColor Cyan
Write-Host "  Config:  $Dest\config.ini" -ForegroundColor Cyan
Write-Host "  Logs:    $Dest\newgreedy.log" -ForegroundColor Cyan
Write-Host ""
Write-Host "[!] Don't forget: disable UDP trackers in your torrent client." -ForegroundColor Yellow
Write-Host "[!] Set HTTP proxy to 127.0.0.1:3456 in your client settings." -ForegroundColor Yellow
Write-Host "──────────────────────────────────────────"
