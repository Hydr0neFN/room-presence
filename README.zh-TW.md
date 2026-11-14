[English](README.md) · **繁體中文**

# 房間定位（WiFi + BLE：免裝置與帶裝置的房內位置）

回答的問題：房間裡的人在哪一區 - KITCHEN / WORK DESK / PC DESK / BED / home（位置不明）/ away。

## 總覽

| | |
|---|---|
| **問題** | 房間裡的人在哪一區：kitchen、work desk、PC desk、bed、home（位置不明）、away。 |
| **做出來的東西** | 兩類主要訊號在 HA（`ha/room_presence.yaml`）裡用最近鄰校正指紋融合：一種是量 WiFi（CSI 加 link RSSI），人身上不用帶裝置；另一種是各錨點收到屋主手機的 BLE RSSI。 |
| **結果** | HA 裡的 `sensor.yui_room_zone`。分區準確率：只用 BLE 0.82，只用 CSI 0.48（只取 motion 特徵 0.62）：85 筆人工標註、4 個區域、leave-one-visit-out。 |
| **技術堆疊** | ESP32-C3（lightbar2mqtt、LightController、powermon）、NLDeskCYD ESP32 面板、ESPHome D1 mini、Raspberry Pi、Home Assistant、MQTT。 |

## 運作方式

```
BLE phone RSSI --+
WiFi CSI --------+- ESP32 / Pi anchors -- MQTT --> Home Assistant
PC idle ---------+                                nearest fingerprint
                                                        |
                                             sensor.yui_room_zone
```

- 每個錨點 ESP32 以約 20 Hz 對 gateway 發 ping，讀回覆中每個子載波的通道振幅（L-LTF），每 1 秒整理成一筆摘要，約 2 Hz 發到 `presence/<node>/csi`。
- ESPHome 的 kitchenradar 與 NeoPixel 每 1 秒發一次 WiFi RSSI；CSI 的 `motion` 值變成 10 秒平均的 `sensor.rp_link_<node>_motion`，link RSSI 則是 20 秒標準差（dB）。
- 每個錨點每 3 秒發 `presence/<node>/apple`：各錨點收到屋主手機的 BLE RSSI，就是一組位置指紋。
- `calibrate.py --zone <z>` 依區域存下每個錨點的手機 BLE RSSI 中位數，加上 CSI／link 特徵的中位數。
- HA 選 RMS 距離最小的區域（BLE 以 dB 計，特徵以 sigma 乘 `input_number.rp_feat_weight_db` 計，預設 6 dB；缺的特徵跳過）。

## 設計
兩類主要訊號，在 HA（`ha/room_presence.yaml`）裡用最近鄰校正指紋融合：
1. 量 WiFi，人身上不用帶任何裝置。
   - CSI：每個錨點 ESP32 以約 20 Hz 對 gateway 發 ping，讀回覆中每個子載波的通道振幅（L-LTF），把 1 秒的摘要（motion、still、mean、rssi、rssi_sd、n、ok）以約 2 Hz 發到 `presence/<node>/csi`。有人在房裡移動就會擾動通道；HA 的統計感測器 `sensor.rp_link_<node>_motion` 是 `motion` 的 10 秒平均（千分比）。
   - Link RSSI：ESPHome 的 kitchenradar 與 NeoPixel 每 1 秒發一次 WiFi RSSI；`sensor.rp_link_kitchenradar_motion` / `_neopixel_motion` 是 20 秒標準差（dB）。
2. BLE：每個錨點都看得到屋主手機（Apple Continuity 廣告），手機在每個錨點的 RSSI 就是一組位置指紋。節點每 3 秒發 `presence/<node>/apple`。載入手機 IRK 後只算屋主的手機（會解析輪替的私密位址）；沒載入時取最強的非靜止 Apple 廣告（桌下的 MacBook／iPad 5 分鐘後會被當成靜止濾掉）。
3. 輔助證據：PC 閒置時間／鎖定（`PresencePcIdle` 排程工作 -> `presence/pcdesk/idle`；解鎖且閒置 < 60 秒 = pc_desk），以及廚房雷達（有 presence 且距離 < `rp_kitchen_max_cm` = 廚房提示）。

融合：`calibrate.py --zone <z>` 依區域存下每個錨點的手機 BLE RSSI 中位數，加上 CSI／link 特徵的中位數（該位置安靜與移動時的量測值）。HA 選 RMS 距離最小的區域（BLE 以 dB 計，特徵以 sigma 乘 `input_number.rp_feat_weight_db` 計，預設 6 dB；缺的特徵跳過，遲滯 `rp_fp_hysteresis` 2.5 dB）。`yui_room_zone` 的判斷順序：PC 使用中 -> pc_desk；`yui_home` 關閉 -> away；廚房雷達提示 -> kitchen；所有 BLE 都低於 `rp_min_rssi` -> home；指紋區域；最後才取 pcdesk／workdesk 中較強的一邊。

## 量到什麼

