#!/bin/bash
cd /c/Users/Ruanyouyi/Rpi4/room-presence
for i in 1 2 3 4 5 6 7 8 9 10; do
  echo "== $(date +%T)"
  ssh nl-pi-lan "docker exec -i homeassistant python - rp_ yui_" < ha/ha_states.py 2>&1 | grep -E 'yui_room_zone|ble_stale|rp_night_bed|rp_kitchen_evidence|rp_fp_zone|rp_feat_weight|rp_kitchen_radar'
  sleep 60
done
