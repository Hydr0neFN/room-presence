[English](home-assistant.md) · **繁體中文**

# Home Assistant 與資料紀錄

## IRK：選用但建議
沒有手機的 IRK，節點只能用「非靜止裝置裡最強的 Apple 廣告」當訊號。這很不穩：鄰居的手機、來訪的人，還有屋主自己的手機一放著不動 5 分鐘以上（此時看起來就跟 MacBook 一樣，會被濾掉）。有了 IRK，每個節點就算手機一直換私密位址，也能認出屋主的手機（還有手錶）。校正前先載入。

### 取得 IRK（手機與手錶）- 沒有在夜間執行，照 HA 文件做
1. Home Assistant 文件「Private BLE Device」（https://www.home-assistant.io/integrations/private_ble_device/ ）說明怎麼從登入同一個 iCloud 帳號的 Mac（屋主的 MacBook）讀出 iPhone／Apple Watch 的 IRK：Keychain Access -> 搜 `Bluetooth` -> 以裝置命名的項目（手機、手錶；配對過的裝置會透過 iCloud keychain 同步）-> Show password -> 存的值裡就有 IRK（base64 或 hex，看 macOS 版本；base64 用 `echo '<value>' | base64 -d | xxd -p` 轉）。
2. 兩種 byte order 都行：節點兩邊都會試，不用自己反轉。每台裝置 32 個 hex 字元；分隔符號／`0x` 會被忽略。
3. 從任何連得到 broker 的機器發一次 retained（帶標籤的 JSON；要放在同一行，lightbar 的 inbound 緩衝只有 512 byte）：
   `mosquitto_pub -r -h <server-ip> -u <user> -P <pass> -t presence/config/irk -m '{"phone":"<32hex>","watch":"<32hex>"}'`
   純 hex 字串（舊格式）也接受：第一個 key 當成手機。最多 4 個帶標籤的 key。要移除：發一個空的 retained 訊息（`-n`）。
4. 檢查：HA 裡 `sensor.rp_pcdesk_phone_loaded` = 1、`sensor.rp_pcdesk_watch_loaded` = 1；靠近手機時 `sensor.rp_<node>_irk_n` > 0。

