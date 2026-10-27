#!/bin/bash
# Run ON nl-pi as root (morning, owner present). Installs the Bluetooth stack + firmware,
# the scanner service, and a dedicated mosquitto user. Rollback: see README.
# Copy first:  scp nlpi_ble.py presence-ble.service install_nlpi.sh root@10.0.0.20:/tmp/
set -euo pipefail
cd /tmp
date
echo "== packages (bluez, Pi BT firmware, bleak, paho) =="
apt-get install -y pi-bluetooth bluez python3-bleak python3-paho-mqtt
echo "== bluetooth state =="
systemctl enable --now bluetooth || true
sleep 5
ls /sys/class/bluetooth || true
cat /sys/class/bluetooth/hci0/address 2>/dev/null || {
  echo "hci0 has no address: the BT firmware did not load; a reboot may be required (not done automatically)."
}
echo "== scanner service =="
install -d /opt/presence-ble
install -m 644 nlpi_ble.py /opt/presence-ble/nlpi_ble.py
install -m 644 presence-ble.service /etc/systemd/system/presence-ble.service
if [ ! -f /etc/presence-ble.env ]; then
  PW=$(head -c 24 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 24)
  cp -n /etc/mosquitto/passwd /etc/mosquitto/passwd.bak-blepresence
  mosquitto_passwd -b /etc/mosquitto/passwd blepresence "$PW"
  systemctl reload mosquitto
  umask 077
  printf 'MQTT_HOST=127.0.0.1\nMQTT_PORT=1883\nMQTT_USER=blepresence\nMQTT_PASS=%s\nBLE_NODE=nlpi\n' "$PW" > /etc/presence-ble.env
fi
systemctl daemon-reload
systemctl enable --now presence-ble
sleep 8
systemctl is-active presence-ble
journalctl -u presence-ble -n 5 --no-pager
