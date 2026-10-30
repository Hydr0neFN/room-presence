"""Zone history log (runs on nl-pi): raw context + features for back-tracking and offline (weak-label) training.

  presence/zone/state (retained, published by the HA automation rp_zone_to_mqtt every 10 s + on change): zone, fp zone, home,
      PC lock / idle, kitchen radar, work desk light (light.channel_b), light bar, link motion of the ESPHome nodes
  presence/<node>/apple   phone / watch IRK-labelled RSSI per node
  presence/<node>/csi     CSI 1 s summaries (motion, still, ok)
  power/powermon/state    powermon watts
  presence/csi/state      CSI shadow zone (presence-csi service, sensor.rp_csi_zone) -> columns csi_zone, csi_conf (shadow only, nothing reads them back)
    -> DIR/zone-v3-YYYY-MM-DD.csv   every 10 s, columns in HEAD (schema v3; zone-YYYY-MM-DD.csv = v1, zone-v2-* = v2 with a live weak_label)
    -> DIR/transitions.csv          ts, from, to   (every change of `zone`)
Phone / watch columns: mean dBm of the last 10 s, empty = not heard. csi_<node>_motion / _still: mean over the last 10 s of the
messages with ok=1 (the HA matcher sees the 10 s mean of motion via sensor.rp_link_<node>_motion and the latest still value).
Raw logging only: weak zone labels are derived offline from these columns by tools/weak_labels.py (rules can change retroactively).
Context older than CTX_STALE_S (HA silent) is written blank.
Env: MQTT_* (presence-ble.env), ZONELOG_DIR (default /var/lib/zone-log).
"""
import csv
import json
import os
import threading
import time
from collections import defaultdict, deque

DIR = os.environ.get("ZONELOG_DIR", "/var/lib/zone-log")
NODES = ["cyd", "kitchen", "nlpi", "pcdesk", "workdesk"]
CSI_NODES = ["cyd", "kitchen", "pcdesk", "workdesk"]
PERIOD_S = 10
CTX_STALE_S = 90
POWER_WINDOW_S = 120
HEAD = (["ts", "zone", "fp_zone", "home", "pc_online", "pc_locked", "pc_idle", "radar", "radar_cm",
         "power_w", "power_max120", "wd_light", "wd_light_age_s", "lightbar", "lightbar_age_s",
         "link_kitchenradar", "link_neopixel", "link_kitchenrssi", "kitchen_light", "kitchen_light_age_s", "neopixel",
         "neopixel_age_s", "gaming", "gaming_pc_w", "radar_moving", "radar_move_cm", "radar_still_cm", "radar_move_en",
         "radar_still_en"]
        + [f"{n}_{k}" for n in NODES for k in ("phone", "watch")]
        + [f"csi_{n}_{k}" for n in CSI_NODES for k in ("motion", "still")]
        + ["csi_zone", "csi_conf"])