## 4. Home Assistant（已上線）
- `/opt/homeassistant/config/packages/room_presence.yaml`（來源 `ha\room_presence.yaml`；`configuration.yaml` 裡有 `homeassistant: packages: !include_dir_named packages`，備份 `configuration.yaml.bak-2026-10-27`）。重新部署：scp 檔案，跑 `docker exec homeassistant python -m homeassistant --script check_config -c /config`，再 `docker restart homeassistant`（沒有 API token 就沒辦法重載 MQTT／template 實體；只有自動化能透過 `ha/admin/reload_automations` 重載）。
- 實體：`sensor.yui_room_zone` = kitchen | work_desk | pc_desk | bed | home | away；`binary_sensor.yui_home`；`binary_sensor.room_zone_{kitchen,work_desk,pc_desk,bed,away}`；每個節點 `sensor.rp_<pcdesk|workdesk|nlpi>_{signal,apple_best,apple_n,mov_best,mov_n,static_n,irk_avg,irk_n,irk_loaded,phone_loaded,watch_n,watch_loaded,heap}`（`irk_avg`／`irk_n` 是手機那筆）；`binary_sensor.rp_{phone_seen,watch_seen,nodes_alive,phone_irk_loaded,away_condition,pc_locked,pc_online}`；`sensor.rp_fp_zone`、`sensor.rp_fingerprints`；helper `input_number.rp_{kitchen_max_cm,hysteresis_db,fp_hysteresis,min_rssi}`。
- 節點訊號 = 手機 IRK 解析後的平均（沒收到就是 -127）；沒載入手機 IRK 時，取最強的非靜止 Apple 廣告（`mov_best`）。手錶和靜止裝置永遠不算。
- 區域判斷順序：PC 解鎖且閒置 < 60 秒 -> pc_desk；`yui_home` 關閉 -> away；廚房雷達提示 -> kitchen；所有節點訊號都低於 `rp_min_rssi` -> home；有校正過的指紋區域（`rp_fp_zone`）就用它；都沒有就取 pcdesk／workdesk 中較強的一邊，加遲滯。
- 在家／離家：`binary_sensor.yui_home` = NOT `rp_away_condition`；away 條件要持續 5 分鐘：PC 沒在用 AND 手機 IRK 已載入 AND 至少一個節點活著 AND 沒有節點收到手機 AND（沒收到手錶 OR `device_tracker.ephone` = not_home）。如果沒有節點活著或沒載入手機 IRK，只看 `device_tracker.ephone` 是不是 not_home。`device_tracker.ephone`（mobile app）有在實體登錄裡，現在是 `home`。5 分鐘規則讓手機短暫收不到不至於誤判；放在桌上的手錶沒辦法在 app 已經說 not_home 之後還把屋主留在「在家」。簡報裡的規則是 3 分鐘內算看到，這裡給手機完整的 5 分鐘才判離家，因為廣告本來就斷斷續續。
- 今晚用假 MQTT 訊息透過 broker 驗過（不 retained，自己會過期；當時還沒有指紋）：(a) 手機 -55 在 workdesk／-70 在 pcdesk，手錶 -40、一台 MacBook 在 pcdesk 是 -30 -> `rp_workdesk_signal` -55、`rp_pcdesk_signal` -70，區域 work_desk，在家 on；(b) 沒 IRK，MacBook 靜止（mov_n 0）加一支走動的手機 -62 -> pcdesk 訊號 -62，區域 pc_desk；(c) IRK 已載入但什麼都沒收到 -> 5 分鐘後 `rp_away_condition` 打開、`yui_home` 關閉、區域 away；假訊息停了之後一切回到 home。讀狀態的輔助指令：`ssh <server> "docker exec -i homeassistant python - rp_ yui_" < ha\ha_states.py`；假訊息產生器：`py -3.13 ha\fake_test.py <phone_work|nophone_mac|none> <seconds>`。
- 自動化（受總開關管制，只在暗的時候，00:30-07:30 之外，且燈在最近 10 分鐘內沒被改過）：PC 桌前有人 -> 燈條開 2900 K 55 %；空 10 分鐘 -> 關；工作台有人 -> light.channel_b 開；空 10 分鐘 -> 關。原有的廚房自動化沒動。BLE 韌體還沒上線前不要開。

## 從手機校正（校正服務）

- `nlpi/calibrate_svc.py` 在 `<server>` 上以 `presence-calibrate.service` 執行（安裝：`nlpi/install_calibrate.sh`，env 跟
  `presence-ble` 共用）。它聽 MQTT `presence/calibrate/cmd`（`{"action":"start","zone":..,"seconds":60}`／`clear`／`cancel`），
  用 `calibrate.record_zone` 記錄，並把進度以 retained 發到 `presence/calibrate/status`。
- 指紋以它為準：`<server>` 上的 `/var/lib/presence-calibrate/fingerprints.json`（第一次啟動時從 retained 的
  `presence/config/fingerprints` 灌進去）。PC 上的 `calibrate.py` 有自己的 `fingerprints.json`：兩邊不要混用。
- HA：`dashboards/house.yaml` 裡的儀表板視圖 `定位校準`（來源 `ha/dashboard_calibrate_view.yaml`），按鈕
  `button.rp_calibrate_<zone>`、`..._cancel`、`..._clear_<zone>`，感測器 `sensor.rp_calibrate_status`／`_progress`。除錯視圖 `定位除錯` 在同一個檔案（見[維運](operations.zh-TW.md)的 2026-10-28 更新）。
