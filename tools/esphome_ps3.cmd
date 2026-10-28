@echo off
cd /d "C:\esp-work"
py -3.13 -m esphome compile kitchenradar.yaml > C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr3.log 2>&1
echo EXIT %ERRORLEVEL% >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr3.log
py -3.13 -m esphome compile neopixel.yaml > C:\Users\Ruanyouyi\Rpi4\room-presence\esp_np3.log 2>&1
echo EXIT %ERRORLEVEL% >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_np3.log
