[English](operations.md) · **繁體中文**

# 維運：狀態、回滾、bring-up

## 目前狀態（2026-10-27 ~14:50）
所有韌體映像檔：`firmware/`（含密鑰，已 gitignore）；回復映像檔：`rollback/`。從這台 PC 做 OTA 會失敗（防火牆：裝置連不回來）- 一律從 `<server>` 做 OTA（`ssh <server>`，把 bin 和 `tools/espota_slow.py` 複製到 /tmp）。建置：lightbar `PLATFORMIO_CORE_DIR=C:\pio-lb`，CYD `C:\pio-cyd` 加暫存副本 `C:\cyd-work`，powermon `C:\pio-pm`，ESPHome 從 `C:\esp-work`（`py -3.13 -m esphome`）；在 Git Bash 裡要這樣叫 PowerShell：`env -u MSYSTEM powershell ...`。
| 裝置（IP） | 韌體／分支／commit | 發佈主題 | 回滾 | OTA 路徑 |
|---|---|---|---|---|
| `<server>`（`<server-ip>`） | `nlpi/` 服務 `presence-ble`（這個 repo） | `presence/nlpi/apple` 3 秒 | `systemctl disable --now presence-ble`；broker passwd 備份 | n/a |
| lightbar（`<lightbar-ip>`）節點 `pcdesk` | lightbar2mqtt，分支 `ble-presence`，6cd8f71（BLE + CSI）加 `ble_presence.cpp` 裡的 init 失敗／brownout guard（工作目錄未 commit，在 lightbar2mqtt 裡），14:36 燒錄（`firmware/lightbar2mqtt-ble-csi-guard.bin`） | apple 3 秒、csi 約 2 Hz（heap 136 kB） | `rollback/lightbar2mqtt-ble-csi-6cd8f71-20261027.bin`（沒有 guard 的 BLE + CSI，12:30-14:36 跑的就是這份）、`lightbar2mqtt-ble-pre-csi.bin`（只有 BLE）、`lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin`（沒有 presence） | `<server>` `espota_slow.py -p 3232 --auth=<OTA_PASSWORD>` |
| LightController（`<workdesk-ip>`）節點 `workdesk` | 屋主的工作目錄 + `lightcontroller-ble-presence.patch` + `lc-port/lightcontroller-csi-hooks.diff`，未 commit，patch 已用含 init 失敗／brownout guard 的版本重做（`ble_presence.cpp` 這段 594 行），14:39 燒錄（`firmware/lightcontroller-ble-csi-guard.bin`）；之後換成 watchdog 版 `lightcontroller-ble-wd.bin`（2026-10-28）；現在（2026-11-03 10:23 OTA）跑的是 `firmware/lightcontroller-ble-wd-minn4.bin`，內容相同，只多了 LightController `platformio.ini` 裡的 `-D CSI_MIN_N=4`（csi 要 n >= 4 frames/s 才算 `ok`，原本是 8；其他板子維持預設 8）；OTA 重開後板子沒回來，屋主約 14:46 斷電重上，原因不明 | apple、csi（heap 約 52 kB） | `rollback/lightcontroller-pre-minn-20261103.bin`（加 CSI_MIN_N 之前在跑的 wd 版，sha256 f251ccdf...）、`rollback/lightcontroller-ble-csi-20261027.bin`（沒有 guard，12:33-14:39 在跑）、`lightcontroller-ble-pre-csi.bin`、`lightcontroller-prepatch-2026-10-27.bin` | `<server>` 原版 `espota.py`（HomeSpan PBKDF2 認證；espota_slow 會失敗） |
| NLDeskCYD（`<cyd-ip>`）節點 `cyd` | NLDeskCYD repo，分支 `ble-presence`，de56799，13:06 燒錄（`firmware/nldeskcyd-ble-csi-v3.bin`） | apple 3 秒、csi 約 2 Hz（heap 閒置 62 kB，最低 52 kB；沒有 BLE 時是 140 kB） | `rollback/nldeskcyd-HEAD1a663a7.bin`（main，沒有 presence） | `<server>` `espota_slow.py`（MD5 認證） |
| powermon C3（`<kitchen-ip>`）節點 `kitchen` | powermon repo，分支 `c3-port`，fe961bc，13:09 燒錄（`firmware/powermon-c3-csi.bin`） | apple、csi、wifi_rssi 1 Hz、power/powermon/state+diag | `rollback/powermon-c3-pre-csi-9faaafc.bin` | `<server>` 原版 `espota.py -P 3233 -p 3232 --auth=<data/config.json otaPassword>` |
| kitchenradar（ESPHome） | wifi_signal 感測器 1 秒 | `sensor.chu_fang_kitchenradar_wifi_signal` | `rollback/kitchenradar-esphome-prev-20261027.bin` | 從 `C:\esp-work` 跑 `esphome upload`（從 PC 跑得通） |
| neopixel（ESPHome） | wifi_signal 60 秒 -> 1 秒 | `sensor.neopixel_neopixel_wifi_signal` | `rollback/neopixel-esphome-prev-20260903.bin` | 同上 |
| HA 套件 | `ha/room_presence.yaml`，13:13 部署（節點 pcdesk、workdesk、nlpi、kitchen、cyd） | `sensor.yui_room_zone` | `<server>` 上的 `packages/room_presence.yaml.bak-20261027c`（加 cyd 之前）、`.bak-20261027b`、`.bak-2026-10-27` | scp + `check_config` + 一次 `docker restart homeassistant` |

