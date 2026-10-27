$env:PLATFORMIO_CORE_DIR='C:\pio-lb'
$env:LIGHTBAR_OTA_PASSWORD=[Environment]::GetEnvironmentVariable('LIGHTBAR_OTA_PASSWORD','User')
Set-Location C:\Users\Ruanyouyi\Rpi4\lightbar2mqtt
pio run -e esp32-c3-pro-mini-ota -t upload --upload-port 10.0.0.87 *> C:\Users\Ruanyouyi\Rpi4\room-presence\ota_lb.log
"EXIT $LASTEXITCODE" | Add-Content C:\Users\Ruanyouyi\Rpi4\room-presence\ota_lb.log
