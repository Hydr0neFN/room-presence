"""Publish this PC's user-idle time to MQTT so Home Assistant can tell "at the PC desk".

Topics (host 10.0.0.20:1883, user pcpresence, password from mqtt-pass.local next to this file):
  presence/pcdesk/idle    {"idle": <seconds since last keyboard/mouse input>, "locked": 0|1}   every 10 s
  presence/pcdesk/status  online | offline (retained, offline is the last will)
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

import paho.mqtt.client as mqtt

HOST = os.environ.get("PRESENCE_MQTT_HOST", "10.0.0.20")
PORT = 1883
USER = "pcpresence"
INTERVAL = 10
HERE = os.path.dirname(os.path.abspath(__file__))


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds():
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return -1
    tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((tick - info.dwTime) & 0xFFFFFFFF) // 1000


def session_locked():
    # OpenInputDesktop fails while the secure (lock screen) desktop is active.
    desk = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desk:
        return True
    ctypes.windll.user32.CloseDesktop(desk)
    return False


def main():
    with open(os.path.join(HERE, "mqtt-pass.local"), encoding="utf-8") as f:
        password = f.read().strip()
    client = mqtt.Client(client_id="pc_idle_pcdesk")
    client.username_pw_set(USER, password)
    client.will_set("presence/pcdesk/status", "offline", qos=1, retain=True)
    client.on_connect = lambda c, u, fl, rc: c.publish("presence/pcdesk/status", "online", qos=1, retain=True)
    client.reconnect_delay_set(min_delay=2, max_delay=60)
    client.connect_async(HOST, PORT, keepalive=60)
    client.loop_start()
    while True:
        payload = json.dumps({"idle": idle_seconds(), "locked": int(session_locked())})
        client.publish("presence/pcdesk/idle", payload, qos=0, retain=False)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
