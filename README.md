# Room presence (overnight phase, 2026-10-26 -> 27)

Zones: KITCHEN / WORK DESK (LightController) / PC DESK (lightbar2mqtt, NLDeskCYD) / away.
Method (owner's correction): compare wireless signal strength (BLE RSSI) between anchors; radar is only a kitchen hint; PC idle time is extra evidence for the PC desk.
(This file is the phase report; the harness refused the name REPORT.md.)

## Status at a glance
| Piece | State |
|---|---|
| Flashing | NO device was flashed tonight (01:00 window missed: BLE build only fit after switching toolchain, then review found 3 bugs, fixed at 01:07). All devices untouched. |
| KitchenRadar | Already exposes distance/energy sensors in HA (firmware == disk YAML, verified with aioesphomeapi). No OTA needed. |
| lightbar2mqtt BLE firmware | Built + reviewed (critic-opus), NOT flashed. Branch `ble-presence` (f3a4fac, 20d35f0, 9e7dd24) |
| LightController BLE | NOT done (no flashing after 01:00; working tree has owner's uncommitted changes). Port after lightbar proves out. |
| NLDeskCYD | Untouched. |
| PC idle publisher | LIVE: Task Scheduler `PresencePcIdle` (at logon, user level, hidden pythonw) |
| HA package | LIVE (HA container restarted once ~00:57 after config check) |
| Automations | Installed, master switch `input_boolean.room_presence_automations` is OFF |

## 1. KitchenRadar
Live entities (verified): binary_sensor.kitchenradar_presence, sensor.kitchenradar_{detection,moving,still}_distance, _moving_energy, _still_energy. At 00:40 presence off, detection distance 127 cm. Used only as kitchen hint: kitchen = presence on AND detection_distance < `input_number.rp_kitchen_max_cm` (250). Calibrate by standing at kitchen / work desk / PC desk and watching the distance sensor. Unknown whether the radar also sees the desks.

## 2. BLE firmware (lightbar2mqtt, PC desk anchor) - READY, NOT FLASHED
- Repo `C:\Users\Ruanyouyi\Rpi4\lightbar2mqtt`, branch `ble-presence` (main untouched). New `ble_presence.h/.cpp`, small hooks in `Lightbar.ino`, `mqtt.cpp`, `platformio.ini`.
- Passive scan (interval 100 ms / window 30 ms, ~30 % duty); Apple Continuity adverts (type 0x10/0x0C/0x0F); if IRKs are loaded, RPAs are resolved with AES `ah()` (both byte orders tried). Publishes every 3 s (not retained) `presence/pcdesk/apple`:
  `{"scan":1,"apple_best":-61,"apple_avg":-66,"apple_n":9,"irk_n":1,"irk":[{"best":-58,"avg":-60,"n":4}],"heap":98000,"up":55}` (-127 / n=0 = not heard).
- Controls: `on` / `off` to `presence/pcdesk/cmd`. IRKs: retained message to `presence/config/irk`, hex, any separators, up to 4 keys, no reflash. Node name = `BLE_NODE_NAME` (default `pcdesk`; use `#define BLE_NODE_NAME "workdesk"` in config.h for the other anchor).
- PLATFORM PIN: the current pioarduino platform (Arduino 3.3.9) gives a 1.196 MB baseline and BLE no longer fits the 1.25 MB OTA slot (1.375 MB). `platformio.ini` now pins `espressif32@6.9.0` (Arduino 2.0.17): baseline 875 kB, with BLE 1,006,044 B (76.8 %). Isolated PlatformIO core dir `C:\pio-lb` so other projects' toolchains stay untouched. Build from PowerShell/cmd (not Git Bash): `$env:PLATFORMIO_CORE_DIR='C:\pio-lb'; pio run -e esp32-c3-pro-mini-ota`. Also fixed a broken esptool in `~/.platformio/penv` (`uv pip install --reinstall esptool==5.3.0`).
- TRADE-OFF found by review: WiFi/BT coexistence aborts if WiFi modem sleep is off, so this build uses `WIFI_PS_MIN_MODEM` instead of `setSleep(false)`. The earlier fix for ping jitter / MQTT reconnects is undone on this branch. Watch the MQTT reconnect count after flashing; if reconnects rise badly, send `off` and/or roll back.
- Safety nets: OTA initialised before BLE; BLE skipped for one boot after a panic/WDT reset (no boot loop); scanner stops below 40 kB free heap, restarts above 50 kB.
- Images (gitignored, contain secrets): new `C:\Users\Ruanyouyi\Rpi4\room-presence\firmware\lightbar2mqtt-ble-presence.bin` (sha256 31673d98...6dcd). Rollback `...\room-presence\rollback\lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin` (907,968 B, sha256 146e0cca...). Caveat: what runs on the bar now was built Sep 6 (dist/ bin); the rollback bin is the same source rebuilt, not byte-identical.
- OTA (PowerShell; env `LIGHTBAR_OTA_PASSWORD` = OTA_PASSWORD from config.h; firewall rule for TCP 3233 per platformio.ini comments):
  ```
  cd C:\Users\Ruanyouyi\Rpi4\lightbar2mqtt
  $env:PLATFORMIO_CORE_DIR='C:\pio-lb'
  pio run -e esp32-c3-pro-mini-ota -t upload --upload-port 10.0.0.87
  ```
  Rollback: `py -3.13 C:\pio-lb\packages\framework-arduinoespressif32\tools\espota.py -i 10.0.0.87 -p 3232 --auth=%LIGHTBAR_OTA_PASSWORD% -f C:\Users\Ruanyouyi\Rpi4\room-presence\rollback\lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin` (USB: `pio run -e esp32-c3-pro-mini -t upload`).
- After flashing, for >= 5 min: light bar command from HA still works, `sensor.rp_pcdesk_*` become available, `sensor.rp_pcdesk_heap` stays > 60 kB.
- Not run on hardware. Risks: BLE+WiFi on single-core C3; NimBLE 2.5.2 on Arduino 2.0.17 compiled but never executed.

## 3. PC idle publisher (live)
`pc-idle\pc_idle.py` -> `presence/pcdesk/idle` `{"idle":s,"locked":0|1}` every 10 s, `presence/pcdesk/status` online/offline (retained, LWT). Dedicated mosquitto user `pcpresence` (password only in gitignored `pc-idle/mqtt-pass.local`; nl-pi `/etc/mosquitto/passwd` backed up as `passwd.bak-2026-10-27`; broker has no ACLs). Task `PresencePcIdle`. Verified by subscribing. Stop: `Stop-ScheduledTask PresencePcIdle`; remove: `Unregister-ScheduledTask PresencePcIdle`.

## 4. Home Assistant (live)
- `/opt/homeassistant/config/packages/room_presence.yaml` (source `ha\room_presence.yaml`); `configuration.yaml` got `homeassistant: packages: !include_dir_named packages` (backup `configuration.yaml.bak-2026-10-27`). Rollback: restore backup, move package file away, restart container.
- MQTT YAML entities work in packages here (verified with fake firmware messages).
- Entities: `sensor.yui_room_zone` (kitchen | work_desk | pc_desk | away_or_elsewhere; attributes show signals), `binary_sensor.room_zone_{kitchen,work_desk,pc_desk,away}` (kitchen off-delay 30 s; work_desk on 8 s / off 60 s; pc_desk on 5 s / off 30 s; away on 120 s), `sensor.rp_pcdesk_signal`, `sensor.rp_workdesk_signal`, raw `sensor.rp_*`, `binary_sensor.rp_pc_locked`, `rp_pc_online`, helpers `input_number.rp_kitchen_max_cm` (250), `rp_hysteresis_db` (6), `rp_min_rssi` (-90), `input_boolean.room_presence_automations` (OFF).
- Logic: PC unlocked and idle < 60 s -> pc_desk; kitchen radar hint -> kitchen; both BLE signals below floor -> away; else stronger anchor wins, switching only when the other is better by more than the hysteresis. Anchor signal = owner's IRK-resolved phone when an IRK is loaded on that node, else strongest Apple advert of any nearby device (can pick up neighbours; stopgap). Until the BLE firmware is flashed the zone is only pc_desk / kitchen / away_or_elsewhere.
- Verified after restart: no config errors, entities present, zone = pc_desk (PC in use).
- Automations (gated by master switch, dark only, not 00:30-07:30, light not changed in the last 10 min): PC desk occupied -> light bar on 2900 K 55 %; PC desk empty 10 min -> bar off; work desk occupied -> light.channel_b on; empty 10 min -> off. Existing kitchen automations untouched. Do NOT enable before the BLE firmware is live: with only PC idle, watching a video for > 60 s flips the zone and turns the bar off after 10 min.

## 5. Morning to-do for the owner
1. Flash lightbar (commands above), watch 5 min; if fine, port ble_presence to LightController (`BLE_NODE_NAME "workdesk"`, same platform pin; its uncommitted changes must be preserved).
2. iPhone IRK (makes the signal follow the phone instead of any Apple device): obtain per the Home Assistant "Private BLE Device" docs (needs a Mac on the same iCloud account), then `mosquitto_pub -r -t presence/config/irk -m "<32 hex chars>"`.
3. Calibrate: sit at each desk and in the kitchen, note `sensor.rp_pcdesk_signal` / `rp_workdesk_signal` and the kitchen distance; tune hysteresis, floor, kitchen distance.
4. Turn on `input_boolean.room_presence_automations`.
- Skipped: NLDeskCYD zone label, ESP8266 WiFi sniffing (no flash window, risk).

## Decisions taken
- No flashing after 01:00 and no unreviewed flash before it.
- Toolchain pin to Arduino 2.0.17 + isolated core dir (image otherwise exceeds the OTA slot; partition table cannot change over OTA).
- Automations installed but disabled by default.