- 改過 HA 套件的 `mqtt:` 段之後，對 `ha/admin/reload_mqtt` 隨便發個訊息（不用 token）。
- 日誌：`journalctl -u presence-calibrate`。回滾：`systemctl disable --now presence-calibrate`；HA 備份 `*.bak-20261027e`。

## 之後分析用的電力紀錄（晚餐時段高瓦數）

- `nlpi/powermon_log.py` 在 `<server>` 上以 `powermon-log.service` 執行（裝在 `/opt/powermon-log`）。它把每一筆
  `power/powermon/state`（約 10 秒）連同最新的 `pc/telemetry/state` 瓦數寫到 `/var/lib/powermon-log/power-YYYY-MM-DD.csv`
  （`ts,watts,total_kwh,swing_mv,peak_mv,rssi,pc_w,pc_gaming,pulses,rejected`，約 700 KB/天）。
- 任何讀值 >= `EVENT_W`（1000 W）就開一個事件，連續 3 筆低於才關閉：`events.csv`
  （`start,end,secs,peak_w,mean_w,kwh_est,pc_w_mean`）。全屋負載減掉 `pc_w` 大約就是廚房／其他負載。
- 分析：`scp <server>:/var/lib/powermon-log/*.csv .`；區域脈絡（廚房）在 HA recorder 裡（`sensor.yui_room_zone`）。

## 位置標註網站（分析用的 ground truth）

- `nlpi/label_site.py` 在 `<server>` 上以 `label-site.service` 執行：`http://<server-ip>:8097/`（只限 LAN，綁在 `<server-ip>`，沒有認證；8099
  被另一個服務用掉了）。四個區域按鈕；按一下就把最近 15 秒的手機／手錶（IRK 標記）、非靜止
  BLE 和每個節點的 CSI 存進 SQLite `/var/lib/presence-labels/labels.db`（`labels`、`samples`），有復原按鈕，CSV 匯出
  `/api/export.csv`，MQTT 事件 `presence/label/event`。
- 用法：站到某一區、按它的按鈕，不同時間／姿勢重複做；之後把標註跟 `sensor.yui_room_zone`
  歷史比對，重新擬合指紋。

## 區域歷史紀錄（回追用）

- HA 自動化 `rp_zone_to_mqtt` 每 10 秒以及有變化時，發 retained 的 `presence/zone/state`（zone、fp zone、home、PC online/locked/idle、廚房雷達、
  `light.channel_b` 和燈條狀態加最後變更 epoch、ESPHome link motion）。`nlpi/zone_log.py`
  （`zone-log.service`）每 10 秒寫 `/var/lib/zone-log/zone-v3-YYYY-MM-DD.csv`：zone、fp zone、脈絡欄位（PC、雷達、
  powermon W、燈含廚房燈／NeoPixel、gaming PC）、每個節點的手機／手錶平均 dBm，以及每個節點 10 秒的 CSI motion／still 平均（
  就是比對器用的特徵），另外 `transitions.csv`（每次區域變化）。`zone-YYYY-MM-DD.csv`（v1）和 `zone-v2-*`（live weak_label）是舊 schema。
- Weak label（被動訓練資料，不用按按鈕）：規則和門檻在 `tools/weak_labels.py` 的 docstring（離線套用，不是 logger 做的）（pc_desk = PC
  解鎖且閒置 < 60 秒；kitchen = 雷達 < 250 cm 或 powermon >= 1000 W；work_desk = `light.channel_b` 開著 + PC 沒在用 + workdesk
  手機高於中位數；bed = 00:30-07:30、PC 鎖定、手機 RSSI 穩定 10 分鐘）。空 = 不確定，兩個區域 = 衝突 = 空。
  拿它們訓練／評估：`python tools/fp_eval.py weak export.csv zone-v3-*.csv`。測試：`python tools/test_weak_labels.py`、
  `python nlpi/test_zone_log.py`。
- 現在人在哪：`sensor.yui_room_zone`；歷史：那些 CSV（或 HA recorder／logbook）。
