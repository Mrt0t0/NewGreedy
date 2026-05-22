param([switch]$Update)
$Dest = "$env:LOCALAPPDATA\NewGreedy"
Write-Host "[NewGreedy] Installing v1.5.1..."
pip install -q -r requirements.txt
if (-not (Test-Path $Dest) -or $Update) {
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    Copy-Item -Recurse -Force . $Dest
    Write-Host "[NewGreedy] Files copied to $Dest"
}
if (-not $Update) {
    $action  = New-ScheduledTaskAction -Execute "python" -Argument "$Dest\newgreedy.py"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -Action $action -Trigger $trigger -TaskName "NewGreedy" -RunLevel Highest -Force
}
Write-Host "[NewGreedy] Done."
