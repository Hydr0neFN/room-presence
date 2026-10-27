"""Zone history log (runs on nl-pi) so movements can be tracked back later.

  presence/zone/state (retained, published by the HA automation rp_zone_to_mqtt on every change + every minute)
  presence/<node>/apple (phone / watch IRK-labelled RSSI per node)
    -> DIR/zone-YYYY-MM-DD.csv   every 10 s: ts, zone, fp_zone, home, pc_idle, radar, radar_cm, then <node>_phone / <node>_watch
                                 (mean dBm of the last 10 s, empty = not heard)
    -> DIR/transitions.csv       ts, from, to   (every change of `zone`)
Env: MQTT_* (presence-ble.env), ZONELOG_DIR (default /var/lib/zone-log).
"""
import csv
import json
import os
import threading
import time
from collections import defaultdict, deque

import paho.mqtt.client as mqtt

DIR = os.environ.get("ZONELOG_DIR", "/var/lib/zone-log")
NODES = ["cyd", "kitchen", "nlpi", "pcdesk", "workdesk"]
PERIOD_S = 10
HEAD = (["ts", "zone", "fp_zone", "home", "pc_idle", "radar", "radar_cm"]
        + [f"{n}_{k}" for n in NODES for k in ("phone", "watch")])
lock = threading.Lock()
sig = defaultdict(deque)  # (node, "phone"|"watch") -> deque[(t, avg)]
zone = {"zone": "", "fp": "", "home": "", "pc_idle": "", "radar": "", "radar_cm": ""}
prev_zone = [None]


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def append(path, head, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(head)
        w.writerow(row)


def on_message(c, u, m):
    now = time.time()
    try:
        p = json.loads(m.payload)
        if m.topic == "presence/zone/state":
            with lock:
                zone.update({k: p.get(k, "") for k in zone})
                if prev_zone[0] is not None and p.get("zone") != prev_zone[0]:
                    append(os.path.join(DIR, "transitions.csv"), ["ts", "from", "to"], [iso(now), prev_zone[0], p.get("zone")])
                prev_zone[0] = p.get("zone")
            return
        node = m.topic.split("/")[1]
        with lock:
            for e in p.get("irk") or []:
                if e.get("id") in ("phone", "watch") and e.get("n", 0) > 0:
                    sig[(node, e["id"])].append((now, float(e["avg"])))
    except (ValueError, TypeError, IndexError, AttributeError, OSError):
        pass


def writer():
    while True:
        time.sleep(PERIOD_S)
        now = time.time()
        with lock:
            row = [iso(now), zone["zone"], zone["fp"], zone["home"], zone["pc_idle"], zone["radar"], zone["radar_cm"]]
            for n in NODES:
                for k in ("phone", "watch"):
                    q = sig[(n, k)]
                    while q and q[0][0] < now - PERIOD_S:
                        q.popleft()
                    row.append(round(sum(v for _, v in q) / len(q), 1) if q else "")
        try:
            append(os.path.join(DIR, "zone-" + time.strftime("%Y-%m-%d", time.localtime(now)) + ".csv"), HEAD, row)
        except OSError:
            pass


def main():
    os.makedirs(DIR, exist_ok=True)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="zone_log") if hasattr(mqtt, "CallbackAPIVersion") \
        else mqtt.Client(client_id="zone_log")
    c.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    c.on_connect = lambda cl, u, f, rc: [cl.subscribe("presence/zone/state"), cl.subscribe("presence/+/apple")]
    c.on_message = on_message
    c.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    threading.Thread(target=writer, daemon=True).start()
    c.loop_forever()


if __name__ == "__main__":
    main()
