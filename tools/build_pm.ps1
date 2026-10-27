param([string]$log, [string]$envName='c3')
$env:PLATFORMIO_CORE_DIR='C:\pio-pm'
Set-Location C:\Users\Ruanyouyi\powermon
pio run -e $envName *> $log
"EXIT $LASTEXITCODE" | Add-Content $log
