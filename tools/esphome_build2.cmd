@echo off
cd /d C:\esp-work
py -3.13 -m esphome compile kitchenradar.yaml > C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr.log 2>&1
echo EXIT %ERRORLEVEL% >> C:\Users\Ruanyouyi\Rpi4\room-presence\esp_kr.log
