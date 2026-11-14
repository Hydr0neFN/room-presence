[English](firmware.md) · **繁體中文**

# 韌體與節點

## 1. KitchenRadar
實際存在的實體（已確認）：binary_sensor.kitchenradar_presence、sensor.kitchenradar_{detection,moving,still}_distance、_moving_energy、_still_energy。只當廚房提示用：kitchen = presence 開啟且 detection_distance < `input_number.rp_kitchen_max_cm`（250）。校正方式：分別站在廚房／工作台／PC 桌前，看距離感測器。雷達看不看得到書桌區，目前不確定。

## 2. BLE 韌體（lightbar2mqtt，PC 桌錨點）- 已燒錄（BLE 08:38，+ CSI 12:30）
- Repo `<user-home>\Rpi4\lightbar2mqtt`，分支 `ble-presence`（main 沒動）。`ble_presence.h/.cpp`，在 `Lightbar.ino`、`mqtt.cpp`（inbound 緩衝加到 512）、`platformio.ini` 裡各加一點 hook。
- 被動掃描（interval 100 ms／window 30 ms，約 30 % duty）；Apple Continuity 廣告（type 0x10/0x0C/0x0F）；載入 IRK 後，私密位址用 AES `ah()` 解析（兩種 byte order 都試）。每 3 秒發一次（不 retained）到 `presence/pcdesk/apple`：
  `{"scan":1,"apple_best":-61,"apple_avg":-66,"apple_n":9,"mov_best":-70,"mov_avg":-72,"mov_n":2,"static_n":3,"irk_n":2,"irk":[{"id":"phone","best":-58,"avg":-60,"n":4},{"id":"watch","best":-127,"avg":-127,"n":0}],"heap":98000,"up":55}`（-127／n=0 = 沒收到）。`apple_*` = 所有 Apple 廣告，`mov_*` = 只算沒被判成靜止的廣告，`irk[]` = 標記過的屋主裝置（解析出來的裝置不列入 `apple` 的過濾，也不列入 `mov_*`）。
