param([string]$log, [string]$target)
$env:PLATFORMIO_CORE_DIR='C:\pio-cyd'
Set-Location C:\cyd-work
if ($target) { pio run -e cyd_ota -t $target *> $log } else { pio run -e cyd_ota *> $log }
"EXIT $LASTEXITCODE" | Add-Content $log
