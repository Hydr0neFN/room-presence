# Registers the PresencePcIdle scheduled task (runs pc_idle.py in the user's session).
# Triggers: at logon, plus every 5 min as a self-heal (MultipleInstances IgnoreNew keeps one copy).
# The broker password is read by pc_idle.py from the gitignored mqtt-pass.local next to it.
$ErrorActionPreference = 'Stop'
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe'
$script = Join-Path $PSScriptRoot 'pc_idle.py'
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`""
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$heal = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit 0 `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName PresencePcIdle -Action $action -Trigger @($logon, $heal) `
    -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask PresencePcIdle
# Remove: Unregister-ScheduledTask PresencePcIdle -Confirm:$false
