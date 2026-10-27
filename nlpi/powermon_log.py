"""Whole-house power log (runs on nl-pi) for later analysis, e.g. dinner-time high-wattage use.

  power/powermon/state (every ~10 s) + latest pc/telemetry/state + powermon/diag
    -> DIR/power-YYYY-MM-DD.csv   ts,watts,total_kwh,swing_mv,peak_mv,rssi,pc_w,pc_gaming,pulses,rejected
  watts >= EVENT_W (default 1000) for any reading opens an event, closed after 3 readings below
    -> DIR/events.csv             start,end,secs,peak_w,mean_w,kwh_est,pc_w_mean   (kwh_est from the watts samples)
Plain CSV, append-only, one file per day (about 8.6k rows = 700 KB/day). Env: MQTT_* (presence-ble.env), POWERLOG_DIR,
EVENT_W.
"""
import csv
import json
import os
import time

import paho.mqtt.client as mqtt

DIR = os.environ.get("POWERLOG_DIR", "/var/lib/powermon-log")
EVENT_W = float(os.environ.get("EVENT_W", "1000"))
HEAD = ["ts", "watts", "total_kwh", "swing_mv", "peak_mv", "rssi", "pc_w", "pc_gaming", "pulses", "rejected"]
EHEAD = ["start", "end", "secs", "peak_w", "mean_w", "kwh_est", "pc_w_mean"]
last = {"pc_w": "", "pc_gaming": "", "pulses": "", "rejected": ""}
ev = None  # open event: dict(start, t0, last_t, w=[], pc=[], below=0)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def append(path, head, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(head)
        w.writerow(row)


def close_event():
    global ev
    if not ev:
        return
    w = ev["w"][:-ev["below"]] if ev["below"] else ev["w"]
    t_end = ev["t_last_high"]
    secs = max(0.0, t_end - ev["t0"])
    mean = sum(w) / len(w) if w else 0
    pc = ev["pc"]
    append(os.path.join(DIR, "events.csv"), EHEAD,
           [iso(ev["t0"]), iso(t_end), round(secs), max(w) if w else 0, round(mean), round(mean * secs / 3.6e6, 4),
            round(sum(pc) / len(pc), 1) if pc else ""])
    ev = None


def on_state(t, p):
    global ev
    w = float(p.get("watts", 0))
    append(os.path.join(DIR, "power-" + time.strftime("%Y-%m-%d", time.localtime(t)) + ".csv"), HEAD,
           [iso(t), w, p.get("total_kwh", ""), p.get("swing_mv", ""), p.get("peak_mv", ""), p.get("rssi", ""),
            last["pc_w"], last["pc_gaming"], last["pulses"], last["rejected"]])
    if w >= EVENT_W:
        if not ev:
            ev = {"t0": t, "w": [], "pc": [], "below": 0, "t_last_high": t}
        ev["w"].append(w)
        ev["below"] = 0
        ev["t_last_high"] = t
        if last["pc_w"] != "":
            ev["pc"].append(last["pc_w"])
    elif ev:
        ev["w"].append(w)
        ev["below"] += 1
        if ev["below"] >= 3:
            close_event()


def on_message(c, u, m):
    try:
        p = json.loads(m.payload)
        if m.topic == "power/powermon/state":
            on_state(time.time(), p)
        elif m.topic == "pc/telemetry/state":
            last["pc_w"], last["pc_gaming"] = p.get("total_w", ""), p.get("gaming", "")
        elif m.topic == "power/powermon/diag":
            last["pulses"], last["rejected"] = p.get("pulses", ""), p.get("rejected", "")
    except (ValueError, TypeError, AttributeError, OSError):
        pass


def main():
    os.makedirs(DIR, exist_ok=True)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="powermon_log") if hasattr(mqtt, "CallbackAPIVersion") \
        else mqtt.Client(client_id="powermon_log")
    c.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    c.on_connect = lambda cl, u, f, rc: [cl.subscribe(t) for t in ("power/powermon/state", "power/powermon/diag", "pc/telemetry/state")]
    c.on_message = on_message
    c.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    c.loop_forever()


if __name__ == "__main__":
    main()
