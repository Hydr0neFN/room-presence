status: DONE
commits: none (no repo); patch /Users/ruanyouyi/rpi4/room-presence-csi-shadow-20261030.patch
tests: 25/25 (test_csi.py), fail set unchanged
findings: 0
open: owner calibration visits (see Proposed owner steps); promotion after >=7 days

# CSI shadow zoning - phase report (sub-main, 2026-10-30)

## Step 1 - CSI data path (discovered on nl-pi)
- MQTT `presence/<node>/csi`, nodes cyd, kitchen, pcdesk, workdesk (nlpi has no CSI), ~every 3 s, retained no. Payload:
  `{on, ok, n, mean, motion, mstd, still, still_n, gstd:[8 group stds], rssi, rssi_sd, heap, up, rr}`. motion/still in per-mille;
  ok=0 = no usable window; still=-1 = firmware has no baseline yet; `up` = node uptime s (drops on reboot), `rr` reset reason.
- HA: package `rp_<node>_csi_*` MQTT sensors (expire 30 s); `sensor.rp_link_<node>_motion` = statistics mean over 10 s.
  Matcher feat names: `csi_<node>_motion` -> rp_link_<node>_motion, `csi_<node>_still` -> rp_<node>_csi_still; `rp_feat_weight_db`=0 so unused.
- calibrate.py (`record_zone`): subscribes presence/+/apple + presence/+/csi, ignores first 5 s, per node stores median of the 10 s
  sliding means of motion and median of still (ok==1), plus link_* from HA recorder; stored in /var/lib/presence-calibrate/fingerprints.json
  (`feat`) and published retained `presence/config/fingerprints` {nodes, zones, feat:{names,zones,scale}}. Raw (no baseline).
  Status of a recording: retained `presence/calibrate/status` {state recording|done|idle|error, zone, seconds, elapsed, message, zones{}}.
  Commands: `presence/calibrate/cmd` {action start|clear|cancel, zone, seconds}. No bathroom fingerprint stored yet (4 zones only).
- zone-log (/var/lib/zone-log/zone-v3-DATE.csv, every 10 s, 45 cols): ts, zone (live yui_room_zone), fp_zone (BLE fingerprint),
  home, pc_*, radar*, lights, power, per-node phone/watch, csi_<cyd|kitchen|pcdesk|workdesk>_{motion,still} (10 s means, ok==1).
  Context comes from retained `presence/zone/state` (HA automation, every 10 s). No label column; no csi_zone column yet.
- label-site (nl-pi:8097): SQLite /var/lib/presence-labels/labels.db: labels(id, ts, zone, window_s=15), samples(label_id, node,
  phone_avg, phone_n, watch_avg, watch_n, mov_best, msgs, csi_motion, csi_still, csi_n). 85 labels, ALL from 2026-10-27 15:30-16:00
  (kitchen 27, pc_desk 24, work_desk 20, bed 14, bathroom 0); the zone-log starts 17:03 that day, so no baseline history exists for them.
  Publishes `presence/label/event` {id, zone, ts}. The "weak labels" of earlier analysis (tools/weak_labels.py) are not on nl-pi/Mac:
  I wrote own rule labels (csi_features.weak_label: pc_desk = PC active <=60 s; kitchen = radar on <=150 cm and PC quiet >=300 s;
  work_desk = work-desk light operated <=15 min, radar off, PC quiet; bed = 23:00-07:00, PC idle >=30 min, radar off, lights off).
- nl-pi: Python 3.13.5, no numpy/sklearn -> stdlib only. paho-mqtt present. HA MQTT discovery is enabled (other devices use it).
- Observed in zone-log 10-27..10-30 (23.6k rows): empty-room (away) motion levels differ ~2x between the two away blocks
  (cyd 14.7 vs 27.0, pcdesk 19 vs 37) and day medians drift (cyd_still 30 -> 6 -> 7 -> 2): raw features are not stable -> rolling baseline needed.

## CHECKPOINT (hard retire at ~150k context) - resume from here
status above is BLOCKED/in-progress only because the build is not finished; nothing is deployed, NOTHING on nl-pi was changed yet
(only read-only ssh + mosquitto_sub). No backups made yet (no live file touched).

### Done (local, Mac) - code lives in /Users/ruanyouyi/rpi4/room-presence-csi-shadow-20261030/ (copy of /tmp/csi-work)
- csi_features.py : Baseline (rolling 8 h window, quiet floor = 10th pct of log1p, scale = MAD, floor 0.15, MIN_N 360, reset on node reboot),
  ZoneModel (nearest centroid, pooled MAD sd floor 1.0, motion weight 0.15 -> still dominates), SMOOTH_N=6, decide(), weak_label rules.
