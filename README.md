**English** · [繁體中文](README.zh-TW.md)

# Room presence (WiFi + BLE device-free / device-based room location)

Question answered: which zone of the room is the owner in - KITCHEN / WORK DESK / PC DESK / BED / home (place unknown) / away.

## At a glance

| | |
|---|---|
| **Problem** | Which zone of the room the owner is in: kitchen, work desk, PC desk, bed, home (place unknown), away. |
| **What I built** | Two first-class signal families, fused in HA (`ha/room_presence.yaml`) by nearest calibrated fingerprint: WiFi sensing with no device on the person (CSI + link RSSI), and the BLE RSSI of the owner's phone per anchor. |
| **Result** | `sensor.yui_room_zone` in Home Assistant. Zone accuracy with BLE only 0.82, CSI only 0.48 (0.62 with motion features only): 85 manual labels, 4 zones, leave-one-visit-out. |
| **Stack** | ESP32-C3 (lightbar2mqtt, LightController, powermon), NLDeskCYD ESP32 panel, ESPHome D1 minis, Raspberry Pi, Home Assistant, MQTT. |

## How it works

```
BLE phone RSSI --+
WiFi CSI --------+- ESP32 / Pi anchors -- MQTT --> Home Assistant
PC idle ---------+                                nearest fingerprint
                                                        |
                                             sensor.yui_room_zone
```

- Each anchor ESP32 pings the gateway at ~20 Hz, reads the per-subcarrier channel amplitude (L-LTF) of the replies and publishes a 1 s summary ~2 Hz to `presence/<node>/csi`.
- The ESPHome kitchenradar and NeoPixel publish their WiFi RSSI every 1 s; the CSI `motion` value becomes the 10 s mean `sensor.rp_link_<node>_motion`, the link RSSI the 20 s standard deviation (dB).
- Every anchor publishes `presence/<node>/apple` every 3 s: its BLE RSSI of the owner's phone is a position fingerprint.
- `calibrate.py --zone <z>` stores, per zone, the median BLE phone RSSI per node plus the median CSI/link features.
- HA picks the zone with the smallest RMS distance (BLE in dB, features in sigma x `input_number.rp_feat_weight_db`, default 6 dB; unavailable features are skipped).

## Design
Two first-class signal families, fused in HA (`ha/room_presence.yaml`) by nearest calibrated fingerprint:
1. WiFi sensing, no device on the person needed.
   - CSI: each anchor ESP32 pings the gateway at ~20 Hz, reads the per-subcarrier channel amplitude (L-LTF) of the replies and publishes a 1 s summary (motion, still, mean, rssi, rssi_sd, n, ok) ~2 Hz to `presence/<node>/csi`. A body moving in the room disturbs the channel; the HA statistics sensor `sensor.rp_link_<node>_motion` is the 10 s mean of `motion` (per-mille).
   - Link RSSI: the ESPHome kitchenradar and NeoPixel publish their WiFi RSSI every 1 s; `sensor.rp_link_kitchenradar_motion` / `_neopixel_motion` are the 20 s standard deviation (dB).
2. BLE: the owner's phone (Apple Continuity adverts) seen by every anchor; its RSSI per anchor is a position fingerprint. Nodes publish `presence/<node>/apple` every 3 s. With the phone IRK loaded only the owner's phone (rotating private address resolved) counts; without it the strongest NON-stationary Apple advert is used (MacBooks/iPad under the desk are filtered as stationary after 5 min).
3. Extra evidence: PC idle time/lock (`PresencePcIdle` scheduled task -> `presence/pcdesk/idle`; unlocked and idle < 60 s = pc_desk) and the kitchen radar (presence + distance < `rp_kitchen_max_cm` = kitchen hint).

Fusion: `calibrate.py --zone <z>` stores, per zone, the median BLE phone RSSI per node plus the median CSI/link features (the quiet-vs-motion levels at that spot). HA picks the zone with the smallest RMS distance (BLE in dB, features in sigma x `input_number.rp_feat_weight_db`, default 6 dB; unavailable features are skipped, hysteresis `rp_fp_hysteresis` 2.5 dB). Order in `yui_room_zone`: PC active -> pc_desk; `yui_home` off -> away; kitchen radar hint -> kitchen; all BLE below `rp_min_rssi` -> home; fingerprint zone; fallback stronger of pcdesk/workdesk.