13:28-13:32 屋主坐在 PC 前，系統卻顯示 kitchen（1800 W + 燈）。廚房證據現在只認雷達（`rp_kitchen_radar_hint`）；powermon >= 1000 W 和廚房燈數，只有在雷達前 2 分鐘內看到人時才算（`rp_kitchen_radar_recent`）。重跑 `analysis/a10_sim_fix.py`（`analysis/out_a10_kitchen_radar_only.txt`）：kitchen 列 603 -> 486，117 列改判 home，對 weak label 的分區準確率不變。

只用 CSI 的分區準確率（`tools/fp_eval.py cv analysis/data/export.csv --cv visits --purge-s 15`，85 筆人工標註、4 個區域、leave-one-visit-out）：只用 BLE 0.82；CSI 權重加到 w = 6 dB 時 0.48（只用 motion 特徵 0.62）。以目前的特徵，單靠 CSI 明顯比 BLE 差。

10-27 17:13 的 pcdesk 重置是板子自己重置（brownout／USB 供電），發佈沒有卡住；舊的 guard 之後讓 BLE 整個開機週期都關著。現在改成延後啟動。

workdesk（LightController，RSSI 平均 -73.6 dBm，最弱的一條 link；從 `<server>` ping 平均 25 ms／最大 66 ms，kitchenradar 只要 8 ms）：`light.channel_b`（同一塊板上的 HomeKit 燈）在 presence 斷線的同一段時間也不可用，ESPHome 的 kitchenradar／neopixel 從來沒斷，其他 presence 節點也正常。所以是整塊板子離開網路，不只 presence 資料流；這個問題在 presence 韌體之前就有（屋主）。目前還分不出是重開機還是 Wi-Fi 掉線：新加的感測器 `sensor.rp_<node>_uptime`（來自 `/ble` 狀態）會在下一次斷線時給答案（uptime 重置 = 重開機，沒重置 = 關聯掉線）。

## 錨點配置
房間是一個長方形：一端是廚房／門口，另一端是窗戶和床，PC 桌和工作台在靠近床那端的兩面長牆上。
| 節點 | 位置 | 硬體 |
|---|---|---|
| `pcdesk` | PC 桌下、左後最深角，旁邊是電源延長線，人坐著時被身體擋住，緊貼 MacBook | lightbar2mqtt ESP32-C3 |
| `cyd` | 在 PC 桌面上方（最好的 pc_desk 錨點，跟坐著的人視線直通） | NLDeskCYD ESP32 面板 |
| `workdesk` | 工作台 | LightController ESP32-C3 |
| `nlpi` | 床下地板上、金屬暖氣片前，緊鄰 TP-Link AX10（2.4 GHz AP）；只做 BLE | Raspberry Pi |
| `kitchen` | 電表／廚房處的 powermon ESP32-C3；目前板子還放在 PC 旁（不在電表上）：裝好之前不要用它校正 `kitchen` 區 | powermon C3 |
| links | kitchenradar（`<kitchenradar-ip>`）、neopixel（`<neopixel-ip>`）只提供 WiFi RSSI；雷達提示也由 kitchenradar 提供 | ESPHome D1 mini |
`pcdesk` 和 `nlpi` 幾乎在同一條線上，又都被金屬和 2.4 GHz 流量干擾：不要相信「最強錨點勝出」，請用指紋。

## 這個設計依據的屋主事實
- 手機：100 % 會跟著屋主出門。手錶：90 %，而且在家的時候放在桌上（只在戶外戴；打字／打電動時屋主會拿下來）。所以手錶永遠不當區域訊號，只輔助判斷在家／離家。
- 固定的 Apple 裝置：2 台 MacBook 卡在 PC 桌板下，一台 iPad 在桌上的懸臂支架。它們整天用固定 RSSI 打廣告。見「靜止過濾」。
- 錨點：lightbar ESP32-C3 在 PC 桌下左後最深角，旁邊是延長線和一堆線，人坐著時被身體擋住，就在 Mac 旁邊；`<server>` 在床下地板上、金屬暖氣片前，夾在一堆電源轉接頭之間，旁邊是 TP-Link AX10（2.4 GHz AP）；LightController 在工作台；NLDeskCYD 螢幕在 PC 桌面上方，本來會是最好的 pc_desk 錨點（現在 BLE + CSI 都跑在它上面，見目前狀態）。房間是一個長方形：一端是廚房／門口，另一端是窗戶和床，PC 桌和工作台在靠近床那端的兩面長牆上。結果：pcdesk 和 nlpi 幾乎在同一條線上，又都被金屬和 2.4 GHz 流量干擾，所以不要相信「最強錨點勝出」：要用校正過的指紋。校正時手機擺的方向要跟平常帶的一樣。