- csi_replay.py   : offline replay + leave-one-day-out CV vs rule weak labels, BLE fp/live comparison, --compare sweeps. Runs: python3 csi_replay.py --logdir DIR [--compare]
- csi_svc.py      : Engine (pure logic, MQTT-free) + MQTT wrapper: subscribes presence/+/csi, presence/zone/state, presence/calibrate/{status,cmd}, presence/label/event;
  publishes retained presence/csi/state + presence/csi/agreement, HA MQTT discovery for sensor.rp_csi_zone / sensor.rp_csi_agreement (device "Room presence CSI shadow"),
  LWT presence/csi/availability, feature log /var/lib/presence-csi/feat-DATE.csv, state.json.gz persistence (baseline, samples, seed, ring, label evals).
  Bootstrap on first start from last 3 days of /var/lib/zone-log (baseline + weak-rule seed samples). NOT YET RUN OR TESTED (never executed once).
- Data copies for replay: .../room-presence-csi-shadow-20261030/data (zone-log CSVs 10-27..10-30, labels.db).

### Replay numbers (3 usable days 10-28..10-30, leave-one-day-out, vs RULE weak labels - not ground truth; first 8 h of the log excluded as warm-up)
- CSI baseline+classifier (w8h q0.1, sm6): n=10676 acc 0.736, balanced acc 0.599 (bed .92 kitchen .71 pc_desk .34 work_desk .41); day-only (07-23) acc .43 bal .50; night acc .88.
- Same windows: BLE fingerprint fp_zone acc 0.496 bal 0.363; BLE live zone acc .709 bal .694 (OPTIMISTIC: shares evidence with the rules).
- Static global normalisation (no rolling baseline, uses whole log = mild oracle): acc .765 bal .639 -> the rolling baseline is NOT shown to beat static on 3 days.
- Baseline window sweep (bal): 4h .61, 6h .60, 8h .60, 12h .52, 24h .45 -> longer windows worse (drift ~ hours); 8 h kept because it keeps a quiet floor at night.
- Motion features alone: bal 0.28 (useless); still alone 0.67 -> motion kept at weight 0.15.
- Label-site 85 labels (all one 30-min session 10-27 15:30-16:00, no baseline history): plain LOO BLE .78, CSI(motion+still) .72, CSI still-only .82, both .92 but leave-one-burst-out
  (8 bursts) BLE .41 CSI .05-.08 -> the 85 labels cannot validate generalisation; need visits spread over day/night.
- Honest conclusion: CSI-only is still weak vs real labels; pc_desk vs bed at night is hard (typing at PC in a quiet room barely moves 'still').

### Left to do (in order)
1. py_compile + unit tests for csi_features/csi_svc.Engine (synthetic msgs, cal capture, label ingest from temp sqlite, persistence roundtrip) -> test_csi.py; fix bugs (csi_svc never run yet).
2. zone_log.py patch (/opt/zone-log): subscribe presence/csi/state, add columns csi_zone,csi_conf at END of HEAD; migrate TODAY's file in place (append two empty columns; backup .bak-20261030-csi); blank when state older than 90 s.
3. csi_score.py (scores csi_zone/csi_conf columns of zone-log vs rule weak labels, vs fp_zone, vs live; per-day), reuse csi_replay.metrics/fmt.
4. systemd unit /etc/systemd/system/presence-csi.service (copy style of presence-calibrate.service: EnvironmentFile=/etc/presence-ble.env, Environment=CSI_DIR=/var/lib/presence-csi, ExecStart python3 /opt/presence-csi/csi_svc.py, Restart=always, Nice=10); install files to /opt/presence-csi/.
5. Dashboard: add "CSI 影子模式" markdown card(s) at END of the 定位除錯 view in /opt/homeassistant/config/dashboards/house.yaml (file was edited by rp-debugview at 09:57 - re-read right before editing; backup house.yaml.bak-20261030-csi; markdown with `|-`; one table row per line; also a small indicator = sensor.rp_csi_zone / rp_csi_agreement as entities card). Check yaml.safe_load in container + check_config (docker exec homeassistant python -m homeassistant --script check_config -c /config). Lovelace yaml dashboards reload on browser refresh; no HA restart needed (discovery sensors appear on their own).
6. Deploy: ssh nl-pi; mkdir /opt/presence-csi, /var/lib/presence-csi; scp files; systemctl daemon-reload; enable --now presence-csi; restart zone-log AFTER zone_log.py patch (backup first); verify mosquitto_sub presence/csi/state, sensor.rp_csi_zone via ha/ha_states.py (docker exec -i homeassistant python - rp_csi < /opt/presence-calibrate/ha/ha_states.py), journalctl -u presence-csi, zone-log new column.
7. Make sure live sensor.yui_room_zone / rp_feat_weight_db unchanged (compare before/after), room_presence_automations OFF.
8. Patch: unified diff (paths relative to /opt/ like the bathroom patch) -> /Users/ruanyouyi/rpi4/room-presence-csi-shadow-20261030.patch incl. new files as /dev/null additions; list new files in the report.
9. Phase report finish (files, services, verification output, replay numbers above, owner steps below, promotion criterion) + final message <=15 lines zh-TW.

