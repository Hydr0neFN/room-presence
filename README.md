# Room presence (WiFi + BLE device-free / device-based room location)

Question answered: which zone of the room is the owner in - KITCHEN / WORK DESK / PC DESK / BED / home (place unknown) / away. Result: `sensor.yui_room_zone` in Home Assistant (HA).

## Design
Two first-class signal families, fused in HA (`ha/room_presence.yaml`) by nearest calibrated fingerprint:
1. WiFi sensing, no device on the person needed.
   - CSI: each anchor ESP32 pings the gateway at ~20 Hz, reads the per-subcarrier channel amplitude (L-LTF) of the replies and publishes a 1 s summary (motion, still, mean, rssi, rssi_sd, n, ok) ~2 Hz to `presence/<node>/csi`. A body moving in the room disturbs the channel; the HA statistics sensor `sensor.rp_link_<node>_motion` is the 10 s mean of `motion` (per-mille).
   - Link RSSI: the ESPHome kitchenradar and NeoPixel publish their WiFi RSSI every 1 s; `sensor.rp_link_kitchenradar_motion` / `_neopixel_motion` are the 20 s standard deviation (dB).
2. BLE: the owner's phone (Apple Continuity adverts) seen by every anchor; its RSSI per anchor is a position fingerprint. Nodes publish `presence/<node>/apple` every 3 s. With the phone IRK loaded only the owner's phone (rotating private address resolved) counts; without it the strongest NON-stationary Apple advert is used (MacBooks/iPad under the desk are filtered as stationary after 5 min).
3. Extra evidence: PC idle time/lock (`PresencePcIdle` scheduled task -> `presence/pcdesk/idle`; unlocked and idle < 60 s = pc_desk) and the kitchen radar (presence + distance < `rp_kitchen_max_cm` = kitchen hint).

Fusion: `calibrate.py --zone <z>` stores, per zone, the median BLE phone RSSI per node plus the median CSI/link features (the quiet-vs-motion levels at that spot). HA picks the zone with the smallest RMS distance (BLE in dB, features in sigma x `input_number.rp_feat_weight_db`, default 6 dB; unavailable features are skipped, hysteresis `rp_fp_hysteresis` 2.5 dB). Order in `yui_room_zone`: PC active -> pc_desk; `yui_home` off -> away; kitchen radar hint -> kitchen; all BLE below `rp_min_rssi` -> home; fingerprint zone; fallback stronger of pcdesk/workdesk.

## Anchor map
The room is one long rectangle: kitchen/door at one end, window and bed at the far end, PC desk and work bench on opposite long walls near the bed end.
| Node | Where | Hardware |
|---|---|---|
| `pcdesk` | under the PC desk, far-left back corner, next to a power strip, shadowed by the body when seated, right beside the MacBooks | lightbar2mqtt ESP32-C3 |
| `cyd` | ON TOP of the PC desk (best pc_desk anchor, line of sight to the seated person) | NLDeskCYD ESP32 panel |
| `workdesk` | work bench | LightController ESP32-C3 |
| `nlpi` | on the floor under the bed in front of a metal radiator, next to the TP-Link AX10 (2.4 GHz AP); BLE only | Raspberry Pi |
| `kitchen` | powermon ESP32-C3 at the electricity meter / kitchen; TODAY the board still sits at the PC (not on the meter): do not calibrate zone `kitchen` with it until it is installed | powermon C3 |
| links | kitchenradar (10.0.0.128), neopixel (10.0.0.100) WiFi RSSI only; kitchenradar also gives the radar hint | ESPHome D1 minis |
`pcdesk` and `nlpi` are nearly collinear and both distorted by metal and 2.4 GHz traffic: never trust "strongest anchor wins", use the fingerprints.