自動化：已安裝，總開關 `input_boolean.room_presence_automations` 是 OFF。IRK：手機加手錶已於 2026-10-27 載入（用跑 ESPHome irk-capture 的 ESP32-C6 抓的，retained 在 `presence/config/irk`）；校正區域時就用它。
CYD 的代價，屋主看到的：LVGL 繪圖緩衝從 64 行降到 24 行（省 25 kB）。其他都沒砍：動畫、字型、圖片、頁面一個都沒少。閒置的星空動畫不變（12 fps）；星空目標 30 fps 時實測約 27 fps；BLE／WiFi 共存逼著 WiFi modem sleep 用 MIN_MODEM（觸控指令可能慢幾毫秒）。風險：配置失敗時 LVGL 會凍住（靠 5 秒 watchdog 重開）；開一個頁面要約 15-25 kB，可用是 52-62 kB。每一頁都點過一次，並看 `cyd/nl/panel/perf`（`hmin`、`hblk`）。
備註：lightbar 的 POWERON 重置（09:59、12:02）來自 PC USB 供電，跟 BLE／CSI 無關（沒有 panic／WDT，heap 平穩）。powermon：放在桌上的這塊板子每秒會數到約 0.9 個假脈波（約 324 W，`total_kwh` 會往上跑），因為它不在電表上；ADC 取樣在跑 CSI 時正常（每 10 秒 1980 個樣本，最大間隔 23-30 ms，有沒有 CSI 都一樣）；CSI 可以用 `presence/<node>/cmd` 上的 `csi_off` 暫停（`csi_on` 恢復）。

