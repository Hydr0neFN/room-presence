"""Record a per-zone RSSI fingerprint from the presence/<node>/apple topics.

  py -3.13 calibrate.py --zone pc_desk --seconds 60     # sit/stand in the zone while it runs
  py -3.13 calibrate.py --show
  py -3.13 calibrate.py --clear bed

Per node and per 3 s message the signal is the owner's IRK-resolved PHONE (if a phone IRK is
loaded on that node) else the strongest non-stationary Apple advert (the watch is never used); a message without a sample counts
as -100 (same rule as Home Assistant). The median per node is stored in fingerprints.json
and published retained to presence/config/fingerprints, which Home Assistant matches against.
"""
import argparse
import json
import os
import statistics
import time

import paho.mqtt.client as mqtt

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "fingerprints.json")
ZONES = ("kitchen", "work_desk", "pc_desk", "bed")
FLOOR = -100


def node_signal(p):
    """Same rule as Home Assistant: the labelled phone IRK, else the strongest NON-stationary Apple advert."""
    irk = p.get("irk") or []
    phone = [e for e in irk if e.get("id", "phone") == "phone"]
    if phone:
        return phone[0]["avg"] if phone[0]["n"] > 0 else None
    n = p.get("mov_n", p.get("apple_n", 0))
    return p.get("mov_best", p.get("apple_best")) if n > 0 else None


def load():
    if os.path.exists(FILE):
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def publish(client, fps):
    nodes = sorted({n for z in fps.values() for n in z["nodes"]})
    zones = {z: {n: v["nodes"].get(n, FLOOR) for n in nodes} for z, v in fps.items()}
    body = {"ts": int(time.time()), "nodes": nodes, "zones": zones}
    client.publish("presence/config/fingerprints", json.dumps(body), qos=1, retain=True).wait_for_publish(5)
    return body


def connect():
    with open(os.path.join(HERE, "pc-idle", "mqtt-pass.local"), encoding="utf-8") as f:
        pw = f.read().strip()
    c = mqtt.Client(client_id="calibrate_cli")
    c.username_pw_set("pcpresence", pw)
    c.connect("10.0.0.20", 1883, 60)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", choices=ZONES)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--settle", type=int, default=5, help="seconds ignored at the start")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--clear", choices=ZONES)
    ap.add_argument("--require", default="pcdesk,workdesk,nlpi", help="nodes expected to report")
    a = ap.parse_args()
    fps = load()
    client = connect()
    client.loop_start()
    if a.show:
        print(json.dumps(fps, indent=1))
        return
    if a.clear:
        fps.pop(a.clear, None)
    elif a.zone:
        samples = {}
        t0 = time.time()

        def on_msg(c, u, m):
            if time.time() - t0 < a.settle:
                return
            try:
                node = m.topic.split("/")[1]
                samples.setdefault(node, []).append(node_signal(json.loads(m.payload)))
            except (ValueError, KeyError, IndexError):
                pass

        client.on_message = on_msg
        client.subscribe("presence/+/apple")
        print(f"Recording '{a.zone}' for {a.seconds} s (first {a.settle} s ignored). Stay in the zone, behave normally.")
        time.sleep(a.seconds)
        nodes, info = {}, {}
        for n, vals in samples.items():
            heard = [v for v in vals if v is not None]
            nodes[n] = int(statistics.median([v if v is not None else FLOOR for v in vals]))
            info[n] = f"{len(heard)}/{len(vals)} heard"
        for n in a.require.split(","):
            if n and n not in nodes:
                print(f"WARNING: node '{n}' sent nothing; stored as {FLOOR}")
        fps[a.zone] = {"nodes": nodes, "recorded": time.strftime("%Y-%m-%d %H:%M"), "heard": info}
        print(f"{a.zone}: {nodes}  ({info})")
    body = publish(client, fps)
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(fps, f, indent=1)
    print("published", json.dumps(body))


if __name__ == "__main__":
    main()