## What the measurements showed

At 13:28-13:32 an owner sitting at the PC was shown "kitchen" (1800 W + light). Kitchen evidence is the radar only (`rp_kitchen_radar_hint`); powermon >= 1000 W and the kitchen light count only while the radar saw someone within the last 2 min (`rp_kitchen_radar_recent`). Replay `analysis/a10_sim_fix.py` (`analysis/out_a10_kitchen_radar_only.txt`): kitchen rows 603 -> 486, 117 rows -> home, zone accuracy vs weak labels unchanged.

CSI-only zone accuracy (`tools/fp_eval.py cv analysis/data/export.csv --cv visits --purge-s 15`, 85 manual labels, 4 zones, leave-one-visit-out): BLE only 0.82; CSI weighted up to w = 6 dB 0.48 (motion features only 0.62); so CSI alone is clearly worse than BLE with the current features.

pcdesk reset 17:13 on 10-27 was a board reset (brownout/USB supply), not a stuck publish; the old guard then kept BLE off for the whole boot. Fixed by the deferred start.

workdesk (LightController, RSSI mean -73.6 dBm, the weakest link; ping from `<server>` avg 25 ms / max 66 ms vs 8 ms for kitchenradar): `light.channel_b` (the HomeKit light on the same board) is unavailable in the same windows as the presence outages, ESPHome kitchenradar / neopixel never were, other presence nodes were fine. So the whole board leaves the network, not only the presence stream; it pre-dates the presence firmware (owner). Not yet separable into reboot vs Wi-Fi loss: sensor `sensor.rp_<node>_uptime` (new, from the `/ble` status) shows it at the next outage.

## Anchor map
The room is one long rectangle: kitchen/door at one end, window and bed at the far end, PC desk and work bench on opposite long walls near the bed end.
| Node | Where | Hardware |
|---|---|---|
| `pcdesk` | under the PC desk, far-left back corner, next to a power strip, shadowed by the body when seated, right beside the MacBooks | lightbar2mqtt ESP32-C3 |
| `cyd` | ON TOP of the PC desk (best pc_desk anchor, line of sight to the seated person) | NLDeskCYD ESP32 panel |
| `workdesk` | work bench | LightController ESP32-C3 |
| `nlpi` | on the floor under the bed in front of a metal radiator, next to the TP-Link AX10 (2.4 GHz AP); BLE only | Raspberry Pi |
| `kitchen` | powermon ESP32-C3 at the electricity meter / kitchen; TODAY the board still sits at the PC (not on the meter): do not calibrate zone `kitchen` with it until it is installed | powermon C3 |
| links | kitchenradar (`<kitchenradar-ip>`), neopixel (`<neopixel-ip>`) WiFi RSSI only; kitchenradar also gives the radar hint | ESPHome D1 minis |
`pcdesk` and `nlpi` are nearly collinear and both distorted by metal and 2.4 GHz traffic: never trust "strongest anchor wins", use the fingerprints.

## Owner facts this design follows
- Phone: goes out with the owner 100 %. Watch: 90 %, and AT HOME IT LIES ON A TABLE (worn only outdoors; the owner takes it off for typing/gaming). So the watch is NEVER a zone signal; it only supports home/away.
- Static Apple devices: 2 MacBooks wedged under the PC desk board and an iPad on a desk arm mount. They advertise all day with a fixed RSSI. See "Stationary filter".
- Anchors: the lightbar ESP32-C3 sits under the PC desk in the far-left back corner next to a power strip and cable mess, shadowed by the owner's body when seated and right beside the Macs; `<server>` lies on the floor under the bed in front of a metal radiator among power adapters, with the TP-Link AX10 (2.4 GHz AP) next to it; LightController sits at the work bench; the NLDeskCYD display sits ON TOP of the PC desk and would be the best pc_desk anchor (BLE + CSI now run on it, see Current status). The room is one long rectangle: kitchen/door at one end, window and bed at the far end, PC desk and work bench on the opposite long walls near the bed end. Consequences: pcdesk and nlpi are nearly collinear and both distorted by metal and 2.4 GHz traffic, so never trust "strongest anchor wins": use the calibrated fingerprints. Calibrate with the same phone orientation you normally carry it in.