## Current status (2026-10-27 ~14:50)
All firmware images: `firmware/` (contain secrets, gitignored); rollback images: `rollback/`. OTA from this PC fails (firewall: the device cannot connect back) - always OTA from nl-pi (`ssh nl-pi-lan`, copy bin + `tools/espota_slow.py` to /tmp). Builds: lightbar `PLATFORMIO_CORE_DIR=C:\pio-lb`, CYD `C:\pio-cyd` + scratch copy `C:\cyd-work`, powermon `C:\pio-pm`, ESPHome from `C:\esp-work` (`py -3.13 -m esphome`); from Git Bash run PowerShell as `env -u MSYSTEM powershell ...`.
| Device (IP) | Firmware / branch / commit | Publishes | Rollback | OTA route |
|---|---|---|---|---|
| nl-pi (10.0.0.20) | `nlpi/` service `presence-ble` (this repo) | `presence/nlpi/apple` 3 s | `systemctl disable --now presence-ble`; broker passwd backup | n/a |
| lightbar (10.0.0.87) node `pcdesk` | lightbar2mqtt, branch `ble-presence`, 6cd8f71 (BLE + CSI) + init-fail/brownout guard in `ble_presence.cpp` (working tree, uncommitted in lightbar2mqtt), flashed 14:36 (`firmware/lightbar2mqtt-ble-csi-guard.bin`) | apple 3 s, csi ~2 Hz (heap 136 kB) | `rollback/lightbar2mqtt-ble-csi-6cd8f71-20261027.bin` (BLE + CSI without guard, what ran 12:30-14:36), `lightbar2mqtt-ble-pre-csi.bin` (BLE only), `lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin` (no presence) | nl-pi `espota_slow.py -p 3232 --auth=<OTA_PASSWORD>` |
| LightController (10.0.0.93) node `workdesk` | owner's working tree + `lightcontroller-ble-presence.patch` + `lc-port/lightcontroller-csi-hooks.diff`, UNCOMMITTED, patch regenerated with the init-fail/brownout guard (`ble_presence.cpp` hunk 594 lines), flashed 14:39 (`firmware/lightcontroller-ble-csi-guard.bin`); the board did not come back after the OTA reboot and needed a power cycle (owner, ~14:46), cause unknown | apple, csi (heap ~52 kB) | `rollback/lightcontroller-ble-csi-20261027.bin` (without guard, ran 12:33-14:39), `lightcontroller-ble-pre-csi.bin`, `lightcontroller-prepatch-2026-10-27.bin` | nl-pi stock `espota.py` (HomeSpan PBKDF2 auth; espota_slow fails) |
| NLDeskCYD (10.0.0.82) node `cyd` | NLDeskCYD repo, branch `ble-presence`, de56799, flashed 13:06 (`firmware/nldeskcyd-ble-csi-v3.bin`) | apple 3 s, csi ~2 Hz (heap 62 kB idle, min 52 kB; was 140 kB without BLE) | `rollback/nldeskcyd-HEAD1a663a7.bin` (main, no presence) | nl-pi `espota_slow.py` (MD5 auth) |
| powermon C3 (10.0.0.77) node `kitchen` | powermon repo, branch `c3-port`, fe961bc, flashed 13:09 (`firmware/powermon-c3-csi.bin`) | apple, csi, wifi_rssi 1 Hz, power/powermon/state+diag | `rollback/powermon-c3-pre-csi-9faaafc.bin` | nl-pi stock `espota.py -P 3233 -p 3232 --auth=<data/config.json otaPassword>` |
| kitchenradar (ESPHome) | wifi_signal sensor 1 s | `sensor.chu_fang_kitchenradar_wifi_signal` | `rollback/kitchenradar-esphome-prev-20261027.bin` | `esphome upload` from `C:\esp-work` (worked from the PC) |
| neopixel (ESPHome) | wifi_signal 60 s -> 1 s | `sensor.neopixel_neopixel_wifi_signal` | `rollback/neopixel-esphome-prev-20260903.bin` | same |
| HA package | `ha/room_presence.yaml`, deployed 13:13 (nodes pcdesk, workdesk, nlpi, kitchen, cyd) | `sensor.yui_room_zone` | `packages/room_presence.yaml.bak-20261027c` (before cyd), `.bak-20261027b`, `.bak-2026-10-27` on nl-pi | scp + `check_config` + one `docker restart homeassistant` |
Automations: installed, master switch `input_boolean.room_presence_automations` is OFF. IRK: phone + watch loaded 2026-10-27 (captured with an ESP32-C6 running ESPHome irk-capture, retained `presence/config/irk`); calibrate zones with it.
CYD cost, what the owner sees: the LVGL draw buffer went from 64 to 24 lines (frees 25 kB). Nothing else was cut: no animation, font, image or page removed. Idle sky animation unchanged (12 fps); at the active 30 fps sky target the measured rate is ~27 fps; BLE/WiFi coexistence forces WiFi modem sleep MIN_MODEM (touch commands may be a few ms slower). Risk: LVGL freezes (then reboots via the 5 s watchdog) if an allocation fails; opening a page needs ~15-25 kB, free is 52-62 kB. Tap through every page once and check `cyd/nl/panel/perf` (`hmin`, `hblk`).
Notes: the lightbar POWERON resets (09:59, 12:02) came from the PC USB port supply, not from BLE/CSI (no panic/WDT, heap flat). powermon: the board on the desk counts ~0.9 phantom pulses/s (about 324 W, `total_kwh` rises) because it is not on the meter; ADC polling is healthy with CSI (1980 samples per 10 s, max gap 23-30 ms with or without CSI); CSI can be paused with `csi_off` on `presence/<node>/cmd` (`csi_on` resumes).

