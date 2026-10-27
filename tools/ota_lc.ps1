Set-Location C:\Users\Ruanyouyi\LightController
pio run -e ota -t upload *> C:\Users\Ruanyouyi\Rpi4\room-presence\ota_lc.log
"EXIT $LASTEXITCODE" | Add-Content C:\Users\Ruanyouyi\Rpi4\room-presence\ota_lc.log
