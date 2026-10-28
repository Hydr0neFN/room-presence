@echo off
cd /d "C:\esp-work"
py -3.13 -m esphome upload neopixel.yaml --device 10.0.0.100 > C:\Users\Ruanyouyi\Rpi4\room-presence\esp_np_up.log 2>&1
echo EXIT %ERRORLEVEL% >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_np_up.log