### Proposed owner steps / promotion (put in final report)
- 定位校準: per zone (kitchen, work_desk, pc_desk, bed, bathroom) press the calibrate button 60 s, >=3 visits per zone at different times (day, evening, night; bed asleep-quiet and awake), bathroom: inside, doorway, near air fryer; also label-site taps in each place.
- Promotion criterion proposal: over >=7 days after the visits, sensor.rp_csi_agreement attrs: CSI balanced accuracy vs label-site taps (csi_vs_labels_7d, n>=30 incl. all zones) >= BLE fp_vs_labels_7d, AND weak_bal_24h >= ble_fp_weak_bal_24h on >=5 of 7 days, AND no zone recall < 0.4; then raise rp_feat_weight_db stepwise or let CSI vote only as tie-break, never replace.

### Resume log (sub-main #2)
- step1a DONE: test_csi.py (23 tests, python3 -m unittest test_csi) passes locally; csi_svc.Engine logic works on synthetic data. Next: live read-only smoke run of csi_svc.py on nl-pi (CSI_DIR=/tmp/csi-smoke, no discovery publishing to retained topics? -> see below), then zone_log patch.
- step2/3 DONE locally: new/zone-log/zone_log.py (orig/zone-log/zone_log.py = pristine copy), csi_score.py, tests pass (25). smoke_live.py ran OK on nl-pi (read-only, bootstrap 21s). Next: systemd unit, dashboard, deploy, patch.
- step6a: presence-csi service installed+started on nl-pi (/opt/presence-csi, unit /etc/systemd/system/presence-csi.service, new/systemd/). No live files edited yet. Next: verify sensors, zone_log patch (backup first), dashboard (backup), patch file.
- HA entities now sensor.rp_csi_zone/agreement/label (no device block; registry cleaned with HA stopped; backups core.entity_registry.bak-20261030-csi + core.device_registry.bak-20261030-csi in /opt/homeassistant/config/.storage, rewritten with indent=2). Next: zone_log deploy, dashboard deploy, patch.
- zone_log deployed (backup /opt/zone-log/zone_log.py.bak-20261030-csi, csv bak /var/lib/zone-log/zone-v3-2026-10-30.csv.bak-20261030-csi); house.yaml deployed (bak .bak-20261030-csi). Next: check_config (bg), verify dashboard loads, patch file, final report.

## FINAL (sub-main #2) - DONE, deployed 2026-10-30 11:1x
- nl-pi: /opt/presence-csi/{csi_features,csi_replay,csi_svc,csi_score}.py, unit /etc/systemd/system/presence-csi.service (active), state /var/lib/presence-csi.
  Entities sensor.rp_csi_zone (attrs scores/confidence/used_nodes/baseline_*/training), sensor.rp_csi_agreement, sensor.rp_csi_label ("zone · conf%").
  No MQTT-discovery device block (entity_id must be sensor.rp_csi_*); registry/device entries of first attempt removed with HA stopped (core.*registry.bak-20261030-csi in .storage); HA restarted twice.
- zone_log.py: csi_zone, csi_conf columns (47->49 cols), today's CSV migrated in place (rows consistent); backups *.bak-20261030-csi. csi_score.py: python3 /opt/presence-csi/csi_score.py.
- Dashboard house.yaml: home view badges "區域" + "CSI（影子）"; 定位除錯 view: 2 markdown cards + heading. Templates rendered with stub jinja; check_config no errors.
- Verified: services active, entities have states, yui_room_zone/rp_feat_weight_db(0.0)/room_presence_automations(off) untouched.
- Patch: room-presence-csi-shadow-20261030.patch (git apply --check ok; includes tests + smoke_live.py).
- agree_pct stays 'unknown' until >=5 weak-label samples per zone accumulate in the 24h ring.