## Current status (2026-10-27 ~14:50)
All firmware images: `firmware/` (contain secrets, gitignored); rollback images: `rollback/`. OTA from this PC fails (firewall: the device cannot connect back) - always OTA from `<server>`. HA package `ha/room_presence.yaml` deployed 13:13; `sensor.yui_room_zone` is the result entity. The master switch `input_boolean.room_presence_automations` is OFF.
Device table, 2026-10-28 changes, rollback steps, owner checklist and bring-up log: [Operations](docs/operations.md).

## Limits and open items
- CSI and link RSSI see MOTION. A person sitting still for minutes reads as "empty room" in those features; the BLE phone RSSI and PC idle carry the still case. Features help to tell a zone from an empty room, not to count people.
- BLE RSSI is noisy (+-5-10 dB), depends on how the phone is carried and on the body shadow; pcdesk/nlpi geometry is poor. Expect zone flips near borders; hysteresis helps, it does not make it exact.
- Without the phone IRK a phone lying still 5+ min looks stationary and is filtered, and neighbours' phones can win: calibrate with the same mode you will run in and re-calibrate when the IRK is loaded.
- Fingerprints are only as good as the 60 s calibration; re-run ALL zones after adding/moving an anchor or changing firmware. After a node restart wait 5 min (stationary filter).
- All anchors share the 2.4 GHz band with the AP and each other; ~20 Hz ping traffic per CSI node (4 nodes) is non-trivial airtime. If WiFi gets flaky, `csi_off` the less useful nodes first.
- The kitchen node is not at the kitchen yet; the kitchen zone currently rests on the radar hint only.
- No second person tracking, no direction, no identity beyond the phone IRK.
- Known open: the loop-WDT is not fault-injected; a NimBLE host-task wedge is now bounded by it on the lightbar only (cyd / LightController / powermon still have the 200 ms try-lock but no loop WDT).

## Build / run
Calibrate one zone at a time (phone in the usual pocket, ~1 min each, nobody else moving in the room):
```
py -3.13 calibrate.py --zone kitchen
py -3.13 calibrate.py --zone work_desk
py -3.13 calibrate.py --zone pc_desk
py -3.13 calibrate.py --zone bed
```
`--show --feat-note` prints the stored BLE levels and CSI/link features; `--clear <zone>` removes one. Link features need `ssh <server>` to work from this PC.
Redeploy HA: scp `ha\room_presence.yaml` to `/opt/homeassistant/config/packages/`, `docker exec homeassistant python -m homeassistant --script check_config -c /config`, then `docker restart homeassistant`.
Unit tests: `py -3.13 -I -m unittest` in `nlpi\` (7 tests); `python tools/test_weak_labels.py`; `python nlpi/test_zone_log.py`.
Node firmware, OTA routes and build dirs: [Firmware and nodes](docs/firmware.md).

## Repo layout
- `analysis/` - replay scripts and outputs for the kitchen-evidence fix (`a10_sim_fix.py`, `out_a10_*.txt`).
- `csi/` - CSI presence module (`csi_presence.cpp/.h`).
- `ha/` - HA package `room_presence.yaml`, dashboard view sources, `fake_test.py`, `ha_states.py`, `test_fp_features.py`.
- `lc-port/` - LightController CSI hooks diff and `patch_cyd.py`.
- `nlpi/` - services for the Pi: BLE scanner, calibration service, label site, zone log, power log, unit tests.
- `pc-idle/` - PC idle publisher and the scheduled-task installer.
- `tools/` - build/OTA helpers and evaluation scripts (`fp_eval.py`, `weak_labels.py`, `espota_slow.py`).
- `calibrate.py` and the patches at the root: `lightcontroller-ble-presence.patch`, `ble-watchdog.diff`, `ble-presence-guard.diff`, `cyd-port.diff`, `cyd-port2.diff.txt`, `ping_before_ps.txt`, `ping_after_ps.txt`.

## Docs
- [Operations: status and rollback](docs/operations.md) - deployment state, rollback, owner checklist, bring-up log.
- [Firmware and nodes](docs/firmware.md) - per-node firmware, OTA routes, the AX10 RSSI question.
- [Home Assistant and data logs](docs/home-assistant.md) - entities, IRK, calibration service, label site, zone log, power log.
- [Prior art](docs/prior-art.md) - what was read and what was not copied yet.

## License
No LICENSE file in this repo.