## 2026-10-28 更新（與上表不同時以此為準）
- BLE 節點改跑 watchdog 版（crash reset 後延後啟動掃描器，含 brownout；掃描 watchdog、定時鎖、每 30 秒發 `presence/<node>/ble` 狀態，內含 state／reset reason／重開次數／uptime）：pcdesk `firmware/lightbar2mqtt-ble-wd-wdt.bin`（lightbar2mqtt 分支 `ble-presence`，loopTask task-WDT 120 秒，由 `loop()` 和 OTA 進度餵；commit 在 a4bb316 之後），cyd `nldeskcyd-ble-wd.bin`（NLDeskCYD 5de58fb），workdesk `lightcontroller-ble-wd.bin`（patch `lightcontroller-ble-presence.patch`，LightController 工作目錄未 commit），kitchen `powermon-c3-wd.bin`（powermon e664699）。回滾映像檔：`lightbar2mqtt-ble-wd.bin`（pcdesk 有 watchdog 但沒有 loop-WDT）、`lightbar2mqtt-ble-csi-guard.bin`、`lightcontroller-ble-csi-guard.bin`、`nldeskcyd-ble-csi-v3.bin`、`powermon-c3-csi.bin`。OTA 路徑不變（只能從 `<server>`）。
- 10-27 17:13 的 pcdesk 重置是板子自己重置（brownout／USB 供電），發佈沒有卡住；舊的 guard 之後讓 BLE 整個開機週期都關著。現在改成延後啟動。
- kitchenradar + neopixel（ESPHome）：`power_save_mode: none`（ping RTT 沒變：`ping_before_ps.txt`、`ping_after_ps.txt`）。`<server>` 沒有 WLAN（只有 eth0）：不用改。
- HA（`ha/room_presence.yaml`；備份在 `<server>` 上的 `packages/room_presence.yaml.bak-20261028`、`b`、`c`、`d`）：節點健康檢查（過期的節點會被比對器跳過）、廚房要有證據、夜間床的規則、防跳動的 hold（PC hold 300 秒、45 秒 dwell）。廚房證據只看雷達（`rp_kitchen_radar_hint`）；powermon >= 1000 W 和廚房燈數，只有在雷達前 2 分鐘內看到人時才算（`rp_kitchen_radar_recent`），因為 13:28-13:32 屋主坐在 PC 前卻被顯示成 kitchen（1800 W + 燈）。重跑 `analysis/a10_sim_fix.py`（`analysis/out_a10_kitchen_radar_only.txt`）：kitchen 列 603 -> 486，117 列改判 home，對 weak label 的分區準確率不變。
- 儀表板視圖 `定位除錯`（在 `定位校準` 旁邊，來源 `ha/dashboard_calibrate_view.yaml`；備份 `dashboards/house.yaml.bak-20261028c`）：區域判斷原因（`sensor.rp_zone_reason`）、每個節點的手機 RSSI 加過期旗標加 CSI 計數、各區域的指紋距離（`sensor.rp_fp_debug`，屬性 `dist`）、證據旗標（雷達／最近有雷達／電力／廚房燈／夜間床／在家）以及 PC 狀態（online／locked／idle）。
- PC 閒置發佈程式：`pc-idle/install_task.ps1` 註冊 `PresencePcIdle` 排程工作（登入時啟動，每 5 分鐘自我修復）；它之前掛掉了。
- Wi-Fi 睡眠／共存（屋主指示：Wi-Fi 優先）：用 `WIFI_PS_MIN_MODEM` 時 station 每個 DTIM 醒一次，`listen_interval` 只對 `WIFI_PS_MAX_MODEM` 有效（Espressif Wi-Fi API guide v4.4.7），所以 listen interval 1 不會改變什麼，也就沒套用。`esp_coex_preference_set(ESP_COEX_PREFER_WIFI)` 在 IDF 4.4 有，但沒有套用：證據顯示不需要它（`tools/q_gaps.py`，6 小時：pcdesk／kitchen／cyd 的 CSI 間隔 > 10 秒占 0.3-0.7 %，全是我 OTA 和 HA 重開造成的；各節點 RSSI 平均 -40／-61／-46 dBm）。如果開始有節點掉線，先對 `presence/<node>/cmd` 下 `off`（關 BLE，不用重燒）當 BLE-off 基準，再試 `WIFI_PS_NONE`。
- workdesk（LightController，RSSI 平均 -73.6 dBm，最弱的一條 link；從 `<server>` ping 平均 25 ms／最大 66 ms，kitchenradar 只要 8 ms）：斷線時段 10-27 21:53-22:14、22:49-23:09，10-28 02:40-04:01 UTC（本地 +1 小時）、07:17-07:30，還有更短的；`light.channel_b`（同一塊板上的 HomeKit 燈）在同樣的時段也不可用，ESPHome 的 kitchenradar／neopixel 從來沒斷，其他 presence 節點也正常。所以是整塊板子離開網路，不只 presence 資料流；這個問題在 presence 韌體之前就有（屋主）。目前還分不出是重開機還是 Wi-Fi 掉線：新加的感測器 `sensor.rp_<node>_uptime`（來自 `/ble` 狀態）會在下一次斷線時給答案（uptime 重置 = 重開機，沒重置 = 關聯掉線）。`tools/q_wd.py` 會列出斷線時段。如果確認是關聯掉線，候選修法：workdesk 的天線／位置、TP-Link AX10 的 2.4 GHz 頻道（先匯出備份，一次只改一項）。
  - 2026-11-02 23:25 改天線（31 mm 線、15 mm 環，環朝上）：HA 可用率 87 % -> ~100 %，RSSI ~-81 dBm（原廠天線常常根本連不上，有一次離線 8 h）。
  - 只有這塊板子調低 CSI `ok` 門檻：`CSI_MIN_N=4`（`csi/csi_presence.cpp` 預設 8）；在 -81 dBm 下 n 大多是 0-7（改之前的 120 s：n>=4 佔 29 %，n>=8 佔 0 %）。