## Honest limits
- CSI and link RSSI see MOTION. A person sitting still for minutes reads as "empty room" in those features; the BLE phone RSSI and PC idle carry the still case. Features help to tell a zone from an empty room, not to count people.
- BLE RSSI is noisy (+-5-10 dB), depends on how the phone is carried and on the body shadow; pcdesk/nlpi geometry is poor. Expect zone flips near borders; hysteresis helps, it does not make it exact.
- Without the phone IRK a phone lying still 5+ min looks stationary and is filtered, and neighbours' phones can win: calibrate with the same mode you will run in and re-calibrate when the IRK is loaded.
- Fingerprints are only as good as the 60 s calibration; re-run ALL zones after adding/moving an anchor or changing firmware. After a node restart wait 5 min (stationary filter).
- All anchors share the 2.4 GHz band with the AP and each other; ~20 Hz ping traffic per CSI node (4 nodes) is non-trivial airtime. If WiFi gets flaky, `csi_off` the less useful nodes first.
- The kitchen node is not at the kitchen yet; the kitchen zone currently rests on the radar hint only.
- No second person tracking, no direction, no identity beyond the phone IRK.

## Owner checklist (in order)
1. IRK (optional but recommended): follow "Getting the IRKs" below, publish retained `presence/config/irk`; check `sensor.rp_pcdesk_phone_loaded` = 1. Without it everything runs on the weaker fallback.
2. Calibrate (phone in the usual pocket, ~1 min each, nobody else moving in the room): `py -3.13 calibrate.py --zone kitchen`, `--zone work_desk`, `--zone pc_desk` (sit at the PC, hands off), `--zone bed`. Kitchen: wait until the powermon board hangs at the meter/kitchen, else skip it (radar hint still covers it). `--show --feat-note` prints the stored BLE levels and CSI/link features; `--clear <zone>` removes one. Link features need `ssh nl-pi-lan` to work from this PC.
3. Walk around and watch `sensor.yui_room_zone`, `sensor.rp_fp_zone`, `binary_sensor.yui_home`; tune `input_number.rp_fp_hysteresis` (BLE side) and `rp_feat_weight_db` (0 = BLE only, higher = trust CSI/link more).
4. Tap through all CYD pages once (LVGL heap check above).
5. Turn on `input_boolean.room_presence_automations` (dark only, not 00:30-07:30).

## IRK: optional but recommended
Without the phone's IRK the nodes can only use "strongest Apple advert of a non-stationary device" as the signal. That is unreliable: neighbours' phones, anyone visiting, and the owner's own phone as soon as it lies still for 5+ min (it then looks like the MacBooks and is filtered). The IRK lets every node recognise exactly the owner's phone (and watch) despite the rotating private address. Load it before calibrating.