## 目前狀態（2026-10-27 ~14:50）
所有韌體映像檔：`firmware/`（含密鑰，已 gitignore）；回復映像檔：`rollback/`。從這台 PC 做 OTA 會失敗（防火牆：裝置連不回來）- 一律從 `<server>` 做 OTA。HA 套件 `ha/room_presence.yaml` 於 13:13 部署；結果實體是 `sensor.yui_room_zone`。總開關 `input_boolean.room_presence_automations` 是 OFF。
裝置表、2026-10-28 的變更、回滾步驟、屋主檢查清單與 bring-up 紀錄：[維運](docs/operations.zh-TW.md)。

## 限制與未完成項目
- CSI 和 link RSSI 看到的是「動作」。人坐著不動幾分鐘，這兩個特徵就讀成「空房間」；靜止的情況靠手機 BLE RSSI 和 PC 閒置。特徵能幫忙分辨是哪一區、房裡有沒有人，但不能數人數。
- BLE RSSI 很吵（+-5-10 dB），會受手機怎麼帶、身體遮蔽影響；pcdesk／nlpi 的幾何位置不好。區域邊界附近本來就會跳，遲滯有幫助，但沒辦法做到完全準。
- 沒載入手機 IRK 時，手機放著不動 5 分鐘以上會被當成靜止濾掉，鄰居的手機也可能勝出：校正時用哪種模式，跑的時候就用哪種；IRK 載入後要重新校正。
- 指紋的品質取決於那 60 秒的校正；增加／移動錨點或換韌體後，全部區域都要重跑。節點重開後等 5 分鐘（靜止過濾）。
- 所有錨點跟 AP 和其他錨點共用 2.4 GHz 頻段；每個 CSI 節點約 20 Hz 的 ping（4 個節點）用掉的 airtime 不算少。WiFi 開始不穩時，先對比較沒用的節點下 `csi_off`。
- 廚房節點還沒裝到廚房；廚房區目前只靠雷達提示。
- 不追第二個人、不做方向、除了手機 IRK 之外不做身分。
- 已知未完成：loop-WDT 沒做故障注入；NimBLE host task 卡住，現在只有 lightbar 有 loop-WDT 擋著（cyd／LightController／powermon 還是只有 200 ms try-lock，沒有 loop WDT）。

## 建置與執行
一次校正一個區域（手機放平常的口袋，每個約 1 分鐘，房裡不要有別人走動）：
```
py -3.13 calibrate.py --zone kitchen
py -3.13 calibrate.py --zone work_desk
py -3.13 calibrate.py --zone pc_desk
py -3.13 calibrate.py --zone bed
```
`--show --feat-note` 會印出存下來的手機 BLE 讀值和 CSI／link 特徵；`--clear <zone>` 刪掉其中一個。link 特徵需要能從這台 PC `ssh <server>`。
重新部署 HA：把 `ha\room_presence.yaml` scp 到 `/opt/homeassistant/config/packages/`，跑 `docker exec homeassistant python -m homeassistant --script check_config -c /config`，再 `docker restart homeassistant`。
單元測試：在 `nlpi\` 下跑 `py -3.13 -I -m unittest`（7 個測試）；`python tools/test_weak_labels.py`；`python nlpi/test_zone_log.py`。
各節點韌體、OTA 路徑與建置目錄：[韌體與節點](docs/firmware.zh-TW.md)。

## 目錄結構
- `analysis/` - 廚房證據修正的重跑腳本與輸出（`a10_sim_fix.py`、`out_a10_*.txt`）。
- `csi/` - CSI presence 模組（`csi_presence.cpp/.h`）。
- `ha/` - HA 套件 `room_presence.yaml`、儀表板視圖來源、`fake_test.py`、`ha_states.py`、`test_fp_features.py`。
- `lc-port/` - LightController CSI hook 的 diff 和 `patch_cyd.py`。
- `nlpi/` - Pi 上的服務：BLE 掃描器、校正服務、標註網站、zone log、power log、單元測試。
- `pc-idle/` - PC 閒置發佈程式和排程工作安裝腳本。
- `tools/` - 建置／OTA 輔助腳本與評估腳本（`fp_eval.py`、`weak_labels.py`、`espota_slow.py`）。
- 根目錄的 `calibrate.py` 與各 patch：`lightcontroller-ble-presence.patch`、`ble-watchdog.diff`、`ble-presence-guard.diff`、`cyd-port.diff`、`cyd-port2.diff.txt`、`ping_before_ps.txt`、`ping_after_ps.txt`。

## 文件
- [維運：狀態與回滾](docs/operations.zh-TW.md) - 部署狀態、回滾、屋主檢查清單、bring-up 紀錄。
- [韌體與節點](docs/firmware.zh-TW.md) - 各節點韌體、OTA 路徑、AX10 RSSI 的問題。
- [Home Assistant 與資料紀錄](docs/home-assistant.zh-TW.md) - 實體、IRK、校正服務、標註網站、zone log、power log。
- [既有方案](docs/prior-art.zh-TW.md) - 讀過什麼、還有什麼沒抄進來。

## 授權

MIT License，詳見 [LICENSE](LICENSE)。
