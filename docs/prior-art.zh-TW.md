[English](prior-art.md) · **繁體中文**

# 既有方案

## 看過的既有方案（2026-10-28 抓的頁面）
- ESPresense（https://espresense.com/ ）：以指紋辨識 ID、Apple 裝置的 IRK enrollment、距離上的 median 預濾波加 1 Euro filter。IRK 我們已經在用；RSSI 上的 median／1 Euro filter 還沒抄進來。
- Bermuda BLE Trilateration（https://github.com/agittins/bermuda ）：在 ESPHome proxy 上做的 HA 整合。
- Espressif Wi-Fi API guide（https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32c3/api-guides/wifi.html ）：modem-sleep 模式。
- room-assistant 和 ESPHome 的 bluetooth_proxy 頁面沒有針對共存設定去挖（proxy 頁面上找不到相關內容）。