- 控制：對 `presence/pcdesk/cmd` 下 `on`／`off`。IRK：retained `presence/config/irk`（上面），不用重燒。節點名稱 = `BLE_NODE_NAME`（預設 `pcdesk`）。
- 靜止過濾：對每個位址取 30 秒窗的 RSSI 平均；平均值在 6 dB 內維持至少 5 分鐘、8 個窗，這個位址就算靜止。平均 RSSI 會以「band」記住 3 小時，所以同一台裝置下一個輪替位址（約每 15 分鐘換一次）只要在 3 dB 內，從第四個廣告起就被抑制。之後裝置移動（平均值差超過 6 dB）就解除。每個節點追蹤 48 個位址。同樣的演算法在 `nlpi\stationary.py`（7 個單元測試，在 `nlpi\` 下跑 `py -3.13 -I -m unittest`）。限制：備援模式下，手機放著不動 5 分鐘以上會被當成靜止（IRK 可以解決）；重開後過濾器要 5 分鐘才穩；沒做：「PC 鎖定時持續視為在場」。
- 平台鎖版：現在的 pioarduino 平台（Arduino 3.3.9）基準就 1.196 MB，BLE 已經塞不進 1.25 MB 的 OTA 分割區（1.375 MB）。`platformio.ini` 鎖 `espressif32@6.9.0`（Arduino 2.0.17），PlatformIO core 目錄獨立成 `C:\pio-lb`。從 PowerShell／cmd 建置（不要用 Git Bash）：`$env:PLATFORMIO_CORE_DIR='C:\pio-lb'; pio run -e esp32-c3-pro-mini-ota`。
- review 時發現的取捨：WiFi 的 modem sleep 一關，WiFi／BT 共存就會 abort，所以這個版本用 `WIFI_PS_MIN_MODEM`，不用 `setSleep(false)`；這條分支上等於把之前修 ping jitter／MQTT 重連的改動撤掉了。燒錄後注意 MQTT 重連次數；如果明顯變多，對 `presence/pcdesk/cmd` 下 `off` 或回滾。
- 保險：OTA 比 BLE 先初始化；panic／WDT 重置後的下一次開機跳過 BLE；free heap 低於 40 kB 時掃描器停，回到 50 kB 以上才重啟。
- 映像檔（已 gitignore，含密鑰）：`<user-home>\Rpi4\room-presence\firmware\lightbar2mqtt-ble-presence.bin`（sha256 如上）。回滾 `...\rollback\lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin`（907,968 B，sha256 146e0cca...）。注意：燈條現在跑的是九月 6 日建的版本；回滾 bin 用同一份原始碼重建，重建出來的位元跟現在跑的不一樣。
- OTA（PowerShell；環境變數 `LIGHTBAR_OTA_PASSWORD` = config.h 裡的 OTA_PASSWORD；防火牆規則 TCP 3233 見 platformio.ini 註解）：
  ```
  cd <user-home>\Rpi4\lightbar2mqtt
  $env:PLATFORMIO_CORE_DIR='C:\pio-lb'
  pio run -e esp32-c3-pro-mini-ota -t upload --upload-port <lightbar-ip>
  ```
  回滾：`py -3.13 C:\pio-lb\packages\framework-arduinoespressif32\tools\espota.py -i <lightbar-ip> -p 3232 --auth=%LIGHTBAR_OTA_PASSWORD% -f <user-home>\Rpi4\room-presence\rollback\lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin`（USB：`pio run -e esp32-c3-pro-mini -t upload`）。
- 沒在實機跑過。風險：單核 C3 上同時跑 BLE + WiFi；NimBLE 2.5.2 配 Arduino 2.0.17 編得過但從沒實際執行；parser／tracker 的 C++ 沒有 host 測試（這台沒有 host 編譯器），只有 code review 和跟已測過的 Python 參考實作對照。

## 3. PC 閒置發佈程式（已上線）
`pc-idle\pc_idle.py` -> `presence/pcdesk/idle` `{"idle":s,"locked":0|1}` 每 10 秒，`presence/pcdesk/status` online/offline（retained、LWT）。專用的 mosquitto 使用者 `pcpresence`（密碼只放在已 gitignore 的 `pc-idle/mqtt-pass.local`；`<server>` 的 `/etc/mosquitto/passwd` 備份為 `passwd.bak-2026-10-27`；broker 沒有 ACL）。工作 `PresencePcIdle`（安裝或修復：`powershell -ExecutionPolicy Bypass -File pc-idle/install_task.ps1`；登入時觸發，另外每 5 分鐘自我修復，IgnoreNew 保證只留一份；2026-10-28 加的，因為這個工作在 10-27 掛掉，pc_desk 就偵測不到）。停止：`Stop-ScheduledTask PresencePcIdle`；移除：`Unregister-ScheduledTask PresencePcIdle`。

## 5. BLE 掃描器與校正
- `nlpi\nlpi_ble.py`（bleak 服務，topic `presence/nlpi/apple`，同樣的 JSON schema，IRK 有標記，靜止過濾用 `stationary.py`；注意：節點叫 `nlpi`，區域叫 `bed`）。`ah()` 已用 spec 測資驗過；IRK 標記解析離線檢查過；服務本身還沒在 Pi 上跑過。
- `calibrate.py` 依區域存中位訊號（有手機 IRK 就用 IRK 那筆，否則用非靜止備援）每個節點一份。

## 6. LightController 移植（08:45 燒錄，+ CSI 12:33；節點 `workdesk`）
- Patch：`<user-home>\Rpi4\room-presence\lightcontroller-ble-presence.patch`（unified diff，`-p1`，對屋主 2026-10-27 01:30 的工作目錄做的，含未 commit 的改動；`git apply --check` 會過；工作目錄沒有被改）。它新增 `src/ble_presence.{h,cpp}`（跟 lightbar2mqtt 的檔案一樣，用 `-D BLE_PUBLISH_FN_API` 建置）、`StatusReporter.h` 裡的發佈／訂閱／IRK hook（MQTT 緩衝 768，keepalive 60）、`main.cpp` 裡啟動 BLE（WiFi 起來後 30 秒，只走 MQTT），`platformio.ini` 裡 NimBLE-Arduino 2.3、節點名稱 `workdesk` 和被動模式的 NimBLE 旗標。它也把 `WiFi.setSleep(false)` 換成 `WIFI_PS_MIN_MODEM`（共存需要；等於撤掉 HomeSpan／MQTT 穩定度用的「不進 modem sleep」選擇，注意 HomeKit／MQTT 會不會掉線）。
- 套用：`cd <user-home>\LightController; git apply --check -p1 <user-home>\Rpi4\room-presence\lightcontroller-ble-presence.patch; git apply -p1 <user-home>\Rpi4\room-presence\lightcontroller-ble-presence.patch`（只碰到未 commit 的修改檔，另外新增兩個檔；不會 stage 或 commit）。然後在已 gitignore 的 `src/config.h` 設 `ENABLE_MQTT 1` 加上 `MQTT_USER`／`MQTT_PASS`（專用的 broker 使用者，例如 `install_nlpi.sh` 建的 `blepresence`；密碼在 `<server>` 的 `/etc/presence-ble.env`）。`ENABLE_MQTT 0` 時 BLE 不會啟動（只有 setSleep 的改動生效）。
- 建置加燒錄（PowerShell，用預設 PlatformIO core，不用鎖平台）：`pio run -e ota -t upload`（HomeSpan OTA，密碼來自 `secrets.ini`）。在暫存副本上開 MQTT 建出來的 size：flash 1,684,425 B = 1.9 MB 分割區的 85.7 %，RAM 靜態 18.4 %。要跑才知道：HomeSpan + web log + NimBLE 下的 free heap（掃描器低於 40 kB 會自己停）。看 topic `presence/workdesk/apple`（`heap` 欄位）5 分鐘、HomeKit 的反應，以及 `lightctrl/online`。
- 回滾：`git apply -R -p1` 該 patch，殘留的 `src/ble_presence.*` 刪掉，重燒；或對 `presence/workdesk/cmd` 下 `off`，不重燒就停止掃描。
- 暫存的建置目錄（`lc-port\work`，已 gitignore）放了一份 `config.h`／`secrets.ini`。

## 7. TP-Link AX10 AP 有沒有提供每個 client 的 RSSI？
沒有對裝置本身驗證過（用標題掃鄰居表找不到它在 LAN 上，也沒有登入或改任何設定）。只憑一般知識：Archer AX10 原廠 web UI 會列出關聯的無線 client（Network/Wireless Statistics：MAC、封包），我沒聽過有文件化的本機 API 能給每個 client 的 RSSI；如果它在 access point 模式，預期拿不到可用的訊號資料。主要 AP 可能是 UniFi（gateway 是 `<gateway>`）：UniFi controller 對手機確實會回報每個 client 的 `rssi`／`signal`，那會是同一道房間問題的第二個真實訊號來源，但它屬於手機連上的那台 AP，不屬於房間。當成未驗證；白天再決定值不值得看。
