"""Calibration service (runs on nl-pi): record a zone fingerprint on a command from MQTT, so the owner can
calibrate from the phone (HA buttons) instead of running calibrate.py on the PC.

  presence/calibrate/cmd     in   {"action":"start","zone":"bed","seconds":60} | {"action":"clear","zone":"bed"}
                                  | {"action":"cancel"}
  presence/calibrate/status  out  retained {"state": idle|recording|done|error, "zone", "seconds", "elapsed",
                                  "pct", "message", "zones": {zone: "recorded YYYY-MM-DD HH:MM"}, "ts"}

Fingerprints are kept in FP_FILE and published retained to presence/config/fingerprints (same body as the
CLI). With no FP_FILE yet the service seeds itself from the retained topic, so nothing already stored is lost.
Env (same file as presence-ble): MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASS, FP_FILE.
"""
import json
import os
import threading
import time

import calibrate as cal

ZONES = cal.ZONES
MIN_S, MAX_S = 20, 300
CMD, STATUS = "presence/calibrate/cmd", "presence/calibrate/status"

lock = threading.Lock()
state = {"state": "idle", "zone": None, "seconds": 0, "elapsed": 0, "message": ""}
cancel_flag = threading.Event()
fps = {}
fp_file = os.environ.get("FP_FILE", "/var/lib/presence-calibrate/fingerprints.json")


def zones_info():
    return {z: v.get("recorded", "?") for z, v in fps.items()}


def post(client, **kw):
    state.update(kw)
    pct = int(100 * state["elapsed"] / state["seconds"]) if state["seconds"] else 0
    body = dict(state, pct=pct, zones=zones_info(), ts=int(time.time()))
    client.publish(STATUS, json.dumps(body), qos=1, retain=True)


def save(client):
    os.makedirs(os.path.dirname(fp_file), exist_ok=True)
    tmp = fp_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fps, f, indent=1)
    os.replace(tmp, fp_file)
    cal.publish(client, fps)


def run_record(client, zone, seconds):
    try:
        post(client, state="recording", zone=zone, seconds=seconds, elapsed=0, message="stay in the zone")
        last = [0]

        def progress(el):
            if int(el) != last[0]:
                last[0] = int(el)
                post(client, elapsed=int(el))

        res = cal.record_zone(client, zone, seconds, 5, lambda: cal.read_links(local=True), progress, cancel_flag.is_set)
        if res is None:
            post(client, state="idle", message="cancelled", elapsed=0)
            return
        entry, feat, info = res
        if not entry["nodes"]:
            post(client, state="error", message="no BLE/CSI data heard; nothing stored", elapsed=seconds)
            return
        missing = [n for n in ("pcdesk", "workdesk", "nlpi", "cyd") if n not in entry["nodes"]]
        with lock:
            fps[zone] = entry
            save(client)
        msg = "stored" + (f"; nodes silent: {','.join(missing)}" if missing else "") + f"; feat {len(feat)}"
        post(client, state="done", message=msg, elapsed=seconds)
    except Exception as e:  # keep the service alive whatever one recording does
        post(client, state="error", message=f"{type(e).__name__}: {e}")
    finally:
        cancel_flag.clear()


def do_clear(client, zone):
    with lock:
        fps.pop(zone, None)
        save(client)  # waits for the publish, so never on the network thread
    post(client, state="idle", zone=zone, elapsed=0, seconds=0, message=f"cleared {zone}")


def on_cmd(client, userdata, m):
    try:
        p = json.loads(m.payload)
        action, zone = p.get("action"), p.get("zone")
    except (ValueError, AttributeError):
        return
    busy = state["state"] == "recording"
    if action == "cancel":
        if busy:
            cancel_flag.set()
    elif action == "start":
        if busy:
            post(client, message=f"busy recording {state['zone']}; start ignored")
        elif zone not in ZONES:
            post(client, state="error", message=f"unknown zone {zone!r}")
        else:
            try:
                secs = max(MIN_S, min(MAX_S, int(p.get("seconds", 60))))
            except (TypeError, ValueError):
                secs = 60
            cancel_flag.clear()
            threading.Thread(target=run_record, args=(client, zone, secs), daemon=True).start()
    elif action == "clear":
        if busy:
            post(client, message="busy recording; clear ignored")
        elif zone in ZONES:
            threading.Thread(target=do_clear, args=(client, zone), daemon=True).start()


def seed(client):
    """FP_FILE if present, else the retained fingerprints topic (3 s), else empty."""
    if os.path.exists(fp_file):
        with open(fp_file, encoding="utf-8") as f:
            fps.update(json.load(f))
        return
    got = []
    client.message_callback_add("presence/config/fingerprints", lambda c, u, m: got.append(m.payload))
    client.subscribe("presence/config/fingerprints")
    time.sleep(3)
    client.unsubscribe("presence/config/fingerprints")
    client.message_callback_remove("presence/config/fingerprints")
    if got:
        body = json.loads(got[-1])
        for z, nodes in body.get("zones", {}).items():
            fps[z] = {"nodes": nodes, "recorded": time.strftime("%Y-%m-%d %H:%M", time.localtime(body.get("ts", 0))),
                      "heard": {}}
            f = body.get("feat", {}).get("zones", {}).get(z)
            if f:
                fps[z]["feat"] = f


def main():
    client = cal.new_client("calibrate_svc")
    client.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    client.will_set(STATUS, json.dumps({"state": "offline", "message": "service down", "zones": {}, "pct": 0}), qos=1, retain=True)
    client.on_connect = lambda c, u, f, rc: c.subscribe(CMD, qos=1)
    client.message_callback_add(CMD, on_cmd)
    client.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    client.loop_start()
    time.sleep(1)
    seed(client)
    post(client, message="ready")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