- 只用 CSI 的分區準確率（`tools/fp_eval.py cv analysis/data/export.csv --cv visits --purge-s 15`，85 筆人工標註、4 個區域、leave-one-visit-out）：只用 BLE 0.82；CSI 權重加到 w = 6 dB 時 0.48（只用 motion 特徵 0.62）；以目前的特徵，單靠 CSI 明顯比 BLE 差。Roadmap「CSI-first」：在每個區域重錄 CSI 特徵，人分別站著不動和走動，用節點自己的空房基準做正規化（ratio 特徵在廚房表現較好），權重交給 CV 決定而不是手調，之後才把 `rp_feat_weight_db` 從 0 往上加。
- 既有方案：見[既有方案](prior-art.zh-TW.md)。

## 回滾
- Lightbar：OTA 回滾映像檔（指令在[韌體與節點](firmware.zh-TW.md)第 2 節）。LightController：`git apply -R` 那個 patch（同一份文件的第 6 節）再重燒；它原本的韌體是屋主自己建的版本。
- `<server>`：`systemctl disable --now presence-ble`；broker 使用者：還原 `/etc/mosquitto/passwd.bak-blepresence`。
- HA：`/opt/homeassistant/config/packages/room_presence.yaml.bak-2026-10-27` 是今晚改動前的套件；要整個移除這個功能，就刪掉套件檔、還原 `configuration.yaml.bak-2026-10-27`，重開 container。
- `input_boolean.room_presence_automations` 關著時，自動化不會動作。

## 屋主檢查清單（照順序）
1. IRK（選用但建議）：照[Home Assistant 與資料紀錄](home-assistant.zh-TW.md)的「取得 IRK」做，把 retained 的 `presence/config/irk` 發出去；確認 `sensor.rp_pcdesk_phone_loaded` = 1。沒有的話全部跑在較弱的備援模式。
2. 校正（手機放平常的口袋，每個區域約 1 分鐘，房裡不要有別人走動）：`py -3.13 calibrate.py --zone kitchen`、`--zone work_desk`、`--zone pc_desk`（坐在 PC 前，手不要放桌上）、`--zone bed`。廚房：等 powermon 板子裝到電表／廚房那邊，不然就跳過（雷達提示還是能涵蓋）。`--show --feat-note` 會印出存下來的手機 BLE 讀值和 CSI／link 特徵；`--clear <zone>` 刪掉其中一個。link 特徵需要能從這台 PC `ssh <server>`。
3. 走一走，看 `sensor.yui_room_zone`、`sensor.rp_fp_zone`、`binary_sensor.yui_home`；調 `input_number.rp_fp_hysteresis`（BLE 這邊）和 `rp_feat_weight_db`（0 = 只用 BLE，越大越信任 CSI／link）。
4. CYD 每一頁都點過一次（上面說的 LVGL heap 檢查）。
5. 打開 `input_boolean.room_presence_automations`（只在暗的時候，00:30-07:30 之外）。

## 已定決策（安全預設）
- 不燒錄、不在 `<server>` 上 apt install、HA 只重開一次（check_config 之後），備份 `*.bak-2026-10-27`。
- lightbar 的工具鏈鎖在 Arduino 2.0.17，並用獨立的 core 目錄（不然映像檔會超過 OTA 分割區）。
- 自動化裝好但預設停用。
- `pc_active`（PC 解鎖、閒置 < 60 秒）會蓋掉 away，也會讓 away 條件不成立：很明顯有人坐在 PC 前。
- 人在家但什麼都沒收到時，回報的區域是 `home`（以前是 `away_or_elsewhere`）。
- 床邊錨點的節點名稱維持 `nlpi`。
- 跳過：NLDeskCYD、ESP8266 嗅探、host 端編譯的 C++ 測試（這台機器上沒有編譯器）。

