param([string]$log)
Set-Location C:\Users\Ruanyouyi\Documents\PlatformIO\Projects\NLDeskCYD
pio run -e cyd_ota *> $log
"EXIT $LASTEXITCODE" | Add-Content $log
