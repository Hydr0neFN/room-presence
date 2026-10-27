"""Record a per-zone fingerprint from the presence/<node>/apple and presence/<node>/csi topics.

  py -3.13 calibrate.py --zone pc_desk --seconds 60     # sit/stand in the zone while it runs
  py -3.13 calibrate.py --show [--feat-note]
  py -3.13 calibrate.py --clear bed

Per node and per 3 s message the signal is the owner's IRK-resolved PHONE (if a phone IRK is
loaded on that node) else the strongest non-stationary Apple advert (the watch is never used); a message without a sample counts
as -100 (same rule as Home Assistant). The median per node is stored in fingerprints.json
and published retained to presence/config/fingerprints, which Home Assistant matches against.

Device-free WiFi features (optional, stored per zone as "feat"):
  csi_<node>_motion  median of the sliding 10 s means of presence/<node>/csi "motion" (ok==1 only), per-mille
  csi_<node>_still   median of presence/<node>/csi "still" (ok==1 only), per-mille
  link_kitchenradar / link_neopixel  median of sensor.rp_link_{kitchenradar,neopixel}_motion read from
                     Home Assistant over ssh every 5 s (skipped silently if ssh fails), dB
The published body gets "feat": {"names", "zones", "scale"}; scale = max(std over all zones, floor).
Zones calibrated before without feat simply have no feat.
"""
import argparse
import json
import os
import statistics
import subprocess
import time

import paho.mqtt.client as mqtt

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "fingerprints.json")
ZONES = ("kitchen", "work_desk", "pc_desk", "bed")
FLOOR = -100
WINDOW_S = 10.0         # sliding mean window for csi motion
POLL_S = 5.0            # HA link feature poll period
SCALE_FLOOR_CSI = 3.0   # per-mille
SCALE_FLOOR_LINK = 0.5  # dB
LINKS = {"sensor.rp_link_kitchenrssi_motion": "link_kitchenrssi",  # powermon C3 (node kitchen) link RSSI std dev
         "sensor.rp_link_kitchenradar_motion": "link_kitchenradar",
         "sensor.rp_link_neopixel_motion": "link_neopixel"}
HA_STATES = os.path.join(HERE, "ha", "ha_states.py")


def node_signal(p):
    """Same rule as Home Assistant: the labelled phone IRK, else the strongest NON-stationary Apple advert."""
    irk = p.get("irk") or []
    phone = [e for e in irk if e.get("id", "phone") == "phone"]
    if phone:
        return phone[0]["avg"] if phone[0]["n"] > 0 else None
    n = p.get("mov_n", p.get("apple_n", 0))
    return p.get("mov_best", p.get("apple_best")) if n > 0 else None


def sliding_means(series, window=WINDOW_S, min_n=3):
    """series: [(t, value)] ascending. Mean of the values in (t-window, t] for every sample with >= min_n in the window."""
    out, lo, tot = [], 0, 0.0
    for i, (t, v) in enumerate(series):
        tot += v
        while series[lo][0] <= t - window:
            tot -= series[lo][1]
            lo += 1
        if i - lo + 1 >= min_n:
            out.append(tot / (i - lo + 1))
    return out


def read_links():
    """Read the HA link features through ssh (recorder DB). {} if ssh/HA is not reachable."""
    try:
        with open(HA_STATES, "rb") as f:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "nl-pi-lan",
                                "docker exec -i homeassistant python - rp_link_"],
                               stdin=f, capture_output=True, timeout=20)
        if r.returncode != 0:
            return {}
        out = {}
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in LINKS:
                try:
                    out[LINKS[parts[0]]] = float(parts[1])
                except ValueError:
                    pass
        return out
    except (OSError, subprocess.SubprocessError):
        return {}


def feat_floor(name):
    return SCALE_FLOOR_LINK if name.startswith("link_") else SCALE_FLOOR_CSI


def build_feat(fps):
    """{"names": [...], "zones": {zone: {name: value}}, "scale": {name: sigma}} over the zones that have feat."""
    zones = {z: dict(v["feat"]) for z, v in fps.items() if v.get("feat")}
    names = sorted({n for f in zones.values() for n in f})
    scale = {}
    for n in names:
        vals = [f[n] for f in zones.values() if n in f]
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        scale[n] = round(max(sd, feat_floor(n)), 3)
    return {"names": names, "zones": zones, "scale": scale}


