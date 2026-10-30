# bathroom zone handoff (nl-pi, 2026-10-30)
files_changed (backups: <file>.bak-20261030-bathroom, same dir):
/opt/presence-calibrate/calibrate.py            ZONES += "bathroom" (CRLF kept)
/opt/label-site/label_site.py                   ZONES += ("bathroom","浴室/門口")
/opt/homeassistant/config/packages/room_presence.yaml  (CRLF kept)
/opt/homeassistant/config/dashboards/house.yaml  calibrate + clear buttons, saved-fingerprint list
zone_log.py: no change needed (zone is a free column). calibrate_svc.py uses cal.ZONES (no change).
Patch: /Users/ruanyouyi/rpi4/room-presence-bathroom-20261030.patch (git apply --check + apply to backups reproduces deployed files byte-identical; `git apply` from repo root, paths relative to /opt/).

Package changes: input_number.rp_bathroom_radar_min_cm (0=off, 0-600 step 10); buttons rp_calibrate_bathroom / rp_calibrate_clear_bathroom;
binary_sensor.room_zone_bathroom (delay_on 5s, off 30s); binary_sensor.rp_bathroom_radar_hint (radar on AND min>0 AND dist>=min, off-delay 30s);
rp_kitchen_radar_hint now also requires dist < min when min>0; yui_room_zone: `elif bath -> bathroom` after kitchen, dwell list + bathroom,
pc_desk hold gets `and not bath`, bathroom_radar attr; rp_zone_reason (+ 浴室雷達 line, attr); rp_night_bed requires bathroom hint off.
Fingerprint 'bathroom' passes straight through (no kitchen evidence needed). With min=0 behaviour of all existing zones is unchanged.

commands_run:
check_config (docker exec homeassistant python -m homeassistant --script check_config -c /config) | 0 | "Testing configuration at /config", no errors (re-run after CRLF restore: 0)
yaml.safe_load house.yaml in container | 0 | dash ok
py_compile calibrate.py label_site.py | 0
systemctl restart presence-calibrate label-site; docker restart homeassistant | 0 | is-active: presence-calibrate/label-site/zone-log all active
curl http://10.0.0.20:8097/ | grep 浴室/門口 | match found
ha_states.py bathroom yui_room_zone | button.rp_calibrate_bathroom unknown, button.rp_calibrate_clear_bathroom unknown, input_number.rp_bathroom_radar_min_cm 0.0,
  binary_sensor.room_zone_bathroom off, binary_sensor.rp_bathroom_radar_hint off, sensor.yui_room_zone home, rp_zone_reason 指紋：pc_desk
git apply --check /tmp/b.patch on backups, then cmp with deployed | all 4 same
tests_modified: false
not_verified: house.yaml dashboard rendered in browser (YAML parses only); actual calibration/classification of bathroom (needs owner visits); HA log has unrelated pre-existing ERRORs (esphome translation, bluetooth NET_ADMIN).
assumptions: HA core restarted (new input_number needs it; ~1 min downtime); CRLF in calibrate.py/room_presence.yaml was briefly stripped by my python patch and restored (sed), verified by identical diff.

Radar coverage finding (zone-log v3 10-27..10-30 + HA recorder 30k samples): sensor.kitchenradar_detection_distance min 31 cm, max 127 cm, mean 101;
buckets/25cm: 25:104, 50:1313, 75:12352, 100:14267, 125:2069, nothing >=150. moving/still distance max 150 cm (30102 samples). zone-log radar-on rows: 960, all 50-150 cm.
=> radar never reports beyond ~1.5 m; either bathroom/door area is outside its range/gates, or detection there is indistinguishable from kitchen counter.
So min_cm is left 0. Do NOT set it blindly: only useful if after calibration visits you see radar on with dist >= ~130 cm while in the bathroom
(watch sensor.kitchenradar_detection_distance while standing there); then set e.g. 140. Otherwise bathroom relies on BLE/CSI fingerprint only.

Owner calibration steps: stand/walk inside the bathroom/door area (phone in pocket, normal use) and press dashboard 定位校準 -> "校準：浴室/門口" (60 s each);
repeat ~3 visits at different times/positions (inside bathroom, doorway, near air fryer), then check 除錯 view that fp bathroom distance is lowest there.
Label-site (http://10.0.0.20:8097) now lists 浴室/門口 for weak labels. Also recalibrate nothing else. room_presence_automations untouched (OFF); no bathroom light automations; no firmware.
