@echo off
cd /d "C:\Users\Ruanyouyi\OneDrive - Hanze\ESPHome"
py -3.13 -m esphome compile kitchenradar.yaml.bak-20261027 > C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr_old.log 2>&1
echo EXIT %ERRORLEVEL% >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr_old.log
copy /Y ".esphome\build\kitchenradar\.pioenvs\kitchenradar\firmware.bin" C:\Users\Ruanyouyi\Rpi4\room-presence\rollback\kitchenradar-esphome-prev-20261027.bin >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr_old.log 2>&1
py -3.13 -m esphome compile kitchenradar.yaml > C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr.log 2>&1
echo EXIT %ERRORLEVEL% >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr.log
py -3.13 -m esphome compile neopixel.yaml > C:\Users\Ruanyouyi\Rpi4\room-presence\esp_np.log 2>&1
echo EXIT %ERRORLEVEL% >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_np.log
