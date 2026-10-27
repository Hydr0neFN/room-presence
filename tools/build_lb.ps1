$env:PLATFORMIO_CORE_DIR='C:\pio-lb'
Set-Location C:\Users\Ruanyouyi\Rpi4\lightbar2mqtt
pio run -e esp32-c3-pro-mini-ota *> C:\Users\Ruanyouyi\Rpi4\room-presence\build_lb.log
"EXIT $LASTEXITCODE" | Add-Content C:\Users\Ruanyouyi\Rpi4\room-presence\build_lb.log