## 8. 實機 bring-up 紀錄（2026-10-27 白天，屋主同意燒錄）
- `<server>`：`install_nlpi.sh` 跑完沒問題（08:36）；`presence-ble` active，`presence/nlpi/apple` 每 3 秒發一次。回滾方式不變。
- lightbar（`<lightbar-ip>`）：`pio run -e esp32-c3-pro-mini-ota -t upload` 08:38 OK。看了 5 分鐘：heap 144,4xx 穩定、`mqtt_connects` 1、WiFi rssi -37..-44，用 `lightbar2mqtt/<device-topic>/command` 下 `on`／`off` 有反應，燈條已還原成 OFF，HA 裡 `sensor.rp_pcdesk_*` 有值。約 09:52 它重開，reset_reason 是 POWERON，原因是斷電重上，並非 crash；BLE 自己回來了。
- LightController（`<workdesk-ip>`）：先燒回滾映像檔 `rollback\lightcontroller-prepatch-2026-10-27.bin`（用屋主 patch 前的工作目錄加 `ENABLE_MQTT 0` 重建，跟實際跑的不是位元相同）。patch 用 `git apply` 套上（沒有 commit／stash），`src/config.h`（已 gitignore）改成 `ENABLE_MQTT 1`、`MQTT_USER "lcpresence"`；新的 mosquitto 使用者 `lcpresence`（密碼只放在已 gitignore 的 config.h 和 `lc-port\lcpresence.pass.local`；broker 的 `passwd` 備份為 `/etc/mosquitto/passwd.bak-lcpresence`）。`critic` 審過（沒有阻擋性問題）。`pio run -e ota -t upload` 08:45 OK。看了 400 秒：`presence/workdesk/apple` heap 60.5 kB、`lightctrl/online` 出現一次（沒有重連），後來 HA 的 `fan.wo_shi_fans` 和 `light.channel_b` 都有值（HomeKit 連線正常）。透過 HA 的可控性測試做不了（沒有 HA API token），只能靠可用性和燒錄後 HA 狀態有變（屋主使用）來判斷。約 10:03 它重開（uptime），BLE 回來；heap 54-59 kB，掃描器還在它 40 kB 的停止門檻之上。回滾：用 HomeSpan OTA（同一條 espota 路徑）燒 prepatch bin，或 `git apply -R`。
- NLDeskCYD（`<cyd-ip>`）：第一次先回滾（heap 140 -> 61 kB）；13:06 用比較小的 LVGL 繪圖緩衝重做，見目前狀態。發現：把 NimBLE／BT 鏈結進去會讓 Arduino 開機時釋放 BT 控制器記憶體的動作不再發生，所以開機後 free heap 從 140 kB 掉到 61 kB，掃描器都還沒開始（掃描器低於 70 kB 就拒絕啟動），這樣 LVGL（配置失敗 = UI 凍住）就有風險。面板已回到 HEAD 1a663a7 的版本（`rollback\nldeskcyd-HEAD1a663a7.bin`），heap 140 kB，MQTT 資料正常更新。屋主的 NLDeskCYD repo 沒有動。之後可能：拿掉頁面／星空緩衝，或換板子。
- 從這台 PC 對 CYD 做 OTA 會失敗（"No response from device"：裝置連不回來，PC 防火牆只允許某些來源的 python）；從 `<server>` 可以：把 `tools\espota_slow.py` 和 bin 複製到 `<server>`，跑 `python3 espota_slow.py -i <cyd-ip> -p 3232 --auth=<secrets.ini ota_password> -f x.bin`。另外：預設 PlatformIO 的 `espressif32` 平台被 pioarduino 的版本蓋掉了（LightController 造成），所以 NLDeskCYD 在預設 core 下建不起來；要用 `PLATFORMIO_CORE_DIR=C:\pio-cyd`，並在暫存副本（`C:\cyd-work`，腳本在 `tools\`）寫 `platform = espressif32@7.1.0`。
- 廚房錨點：評估過，沒建。 (1) NeoPixel D1 mini 用 GPIO4 bit-bang 173 顆 WS2812B（每 frame 中斷關約 5 ms），而 ESP8266 SDK 文件寫 promiscuous 模式跟已連線的 station 不相容，所以嗅探會冒險影響 HA 連線和主要燈光功能。 (2) 睡著的 iPhone 上行 frame 很稀疏，大部分樣本會是 -100，毀掉指紋。 (3) 雷達已經給了廚房提示。建議改成：廚房放一顆 3 歐元的 ESP32-C3，跑 lightbar 韌體（節點名稱 `kitchen`；加進 HA 套件再重新校正）。以後若要走 Wi-Fi 方案：可能是 iPhone 的裝置是 `<person>`，位於 `<phone-ip>`，private MAC `<phone-mac>`（mDNS `_companion-link`，未確認是不是屋主的手機）。KitchenLight、powermon、basil、FW13 沒動。
- HA 套件沒有新增東西（沒有新節點），今天也沒重開 HA。
- 屋主下一步：載入 IRK、校正（`calibrate.py`），然後打開 `input_boolean.room_presence_automations`。