### Getting the IRKs (phone and watch) - not executed overnight, follow the HA docs
1. Home Assistant docs "Private BLE Device" (https://www.home-assistant.io/integrations/private_ble_device/) describe how to read the IRK of an iPhone/Apple Watch from a Mac signed in to the same iCloud account (the owner's MacBooks): Keychain Access -> search `Bluetooth` -> the item named after the device (phone, watch; paired devices sync through iCloud keychain) -> Show password -> the stored value contains the IRK (base64 or hex, depending on macOS version; convert base64 with `echo '<value>' | base64 -d | xxd -p`).
2. Either byte order works: the nodes try both, so no need to reverse it. 32 hex characters per device; separators/`0x` are ignored.
3. Publish once, retained, from any machine that can reach the broker (labelled JSON; keep it on ONE line, the lightbar has a 512-byte inbound buffer):
   `mosquitto_pub -r -h 10.0.0.20 -u <user> -P <pass> -t presence/config/irk -m '{"phone":"<32hex>","watch":"<32hex>"}'`
   A plain hex string (old format) is accepted too: the first key is treated as the phone. Up to 4 labelled keys. To remove: publish an empty retained message (`-n`).
4. Check: in HA `sensor.rp_pcdesk_phone_loaded` = 1 and `sensor.rp_pcdesk_watch_loaded` = 1; `sensor.rp_<node>_irk_n` > 0 near the phone.

## Owner facts this design follows
- Phone: goes out with the owner 100 %. Watch: 90 %, and AT HOME IT LIES ON A TABLE (worn only outdoors; the owner takes it off for typing/gaming). So the watch is NEVER a zone signal; it only supports home/away.
- Static Apple devices: 2 MacBooks wedged under the PC desk board and an iPad on a desk arm mount. They advertise all day with a fixed RSSI. See "Stationary filter".
- Anchors: the lightbar ESP32-C3 sits under the PC desk in the far-left back corner next to a power strip and cable mess, shadowed by the owner's body when seated and right beside the Macs; nl-pi lies on the floor under the bed in front of a metal radiator among power adapters, with the TP-Link AX10 (2.4 GHz AP) next to it; LightController sits at the work bench; the NLDeskCYD display sits ON TOP of the PC desk and would be the best pc_desk anchor (BLE + CSI now run on it, see Current status). The room is one long rectangle: kitchen/door at one end, window and bed at the far end, PC desk and work bench on the opposite long walls near the bed end. Consequences: pcdesk and nlpi are nearly collinear and both distorted by metal and 2.4 GHz traffic, so never trust "strongest anchor wins": use the calibrated fingerprints. Calibrate with the same phone orientation you normally carry it in.


## Rollback
- Lightbar: OTA the rollback image (commands in section 2). LightController: `git apply -R` the patch (section 6) and reflash; its previous firmware is whatever the owner had built.
- nl-pi: `systemctl disable --now presence-ble`; broker user: restore `/etc/mosquitto/passwd.bak-blepresence`.
- HA: `/opt/homeassistant/config/packages/room_presence.yaml.bak-2026-10-27` is the package from before tonight's rework; to remove the feature entirely delete the package file and restore `configuration.yaml.bak-2026-10-27`, restart the container.
- Automations stay inert while `input_boolean.room_presence_automations` is off.

## 1. KitchenRadar
Live entities (verified): binary_sensor.kitchenradar_presence, sensor.kitchenradar_{detection,moving,still}_distance, _moving_energy, _still_energy. Used only as kitchen hint: kitchen = presence on AND detection_distance < `input_number.rp_kitchen_max_cm` (250). Calibrate by standing at kitchen / work desk / PC desk and watching the distance sensor. Unknown whether the radar also sees the desks.

## 2. BLE firmware (lightbar2mqtt, PC desk anchor) - FLASHED (BLE 08:38, + CSI 12:30)
- Repo `C:\Users\Ruanyouyi\Rpi4\lightbar2mqtt`, branch `ble-presence` (main untouched). `ble_presence.h/.cpp`, small hooks in `Lightbar.ino`, `mqtt.cpp` (+inbound buffer 512), `platformio.ini`.
- Passive scan (interval 100 ms / window 30 ms, ~30 % duty); Apple Continuity adverts (types 0x10/0x0C/0x0F); with IRKs loaded, private addresses are resolved with AES `ah()` (both byte orders). Publishes every 3 s (not retained) `presence/pcdesk/apple`:
  `{"scan":1,"apple_best":-61,"apple_avg":-66,"apple_n":9,"mov_best":-70,"mov_avg":-72,"mov_n":2,"static_n":3,"irk_n":2,"irk":[{"id":"phone","best":-58,"avg":-60,"n":4},{"id":"watch","best":-127,"avg":-127,"n":0}],"heap":98000,"up":55}` (-127 / n=0 = not heard). `apple_*` = every Apple advert, `mov_*` = only adverts not judged stationary, `irk[]` = the labelled owner devices (resolved devices are not part of `apple`'s filtering or `mov_*`).
- Controls: `on` / `off` to `presence/pcdesk/cmd`. IRKs: retained `presence/config/irk` (above), no reflash. Node name = `BLE_NODE_NAME` (default `pcdesk`).
- Stationary filter: per address, 30 s window means of the RSSI; if the means stay within 6 dB for at least 5 min and 8 windows the address is stationary. The mean RSSI is remembered as a "band" for 3 h, so the next rotated address (every ~15 min) of the same device within 3 dB is suppressed from its fourth advert on. A device that then moves (means differ by more than 6 dB) is released. Tracks 48 addresses per node. Same algorithm in `nlpi\stationary.py` (7 unit tests, `py -3.13 -I -m unittest` in `nlpi\`). Limits: a phone that lies still 5+ min looks stationary in fallback mode (IRK fixes that); after a reboot the filter needs 5 min; not implemented: "present continuously while the PC is locked".
- PLATFORM PIN: the current pioarduino platform (Arduino 3.3.9) gives a 1.196 MB baseline and BLE no longer fits the 1.25 MB OTA slot (1.375 MB). `platformio.ini` pins `espressif32@6.9.0` (Arduino 2.0.17), isolated PlatformIO core dir `C:\pio-lb`. Build from PowerShell/cmd (not Git Bash): `$env:PLATFORMIO_CORE_DIR='C:\pio-lb'; pio run -e esp32-c3-pro-mini-ota`.
- TRADE-OFF found by review: WiFi/BT coexistence aborts if WiFi modem sleep is off, so this build uses `WIFI_PS_MIN_MODEM` instead of `setSleep(false)`; the earlier fix for ping jitter / MQTT reconnects is undone on this branch. Watch the MQTT reconnect count after flashing; if it rises badly, send `off` to `presence/pcdesk/cmd` and/or roll back.
- Safety nets: OTA initialised before BLE; BLE skipped for one boot after a panic/WDT reset; scanner stops below 40 kB free heap, restarts above 50 kB.
- Images (gitignored, contain secrets): `C:\Users\Ruanyouyi\Rpi4\room-presence\firmware\lightbar2mqtt-ble-presence.bin` (sha256 above). Rollback `...\rollback\lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin` (907,968 B, sha256 146e0cca...). Caveat: what runs on the bar now was built Sep 6; the rollback bin is the same source rebuilt, not byte-identical.
- OTA (PowerShell; env `LIGHTBAR_OTA_PASSWORD` = OTA_PASSWORD from config.h; firewall rule for TCP 3233 per platformio.ini comments):
  ```
  cd C:\Users\Ruanyouyi\Rpi4\lightbar2mqtt
  $env:PLATFORMIO_CORE_DIR='C:\pio-lb'
  pio run -e esp32-c3-pro-mini-ota -t upload --upload-port 10.0.0.87
  ```
  Rollback: `py -3.13 C:\pio-lb\packages\framework-arduinoespressif32\tools\espota.py -i 10.0.0.87 -p 3232 --auth=%LIGHTBAR_OTA_PASSWORD% -f C:\Users\Ruanyouyi\Rpi4\room-presence\rollback\lightbar2mqtt-baseline-HEAD69893a0-core2.0.17.bin` (USB: `pio run -e esp32-c3-pro-mini -t upload`).
- Not run on hardware. Risks: BLE+WiFi on single-core C3; NimBLE 2.5.2 on Arduino 2.0.17 compiled but never executed; the parser/tracker C++ has no host test (no host compiler here), only a code review and equivalence to the tested Python reference.

## 3. PC idle publisher (live)
`pc-idle\pc_idle.py` -> `presence/pcdesk/idle` `{"idle":s,"locked":0|1}` every 10 s, `presence/pcdesk/status` online/offline (retained, LWT). Dedicated mosquitto user `pcpresence` (password only in gitignored `pc-idle/mqtt-pass.local`; nl-pi `/etc/mosquitto/passwd` backed up as `passwd.bak-2026-10-27`; the broker has no ACLs). Task `PresencePcIdle` (install or repair: `powershell -ExecutionPolicy Bypass -File pc-idle/install_task.ps1`; triggers at logon plus every 5 min as a self-heal, IgnoreNew keeps one copy; added 2026-10-28 after the task died on 10-27 and pc_desk went undetected). Stop: `Stop-ScheduledTask PresencePcIdle`; remove: `Unregister-ScheduledTask PresencePcIdle`.

## 4. Home Assistant (live)
- `/opt/homeassistant/config/packages/room_presence.yaml` (source `ha\room_presence.yaml`; `configuration.yaml` has `homeassistant: packages: !include_dir_named packages`, backup `configuration.yaml.bak-2026-10-27`). Redeploy: scp the file, `docker exec homeassistant python -m homeassistant --script check_config -c /config`, then `docker restart homeassistant` (MQTT/template entities cannot be reloaded without an API token; only automations reload via `ha/admin/reload_automations`).
- Entities: `sensor.yui_room_zone` = kitchen | work_desk | pc_desk | bed | home | away; `binary_sensor.yui_home`; `binary_sensor.room_zone_{kitchen,work_desk,pc_desk,bed,away}`; per node `sensor.rp_<pcdesk|workdesk|nlpi>_{signal,apple_best,apple_n,mov_best,mov_n,static_n,irk_avg,irk_n,irk_loaded,phone_loaded,watch_n,watch_loaded,heap}` (`irk_avg`/`irk_n` are the PHONE entry); `binary_sensor.rp_{phone_seen,watch_seen,nodes_alive,phone_irk_loaded,away_condition,pc_locked,pc_online}`; `sensor.rp_fp_zone`, `sensor.rp_fingerprints`; helpers `input_number.rp_{kitchen_max_cm,hysteresis_db,fp_hysteresis,min_rssi}`.
- Node signal = the phone's IRK-resolved average (-127 if not heard); with no phone IRK loaded, the strongest NON-stationary Apple advert (`mov_best`). Watch and stationary devices never count.
- Zone logic in order: PC unlocked and idle < 60 s -> pc_desk; `yui_home` off -> away; kitchen radar hint -> kitchen; all node signals below `rp_min_rssi` -> home; calibrated fingerprint zone (`rp_fp_zone`) if any; else the stronger of pcdesk/workdesk with hysteresis.
- Home/away: `binary_sensor.yui_home` = NOT `rp_away_condition`; away condition must hold 5 min: PC not in use AND phone IRK loaded AND at least one node alive AND phone not heard by any node AND (watch not heard OR `device_tracker.ephone` = not_home). If no node is alive or no phone IRK is loaded, only `device_tracker.ephone` not_home counts. `device_tracker.ephone` (mobile app) exists in the entity registry and is `home` now. The 5 min rule makes a briefly unheard phone harmless; a watch left on the table cannot keep the owner "home" once the app says not_home. Beyond the 3 min "seen" rule of the brief the phone gets the full 5 min before away because adverts are intermittent.
- VERIFIED tonight with fake MQTT messages through the broker (not retained, expired by themselves; no fingerprints existed): (a) phone -55 workdesk / -70 pcdesk, watch -40 and a MacBook at -30 at pcdesk -> `rp_workdesk_signal` -55, `rp_pcdesk_signal` -70, zone work_desk, home on; (b) no IRK, MacBook stationary (mov_n 0) + a walking phone -62 -> pcdesk signal -62, zone pc_desk; (c) IRKs loaded but nothing heard -> `rp_away_condition` turned on after 5 min, `yui_home` off, zone away; after the fake stopped everything returned to home. State read helper: `ssh nl-pi-lan "docker exec -i homeassistant python - rp_ yui_" < ha\ha_states.py`; fake generator: `py -3.13 ha\fake_test.py <phone_work|nophone_mac|none> <seconds>`.
- Automations (gated by the master switch, dark only, not 00:30-07:30, light not changed in the last 10 min): PC desk occupied -> light bar on 2900 K 55 %; empty 10 min -> off; work desk occupied -> light.channel_b on; empty 10 min -> off. Existing kitchen automations untouched. Do NOT enable before the BLE firmware is live.

## 5. nl-pi scanner and calibration
- `nlpi\nlpi_ble.py` (bleak service, topic `presence/nlpi/apple`, same JSON schema, labelled IRKs, stationary filter via `stationary.py`; note: the node is called `nlpi`, the zone `bed`). `ah()` was verified against the spec vector; labelled IRK parsing checked offline; the service itself has not run on the Pi.
- `calibrate.py` stores per-zone median signals (phone IRK entry, else non-stationary fallback) per node.

## 6. LightController port (flashed 08:45, + CSI 12:33; node `workdesk`)
- Patch: `C:\Users\Ruanyouyi\Rpi4\room-presence\lightcontroller-ble-presence.patch` (unified diff, `-p1`, made against the owner's working tree of 2026-10-27 01:30 including the uncommitted changes; `git apply --check` passes on it; the tree was not modified). It adds `src/ble_presence.{h,cpp}` (identical to the lightbar2mqtt files, built with `-D BLE_PUBLISH_FN_API`), publisher/subscribe/IRK hooks in `StatusReporter.h` (MQTT buffer 768, keepalive 60), BLE start in `main.cpp` (30 s after WiFi is up, MQTT only), and in `platformio.ini` NimBLE-Arduino 2.3, node name `workdesk` and passive-only NimBLE flags. It also replaces `WiFi.setSleep(false)` with `WIFI_PS_MIN_MODEM` (needed for coexistence; this undoes the "no modem sleep" choice for HomeSpan/MQTT stability, watch for HomeKit/MQTT drop-outs).
- Apply: `cd C:\Users\Ruanyouyi\LightController; git apply --check -p1 C:\Users\Ruanyouyi\Rpi4\room-presence\lightcontroller-ble-presence.patch; git apply -p1 C:\Users\Ruanyouyi\Rpi4\room-presence\lightcontroller-ble-presence.patch` (it only touches uncommitted-modified files and adds two new ones; nothing is staged or committed). Then in the gitignored `src/config.h` set `ENABLE_MQTT 1` plus `MQTT_USER`/`MQTT_PASS` (a dedicated broker user, e.g. the `blepresence` one that `install_nlpi.sh` creates; its password is in `/etc/presence-ble.env` on nl-pi). With `ENABLE_MQTT 0` BLE does not start (only the setSleep change applies).
- Build + flash (PowerShell, default PlatformIO core, no platform pin needed): `pio run -e ota -t upload` (HomeSpan OTA, password from `secrets.ini`). Built size on a scratch copy with MQTT on: flash 1,684,425 B = 85.7 % of the 1.9 MB slot, RAM 18.4 % static. Unknown until run: free heap with HomeSpan + web log + NimBLE (the scanner stops itself below 40 kB). Watch the topic `presence/workdesk/apple` (`heap` field) for 5 minutes, HomeKit responsiveness and the `lightctrl/online` topic.
- Rollback: `git apply -R -p1` the patch, delete `src/ble_presence.*` if left, reflash; or `off` to `presence/workdesk/cmd` to stop scanning without a reflash.
- The scratch build tree (`lc-port\work`, gitignored) holds a copy of `config.h`/`secrets.ini`.

## 7. Does the TP-Link AX10 AP expose per-client RSSI?
Not verified against the device (it was not located on the LAN by a title scan of the neighbour table, and nothing was logged into or changed). From general knowledge only: the Archer AX10 stock web UI lists associated wireless clients (Network/Wireless Statistics: MAC, packets) and I know of no documented local API with per-client RSSI; if it is in access-point mode, expect no usable signal data. The main APs may be UniFi (the gateway is 10.0.0.1): a UniFi controller does report per-client `rssi`/`signal` for the phone, which would be a real second signal source for the same room question but belongs to the AP the phone is attached to, not to a room. Treat as unverified; decide in daytime whether it is worth a look.

## 8. Live bring-up log (2026-10-27 daytime, owner authorised flashing)
- nl-pi: `install_nlpi.sh` ran clean (08:36); `presence-ble` active, `presence/nlpi/apple` publishes every 3 s. Rollback unchanged.
- lightbar (10.0.0.87): `pio run -e esp32-c3-pro-mini-ota -t upload` OK 08:38. Watched 5 min: heap 144,4xx stable, `mqtt_connects` 1, WiFi rssi -37..-44, `on`/`off` via `lightbar2mqtt/l2m_44BD8D4BCE78/0xa11ce0/command` worked and the bar was restored OFF, `sensor.rp_pcdesk_*` available in HA. At ~09:52 it restarted with reset_reason POWERON (power cycle, not a crash); BLE came back by itself.
- LightController (10.0.0.93): rollback image first: `rollback\lightcontroller-prepatch-2026-10-27.bin` (rebuild of the owner's pre-patch tree with `ENABLE_MQTT 0`, not byte-identical to what ran). Patch applied with `git apply` (no commit/stash), `src/config.h` (gitignored) now `ENABLE_MQTT 1`, `MQTT_USER "lcpresence"`; new mosquitto user `lcpresence` (password only in the gitignored config.h and `lc-port\lcpresence.pass.local`; broker `passwd` backed up as `/etc/mosquitto/passwd.bak-lcpresence`). Reviewed by `critic` (no blocking findings). `pio run -e ota -t upload` OK 08:45. Watched 400 s: `presence/workdesk/apple` heap 60.5 kB, `lightctrl/online` once (no reconnect), later HA `fan.wo_shi_fans` and `light.channel_b` available (HomeKit link fine). The controllability test through HA could not be done (no HA API token), it was judged by availability and the state having changed in HA after the flash (owner use). It restarted at ~10:03 (uptime), BLE came back; heap 54-59 kB, scanner stays above its 40 kB stop threshold. Rollback: flash the prepatch bin through HomeSpan OTA (same espota route) or `git apply -R`.
- NLDeskCYD (10.0.0.82): first attempt rolled back (heap 140 -> 61 kB); redone 13:06 with a smaller LVGL draw buffer, see Current status. Finding: linking NimBLE/BT stops Arduino's start-up release of the BT controller memory, so free heap after boot fell from 140 kB to 61 kB before the scanner even started (the scanner refuses below 70 kB), which would put LVGL (alloc failure = frozen UI) at risk. Panel is back on the build of HEAD 1a663a7 (`rollback
ldeskcyd-HEAD1a663a7.bin`), heap 140 kB, MQTT data updating. The owner's NLDeskCYD repo is untouched. Possible later: drop pages/sky buffers or use a different board.
- OTA to the CYD from this PC FAILS ("No response from device": the device cannot connect back, the PC firewall only allows python from some peers); it works from nl-pi: copy `tools\espota_slow.py` + the bin to nl-pi and `python3 espota_slow.py -i 10.0.0.82 -p 3232 --auth=<secrets.ini ota_password> -f x.bin`. Also: the default PlatformIO `espressif32` platform was overwritten by the pioarduino one (LightController), so NLDeskCYD no longer builds in the default core; use `PLATFORMIO_CORE_DIR=C:\pio-cyd` with `platform = espressif32@7.1.0` in a scratch copy (`C:\cyd-work`, scripts in `tools\`).
- Kitchen anchor: evaluated, not built. (1) The NeoPixel D1 mini bit-bangs 173 WS2812B on GPIO4 (interrupts off ~5 ms per frame) and the ESP8266 SDK documents promiscuous mode as incompatible with a connected station, so sniffing risks the HA link and the main light function. (2) A sleeping iPhone sends sparse uplink frames, so most samples would be -100 and spoil the fingerprint. (3) The radar already gives the kitchen hint. Recommended instead: a 3 EUR ESP32-C3 in the kitchen running the lightbar firmware (node name `kitchen`; add it to the HA package and re-calibrate). For a future Wi-Fi approach: the likely iPhone is "R" at 10.0.0.121 with private MAC e2:8d:55:f0:12:0d (mDNS `_companion-link`, not verified as the owner's phone). KitchenLight, powermon, basil, FW13 untouched.
- Nothing new was added to the HA package (no new nodes), no HA restart today.
- Next for the owner: load the IRK, calibrate (`calibrate.py`), then enable `input_boolean.room_presence_automations`.

## Decisions taken (safe defaults)
- No flashing, no apt installs on nl-pi, one HA restart (after check_config), backups `*.bak-2026-10-27`.
- Toolchain pin to Arduino 2.0.17 + isolated core dir for the lightbar (image otherwise exceeds the OTA slot).
- Automations installed but disabled by default.
- `pc_active` (PC unlocked, idle < 60 s) overrides away and also prevents the away condition: someone is evidently at the PC.
- The reported zone when at home but nothing heard is `home` (previously `away_or_elsewhere`).
- The node label for the bed anchor stays `nlpi`.
- Skipped: NLDeskCYD, ESP8266 sniffing, host-compiled C++ tests (no compiler on this machine).

## Calibrating from the phone (calibration service)

- `nlpi/calibrate_svc.py` runs on nl-pi as `presence-calibrate.service` (install: `nlpi/install_calibrate.sh`, env shared with
  `presence-ble`). It listens on MQTT `presence/calibrate/cmd` (`{"action":"start","zone":..,"seconds":60}` / `clear` / `cancel`),
  records with `calibrate.record_zone`, and publishes retained progress to `presence/calibrate/status`.
- It is the authority for fingerprints: `/var/lib/presence-calibrate/fingerprints.json` on nl-pi (seeded from the retained
  `presence/config/fingerprints` at first start). The PC `calibrate.py` keeps its own `fingerprints.json`: do not mix the two.
- HA: dashboard view `定位校準` in `dashboards/house.yaml` (source `ha/dashboard_calibrate_view.yaml`), buttons
  `button.rp_calibrate_<zone>`, `..._cancel`, `..._clear_<zone>`, sensors `sensor.rp_calibrate_status` / `_progress`.
- After editing the `mqtt:` section of the HA package, publish anything to `ha/admin/reload_mqtt` (no token needed).
- Logs: `journalctl -u presence-calibrate`. Rollback: `systemctl disable --now presence-calibrate`; HA backups `*.bak-20261027e`.

## Power log for later analysis (dinner-time high wattage)

- `nlpi/powermon_log.py` runs on nl-pi as `powermon-log.service` (installed to `/opt/powermon-log`). It logs every
  `power/powermon/state` (~10 s) with the latest `pc/telemetry/state` watts to `/var/lib/powermon-log/power-YYYY-MM-DD.csv`
  (`ts,watts,total_kwh,swing_mv,peak_mv,rssi,pc_w,pc_gaming,pulses,rejected`, ~700 KB/day).
- Any reading >= `EVENT_W` (1000 W) opens an event, closed after 3 readings below: `events.csv`
  (`start,end,secs,peak_w,mean_w,kwh_est,pc_w_mean`). House load minus `pc_w` ~ the kitchen/other load.
- Analyse: `scp nl-pi-lan:/var/lib/powermon-log/*.csv .`; zone context (kitchen) is in the HA recorder (`sensor.yui_room_zone`).

## Position labelling site (ground truth for analysis)

- `nlpi/label_site.py` as `label-site.service` on nl-pi: `http://10.0.0.20:8097/` (LAN only, bound to 10.0.0.20, no auth; 8099
  is taken by another service). Four zone buttons; a press stores the last 15 s of phone / watch (IRK-labelled), non-stationary
  BLE and CSI per node in SQLite `/var/lib/presence-labels/labels.db` (`labels`, `samples`), undo button, CSV export
  `/api/export.csv`, MQTT event `presence/label/event`.
- Use: stand in a zone, press its button, repeat at different times/poses; later compare labels with `sensor.yui_room_zone`
  history and re-fit fingerprints.

## Zone history log (back-tracking)

- HA automation `rp_zone_to_mqtt` publishes retained `presence/zone/state` (zone, fp zone, home, PC online/locked/idle, kitchen radar,
  `light.channel_b` and light bar state + last-change epoch, ESPHome link motion) every 10 s and on change. `nlpi/zone_log.py`
  (`zone-log.service`) writes `/var/lib/zone-log/zone-v3-YYYY-MM-DD.csv` every 10 s: zone, fp zone, the context columns (PC, radar,
  powermon W, lights incl. kitchen light / NeoPixel, gaming PC), mean phone/watch dBm per node and the 10 s CSI motion/still means per node (the
  features the matcher uses), and `transitions.csv` (every zone change). `zone-YYYY-MM-DD.csv` (v1) and `zone-v2-*` (live weak_label) are old schemas.
- Weak labels (passive training data, no button presses): rules and thresholds in the `tools/weak_labels.py` docstring (applied offline, not by the logger) (pc_desk = PC
  unlocked and idle < 60 s; kitchen = radar < 250 cm or powermon >= 1000 W; work_desk = `light.channel_b` on + PC inactive + workdesk
  phone above its median; bed = 00:30-07:30, PC locked, phone RSSI stable 10 min). Empty = unsure, two zones = conflict = empty.
  Train/evaluate on them: `python tools/fp_eval.py weak export.csv zone-v3-*.csv`. Tests: `python tools/test_weak_labels.py`,
  `python nlpi/test_zone_log.py`.
- Where am I now: `sensor.yui_room_zone`; history: those CSVs (or the HA recorder / logbook).