def load():
    if os.path.exists(FILE):
        with open(FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def publish(client, fps):
    nodes = sorted({n for z in fps.values() for n in z["nodes"]})
    zones = {z: {n: v["nodes"].get(n, FLOOR) for n in nodes} for z, v in fps.items()}
    body = {"ts": int(time.time()), "nodes": nodes, "zones": zones, "feat": build_feat(fps)}
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
    ap.add_argument("--feat-note", action="store_true", help="with --show: also print the stored features as a table")
    ap.add_argument("--clear", choices=ZONES)
    ap.add_argument("--require", default="pcdesk,workdesk,nlpi,cyd", help="nodes expected to report (kitchen is optional)")
    a = ap.parse_args()
    fps = load()
    if a.show:
        print(json.dumps(fps, indent=1))
        if a.feat_note:
            ft = build_feat(fps)
            print("feat (zone x feature); scale = sigma used by Home Assistant:")
            print("  %-22s" % "" + "".join("%-12s" % z for z in ft["zones"]) + "scale")
            for n in ft["names"]:
                print("  %-22s" % n + "".join("%-12s" % ft["zones"][z].get(n, "-") for z in ft["zones"]) + str(ft["scale"][n]))
            if not ft["names"]:
                print("  (no zone has feat yet)")
        return
    client = connect()
    client.loop_start()
    if a.clear:
        fps.pop(a.clear, None)
    elif a.zone:
        samples = {}
        motion, still, links = {}, {}, {}
        t0 = time.time()

        def on_msg(c, u, m):
            now = time.time()
            if now - t0 < a.settle:
                return
            try:
                parts = m.topic.split("/")
                node, kind = parts[1], parts[2]
                p = json.loads(m.payload)
                if kind == "csi":
                    if int(p.get("ok", 0)) == 1:
                        if p.get("motion") is not None:
                            motion.setdefault(node, []).append((now, float(p["motion"])))
                        if p.get("still") is not None:
                            still.setdefault(node, []).append(float(p["still"]))
                elif kind == "apple":
                    samples.setdefault(node, []).append(node_signal(p))
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                pass

        client.on_message = on_msg
        client.subscribe("presence/+/apple")
        client.subscribe("presence/+/csi")
        print(f"Recording '{a.zone}' for {a.seconds} s (first {a.settle} s ignored). Stay in the zone, behave normally.")
        t_end = t0 + a.seconds
        while time.time() < t_end:
            if time.time() - t0 >= a.settle:
                for k, v in read_links().items():
                    links.setdefault(k, []).append(v)
            time.sleep(max(0.0, min(POLL_S, t_end - time.time())))
        nodes, info = {}, {}
        for n, vals in samples.items():
            heard = [v for v in vals if v is not None]
            nodes[n] = int(statistics.median([v if v is not None else FLOOR for v in vals]))
            info[n] = f"{len(heard)}/{len(vals)} heard"
        for n in a.require.split(","):
            if n and n not in nodes:
                print(f"WARNING: node '{n}' sent nothing; stored as {FLOOR}")
        feat = {}
        for n, series in motion.items():
            means = sliding_means(series)
            if means:
                feat[f"csi_{n}_motion"] = round(statistics.median(means), 2)
        for n, vals in still.items():
            feat[f"csi_{n}_still"] = round(statistics.median(vals), 2)
        for k, vals in links.items():
            feat[k] = round(statistics.median(vals), 3)
        fps[a.zone] = {"nodes": nodes, "recorded": time.strftime("%Y-%m-%d %H:%M"), "heard": info}
        if feat:
            fps[a.zone]["feat"] = feat
        print(f"{a.zone}: {nodes}  ({info})")
        print(f"{a.zone} feat: {feat if feat else 'none (no csi / link data)'}")
    body = publish(client, fps)
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(fps, f, indent=1)
    print("published", json.dumps(body))


if __name__ == "__main__":
    main()