OLD_COLS = 2   # csi_zone, csi_conf were appended to HEAD on 2026-10-30; files written before have HEAD[:-OLD_COLS]
CTX_KEYS = ["zone", "fp", "home", "pc_online", "pc_locked", "pc_idle", "radar", "radar_cm", "wd_light", "wd_light_ts",
            "lightbar", "lightbar_ts", "link_kitchenradar", "link_neopixel", "link_kitchenrssi", "kitchen_light", "kitchen_light_ts",
            "neopixel", "neopixel_ts", "gaming", "gaming_pc_w", "radar_moving", "radar_move_cm", "radar_still_cm", "radar_move_en",
            "radar_still_en"]


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def append(path, head, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(head)
        w.writerow(row)


def migrate(path):
    """Today's file written by the previous schema (HEAD without csi_zone, csi_conf): append two empty columns in place."""
    if not os.path.exists(path):
        return False
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != HEAD[:-OLD_COLS]:
        return False
    tmp = path + ".mig"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        for r in rows[1:]:
            w.writerow(r + [""] * OLD_COLS)
    os.replace(tmp, path)
    return True


def mean_or_blank(vals, nd=1):
    return round(sum(vals) / len(vals), nd) if vals else ""


class State:
    """Buffers fed by MQTT messages and the row builder; no I/O, so it can be tested with synthetic times."""

    def __init__(self):
        self.sig = defaultdict(deque)       # (node, "phone"|"watch") -> deque[(t, avg)]
        self.csi = defaultdict(deque)       # node -> deque[(t, motion, still)]
        self.power = deque()                # (t, watts)
        self.csi_state = {}                 # latest presence/csi/state payload
        self.ctx = {}
        self.ctx_t = 0.0

    def add_ctx(self, now, p):
        self.ctx = {k: p.get(k, "") for k in CTX_KEYS}
        self.ctx_t = now

    def add_apple(self, now, node, p):
        for e in p.get("irk") or []:
            if e.get("id") in ("phone", "watch") and e.get("n", 0) > 0:
                self.sig[(node, e["id"])].append((now, float(e["avg"])))

    def add_csi(self, now, node, p):
        if int(p.get("ok", 0)) == 1 and p.get("motion") is not None:
            still = float(p["still"]) if p.get("still") is not None and float(p["still"]) >= 0 else None   # -1 = no baseline yet
            self.csi[node].append((now, float(p["motion"]), still))

    def add_csi_state(self, p):
        self.csi_state = p

    def add_power(self, now, p):
        if p.get("watts") is not None:
            self.power.append((now, float(p["watts"])))

    def make_row(self, now):
        cutoff = now - PERIOD_S
        sig_cols = []
        for n in NODES:
            for k in ("phone", "watch"):
                q = self.sig[(n, k)]
                while q and q[0][0] < cutoff:
                    q.popleft()
                sig_cols.append(mean_or_blank([x for _, x in q]))
        csi_cols = []
        for n in CSI_NODES:
            q = self.csi[n]
            while q and q[0][0] < cutoff:
                q.popleft()
            csi_cols += [mean_or_blank([m for _, m, _ in q]), mean_or_blank([s for _, _, s in q if s is not None])]
        while self.power and self.power[0][0] < now - POWER_WINDOW_S:
            self.power.popleft()
        power_w = self.power[-1][1] if self.power and self.power[-1][0] >= cutoff else ""
        power_max = max(w for _, w in self.power) if self.power else ""
        fresh = now - self.ctx_t <= CTX_STALE_S
        try:
            cs = self.csi_state if now - float(self.csi_state.get("ts", 0)) <= CTX_STALE_S else {}
        except (TypeError, ValueError):
            cs = {}
        c = self.ctx if fresh else {}

        def age(key):
            ts = c.get(key)
            try:
                return round(now - float(ts)) if ts and float(ts) > 0 else ""
            except (TypeError, ValueError):
                return ""
        wd_age, lb_age = age("wd_light_ts"), age("lightbar_ts")
        return ([iso(now), c.get("zone", ""), c.get("fp", ""), c.get("home", ""),
                 c.get("pc_online", ""), c.get("pc_locked", ""), c.get("pc_idle", ""), c.get("radar", ""), c.get("radar_cm", ""),
                 power_w, power_max, c.get("wd_light", ""), wd_age, c.get("lightbar", ""), lb_age,
                 c.get("link_kitchenradar", ""), c.get("link_neopixel", ""), c.get("link_kitchenrssi", ""),
                 c.get("kitchen_light", ""), age("kitchen_light_ts"), c.get("neopixel", ""), age("neopixel_ts"),
                 c.get("gaming", ""), c.get("gaming_pc_w", ""), c.get("radar_moving", ""), c.get("radar_move_cm", ""),
                 c.get("radar_still_cm", ""), c.get("radar_move_en", ""), c.get("radar_still_en", "")]
                + sig_cols + csi_cols + [cs.get("zone", ""), cs.get("confidence", "")])


state = State()
lock = threading.Lock()
prev_zone = [None]


def on_message(c, u, m):
    now = time.time()
    try:
        p = json.loads(m.payload)
        with lock:
            if m.topic == "presence/zone/state":
                state.add_ctx(now, p)
                if prev_zone[0] is not None and p.get("zone") != prev_zone[0]:
                    append(os.path.join(DIR, "transitions.csv"), ["ts", "from", "to"], [iso(now), prev_zone[0], p.get("zone")])
                prev_zone[0] = p.get("zone")
            elif m.topic == "presence/csi/state":
                state.add_csi_state(p)
            elif m.topic == "power/powermon/state":
                state.add_power(now, p)
            else:
                node, kind = m.topic.split("/")[1:3]
                if kind == "apple":
                    state.add_apple(now, node, p)
                elif kind == "csi" and node in CSI_NODES:
                    state.add_csi(now, node, p)
    except (ValueError, TypeError, IndexError, AttributeError, OSError):
        pass


def writer():
    while True:
        time.sleep(PERIOD_S)
        now = time.time()
        with lock:
            row = state.make_row(now)
        try:
            append(os.path.join(DIR, "zone-v3-" + time.strftime("%Y-%m-%d", time.localtime(now)) + ".csv"), HEAD, row)
        except OSError:
            pass


def main():
    import paho.mqtt.client as mqtt
    os.makedirs(DIR, exist_ok=True)
    migrate(os.path.join(DIR, "zone-v3-" + time.strftime("%Y-%m-%d") + ".csv"))
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="zone_log") if hasattr(mqtt, "CallbackAPIVersion") \
        else mqtt.Client(client_id="zone_log")
    c.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    c.on_connect = lambda cl, u, f, rc: [cl.subscribe(t) for t in
                                         ("presence/zone/state", "presence/+/apple", "presence/+/csi", "power/powermon/state",
                                          "presence/csi/state")]
    c.on_message = on_message
    c.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    threading.Thread(target=writer, daemon=True).start()
    c.loop_forever()


if __name__ == "__main__":
    main()
