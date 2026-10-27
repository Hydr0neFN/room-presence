#!/bin/bash
# Run ON nl-pi as root. Needs /etc/presence-ble.env (from install_nlpi.sh).
# Copy first:  scp calibrate_svc.py presence-calibrate.service install_calibrate.sh ../calibrate.py root@10.0.0.20:/tmp/pc/
#              scp ../ha/ha_states.py root@10.0.0.20:/tmp/pc/ha_states.py
set -euo pipefail
cd /tmp/pc
install -d /opt/presence-calibrate/ha /var/lib/presence-calibrate
install -m 644 calibrate.py calibrate_svc.py /opt/presence-calibrate/
install -m 644 ha_states.py /opt/presence-calibrate/ha/ha_states.py
install -m 644 presence-calibrate.service /etc/systemd/system/presence-calibrate.service
systemctl daemon-reload
systemctl enable --now presence-calibrate
sleep 6
systemctl is-active presence-calibrate
journalctl -u presence-calibrate -n 5 --no-pager
