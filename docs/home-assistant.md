**English** · [繁體中文](home-assistant.zh-TW.md)

# Home Assistant and data logs

## IRK: optional but recommended
Without the phone's IRK the nodes can only use "strongest Apple advert of a non-stationary device" as the signal. That is unreliable: neighbours' phones, anyone visiting, and the owner's own phone as soon as it lies still for 5+ min (it then looks like the MacBooks and is filtered). The IRK lets every node recognise exactly the owner's phone (and watch) despite the rotating private address. Load it before calibrating.

### Getting the IRKs (phone and watch) - not executed overnight, follow the HA docs
1. Home Assistant docs "Private BLE Device" (https://www.home-assistant.io/integrations/private_ble_device/) describe how to read the IRK of an iPhone/Apple Watch from a Mac signed in to the same iCloud account (the owner's MacBooks): Keychain Access -> search `Bluetooth` -> the item named after the device (phone, watch; paired devices sync through iCloud keychain) -> Show password -> the stored value contains the IRK (base64 or hex, depending on macOS version; convert base64 with `echo '<value>' | base64 -d | xxd -p`).
2. Either byte order works: the nodes try both, so no need to reverse it. 32 hex characters per device; separators/`0x` are ignored.
3. Publish once, retained, from any machine that can reach the broker (labelled JSON; keep it on ONE line, the lightbar has a 512-byte inbound buffer):
   `mosquitto_pub -r -h <server-ip> -u <user> -P <pass> -t presence/config/irk -m '{"phone":"<32hex>","watch":"<32hex>"}'`
   A plain hex string (old format) is accepted too: the first key is treated as the phone. Up to 4 labelled keys. To remove: publish an empty retained message (`-n`).
4. Check: in HA `sensor.rp_pcdesk_phone_loaded` = 1 and `sensor.rp_pcdesk_watch_loaded` = 1; `sensor.rp_<node>_irk_n` > 0 near the phone.

## 4. Home Assistant (live)
- `/opt/homeassistant/config/packages/room_presence.yaml` (source `ha\room_presence.yaml`; `configuration.yaml` has `homeassistant: packages: !include_dir_named packages`, backup `configuration.yaml.bak-2026-10-27`). Redeploy: scp the file, `docker exec homeassistant python -m homeassistant --script check_config -c /config`, then `docker restart homeassistant` (MQTT/template entities cannot be reloaded without an API token; only automations reload via `ha/admin/reload_automations`).
- Entities: `sensor.yui_room_zone` = kitchen | work_desk | pc_desk | bed | home | away; `binary_sensor.yui_home`; `binary_sensor.room_zone_{kitchen,work_desk,pc_desk,bed,away}`; per node `sensor.rp_<pcdesk|workdesk|nlpi>_{signal,apple_best,apple_n,mov_best,mov_n,static_n,irk_avg,irk_n,irk_loaded,phone_loaded,watch_n,watch_loaded,heap}` (`irk_avg`/`irk_n` are the PHONE entry); `binary_sensor.rp_{phone_seen,watch_seen,nodes_alive,phone_irk_loaded,away_condition,pc_locked,pc_online}`; `sensor.rp_fp_zone`, `sensor.rp_fingerprints`; helpers `input_number.rp_{kitchen_max_cm,hysteresis_db,fp_hysteresis,min_rssi}`.
- Node signal = the phone's IRK-resolved average (-127 if not heard); with no phone IRK loaded, the strongest NON-stationary Apple advert (`mov_best`). Watch and stationary devices never count.
- Zone logic in order: PC unlocked and idle < 60 s -> pc_desk; `yui_home` off -> away; kitchen radar hint -> kitchen; all node signals below `rp_min_rssi` -> home; calibrated fingerprint zone (`rp_fp_zone`) if any; else the stronger of pcdesk/workdesk with hysteresis.
- Home/away: `binary_sensor.yui_home` = NOT `rp_away_condition`; away condition must hold 5 min: PC not in use AND phone IRK loaded AND at least one node alive AND phone not heard by any node AND (watch not heard OR `device_tracker.ephone` = not_home). If no node is alive or no phone IRK is loaded, only `device_tracker.ephone` not_home counts. `device_tracker.ephone` (mobile app) exists in the entity registry and is `home` now. The 5 min rule makes a briefly unheard phone harmless; a watch left on the table cannot keep the owner "home" once the app says not_home. Beyond the 3 min "seen" rule of the brief the phone gets the full 5 min before away because adverts are intermittent.
- VERIFIED tonight with fake MQTT messages through the broker (not retained, expired by themselves; no fingerprints existed): (a) phone -55 workdesk / -70 pcdesk, watch -40 and a MacBook at -30 at pcdesk -> `rp_workdesk_signal` -55, `rp_pcdesk_signal` -70, zone work_desk, home on; (b) no IRK, MacBook stationary (mov_n 0) + a walking phone -62 -> pcdesk signal -62, zone pc_desk; (c) IRKs loaded but nothing heard -> `rp_away_condition` turned on after 5 min, `yui_home` off, zone away; after the fake stopped everything returned to home. State read helper: `ssh <server> "docker exec -i homeassistant python - rp_ yui_" < ha\ha_states.py`; fake generator: `py -3.13 ha\fake_test.py <phone_work|nophone_mac|none> <seconds>`.
- Automations (gated by the master switch, dark only, not 00:30-07:30, light not changed in the last 10 min): PC desk occupied -> light bar on 2900 K 55 %; empty 10 min -> off; work desk occupied -> light.channel_b on; empty 10 min -> off. Existing kitchen automations untouched. Do NOT enable before the BLE firmware is live.

## Calibrating from the phone (calibration service)

- `nlpi/calibrate_svc.py` runs on `<server>` as `presence-calibrate.service` (install: `nlpi/install_calibrate.sh`, env shared with
  `presence-ble`). It listens on MQTT `presence/calibrate/cmd` (`{"action":"start","zone":..,"seconds":60}` / `clear` / `cancel`),
  records with `calibrate.record_zone`, and publishes retained progress to `presence/calibrate/status`.
- It is the authority for fingerprints: `/var/lib/presence-calibrate/fingerprints.json` on `<server>` (seeded from the retained
  `presence/config/fingerprints` at first start). The PC `calibrate.py` keeps its own `fingerprints.json`: do not mix the two.
- HA: dashboard view `定位校準` in `dashboards/house.yaml` (source `ha/dashboard_calibrate_view.yaml`), buttons
  `button.rp_calibrate_<zone>`, `..._cancel`, `..._clear_<zone>`, sensors `sensor.rp_calibrate_status` / `_progress`. Debug view `定位除錯` in the same file (see Update 2026-10-28 in [Operations](operations.md)).
- After editing the `mqtt:` section of the HA package, publish anything to `ha/admin/reload_mqtt` (no token needed).
- Logs: `journalctl -u presence-calibrate`. Rollback: `systemctl disable --now presence-calibrate`; HA backups `*.bak-20261027e`.

## Power log for later analysis (dinner-time high wattage)

- `nlpi/powermon_log.py` runs on `<server>` as `powermon-log.service` (installed to `/opt/powermon-log`). It logs every
  `power/powermon/state` (~10 s) with the latest `pc/telemetry/state` watts to `/var/lib/powermon-log/power-YYYY-MM-DD.csv`
  (`ts,watts,total_kwh,swing_mv,peak_mv,rssi,pc_w,pc_gaming,pulses,rejected`, ~700 KB/day).
- Any reading >= `EVENT_W` (1000 W) opens an event, closed after 3 readings below: `events.csv`
  (`start,end,secs,peak_w,mean_w,kwh_est,pc_w_mean`). House load minus `pc_w` ~ the kitchen/other load.
- Analyse: `scp <server>:/var/lib/powermon-log/*.csv .`; zone context (kitchen) is in the HA recorder (`sensor.yui_room_zone`).

## Position labelling site (ground truth for analysis)

- `nlpi/label_site.py` as `label-site.service` on `<server>`: `http://<server-ip>:8097/` (LAN only, bound to `<server-ip>`, no auth; 8099
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
