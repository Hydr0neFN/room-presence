"""Presence tracker service (nl-pi): fused zone estimate from the zone log, published on MQTT.

It replaces the CSI zone classifier that feeds sensor.rp_csi_zone (the old service keeps its feature log but its topics can
be moved away with CSI_TOPIC_PREFIX).

  in   <ZONELOG_DIR>/zone-v3-YYYY-MM-DD.csv   one row per 10 s (read by tracker.py)
       presence/track/cmd                     {"action": "refit"}
       presence/calibrate/status              {"state": recording|done|idle|error|offline, "zone": ...}
  out  presence/csi/state         retained JSON {zone, confidence, ts, post, burst, since_burst_s, clamp, n_features,
                                  weak_label, row_ts, engine, training, trained_at}   -> sensor.rp_csi_zone / _label
       presence/csi/availability  retained online / LWT offline
       homeassistant/sensor/rp_csi_{zone,label}/config   discovery (same two entities as before)

The model is trained on the last TRAIN_DAYS zone-log files older than today plus today's file, refitted once a day at
TRACK_REFIT_HOUR (or on demand), and gets bathroom / repaired zones from <TRACK_DIR>/seeds.json. A calibration recording
stores its rows as seeds in that file. Env: MQTT_* (presence-ble.env), ZONELOG_DIR, TRACK_DIR, TRACK_REFIT_HOUR.
"""
import json
import os
import sys
import threading
import time

import tracker as tr

PERIOD_S = 10
MAX_AGE_S = 40           # publish only when the newest processed row is at most this old
WARM_ROWS = 360          # rows of today's file fed through the filter at start (no publish)
WARM_MIN = 60            # fewer rows than this and the tail of yesterday's file is used first
TRACK_DIR = os.environ.get("TRACK_DIR", "/var/lib/presence-track")
ZONELOG_DIR = os.environ.get("ZONELOG_DIR", "/var/lib/zone-log")
REFIT_HOUR = int(os.environ.get("TRACK_REFIT_HOUR", "4"))
T_STATE = "presence/csi/state"
T_AVAIL = "presence/csi/availability"
T_CMD = "presence/track/cmd"
T_CAL = "presence/calibrate/status"


def log(msg):
    print("presence-track: %s" % msg, file=sys.stderr)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def day_of(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


DISCOVERY = {
    "rp_csi_zone": {"name": "RP csi zone", "unique_id": "rp_csi_zone", "state_topic": T_STATE,
                    "value_template": "{{ value_json.zone }}", "json_attributes_topic": T_STATE, "icon": "mdi:wifi-marker",
                    "expire_after": 90},
    "rp_csi_label": {"name": "RP csi label", "unique_id": "rp_csi_label", "state_topic": T_STATE,
                     "value_template": "{{ value_json.zone }} \u00b7 {{ (value_json.confidence * 100) | round(0) | int }}%",
                     "icon": "mdi:wifi-marker", "expire_after": 90},
}


class Service:
    """All tick logic, no MQTT: tick(now) -> state payload or None; the message hooks return None."""

    def __init__(self, logdir=None, track_dir=None, refit_hour=None, now=None):
        self.logdir = ZONELOG_DIR if logdir is None else logdir
        self.track_dir = TRACK_DIR if track_dir is None else track_dir
        self.refit_hour = REFIT_HOUR if refit_hour is None else refit_hour
        now = time.time() if now is None else now
        self.seeds_path = os.path.join(self.track_dir, "seeds.json")
        try:
            os.makedirs(self.track_dir, exist_ok=True)
        except OSError as e:
            log("cannot create %s: %s" % (self.track_dir, e))
        self.seeds = tr.load_seeds(self.seeds_path)
        self.trained_at = int(now)
        try:
            self.model, self.info = self._train(now)
        except Exception as e:   # noqa: BLE001 - start with an empty model rather than not at all
            log("train failed: %s" % e)
            self.model, self.info = {}, {}
        self.tracker = tr.Tracker(self.model)
        self.last_ts = None
        self.tail = tr.Tail()    # today's file is read from the last offset, not from the start, on every tick
        self.last_refit_day = None
        self.cal = {"state": "idle", "zone": None, "rows": []}
        self._warm(now)

    # ------------------------------------------------------------- model / files
    def _path_for(self, t):
        return os.path.join(self.logdir, "zone-v3-%s.csv" % day_of(t))

    def _today_path(self, now):
        return self._path_for(now)

    def _day_files(self, now):
        """The last TRAIN_DAYS files older than today plus today's file."""
        today = "zone-v3-%s.csv" % day_of(now)
        files = tr.list_days(self.logdir)
        old = [p for p in files if os.path.basename(p) < today]
        return old[-tr.TRAIN_DAYS:] + [p for p in files if os.path.basename(p) == today]

    def _train(self, now):
        return tr.train(self.logdir, self._day_files(now), tr.merge_seeds(self.seeds))

    def _warm(self, now):
        """Feed the last rows of today's file (and the tail of yesterday when today is short) through the filter."""
        rows = tr.load_rows(self._today_path(now))
        hist = rows
        if len(rows) < WARM_MIN:
            ypath = self._path_for(now - 86400)
            if os.path.exists(ypath):
                hist = tr.load_rows(ypath)[-WARM_ROWS:] + rows
        for row in hist[-WARM_ROWS:]:
            try:
                self.tracker.step(row)
            except (TypeError, ValueError, KeyError):
                pass
        if rows:
            self.last_ts = rows[-1]["ts"]

    def refit(self, now):
        """New model + new Tracker, carrying over post (renormalised over the shared zones) and the burst windows."""
        try:
            model, info = self._train(now)
        except Exception as e:   # noqa: BLE001 - keep the old model when a file is unreadable
            log("refit failed: %s" % e)
            return False
        if len(model) < 2:
            log("refit: %d zones, keeping the old model" % len(model))
            return False
        old = self.tracker
        self.model, self.info, self.trained_at = model, info, int(now)
        self.tracker = tr.Tracker(model)
        self.tracker.burst = old.burst
        self.tracker.since = old.since
        self.tracker.zone, self.tracker.held = (old.zone, old.held) if old.zone in model else (None, 0)
        vals = {z: old.post.get(z, 0.0) for z in model}
        tot = sum(vals.values())
        if tot > 0:
            self.tracker.post = {z: v / tot for z, v in vals.items()}
        return True

    def _maybe_refit(self, now):
        day = day_of(now)
        if time.localtime(now).tm_hour == self.refit_hour and self.last_refit_day != day:
            self.last_refit_day = day
            self.refit(now)

    # ------------------------------------------------------------- inputs
    def on_cmd(self, p, now=None):
        if p.get("action") == "refit":
            self.refit(time.time() if now is None else now)

    def on_cal_status(self, p, now=None):
        """Recording -> keep the features of every processed row; done -> replace that zone's cal seeds and refit."""
        st, zone = p.get("state"), p.get("zone")
        c = self.cal
        if st == "recording":
            if zone in tr.ZONES:
                if c["state"] != "recording" or c["zone"] != zone:
                    self.cal = {"state": "recording", "zone": zone, "rows": []}
            else:
                self.cal = {"state": "idle", "zone": None, "rows": []}
        elif st == "done" and c["state"] == "recording" and c["zone"] == zone:
            if zone in tr.ZONES and len(c["rows"]) >= 3:
                self.seeds.setdefault("cal", {})[zone] = list(c["rows"])
                try:
                    tr.save_seeds(self.seeds_path, self.seeds)
                except OSError as e:
                    log("seed save failed: %s" % e)
                self.refit(time.time() if now is None else now)
            self.cal = {"state": "idle", "zone": None, "rows": []}
        elif st in ("idle", "error", "offline") and c["state"] == "recording":
            self.cal = {"state": "idle", "zone": None, "rows": []}

    # ------------------------------------------------------------- the 10 s tick
    def payload(self, row, res, now):
        return {"zone": res["zone"], "confidence": res["confidence"], "ts": int(now), "post": res["post"],
                "burst": res["burst"], "since_burst_s": res["since_burst_s"], "clamp": res["clamp"], "held": res.get("held", 0),
                "n_features": res["n_features"], "weak_label": res["weak"], "row_ts": row.get("ts"),
                "engine": "track-v1", "training": self.info, "trained_at": self.trained_at}

    def tick(self, now):
        """Read the new rows of today's file, step them in order, publish the newest one when it is fresh enough."""
        try:
            return self._tick(now)
        except Exception as e:   # noqa: BLE001 - one bad tick must not stop the loop
            log("tick: %s" % e)
            return None

    def _tick(self, now):
        self._maybe_refit(now)
        rows = self.tail.read(self._today_path(now))
        last = None
        for row in rows:
            if self.last_ts is not None and row["ts"] <= self.last_ts:
                continue
            res = self.tracker.step(row)
            if self.cal["state"] == "recording":
                self.cal["rows"].append(tr.features(row))
            self.last_ts = row["ts"]
            last = (row, res)
        if last is None:
            return None
        row, res = last
        t = row.get("_t")
        if t is None or now - t > MAX_AGE_S:
            return None
        return self.payload(row, res, now)


# ------------------------------------------------------------------ MQTT wrapper
def run_mqtt():
    import paho.mqtt.client as mqtt
    svc = Service()
    lock = threading.Lock()   # the MQTT thread (refit, calibration seeds) and the tick loop share svc
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="presence_track") if hasattr(mqtt, "CallbackAPIVersion") \
        else mqtt.Client(client_id="presence_track")
    c.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    c.will_set(T_AVAIL, "offline", qos=1, retain=True)

    def on_connect(cl, u, f, rc):
        for t in (T_CMD, T_CAL):
            cl.subscribe(t, qos=1)
        for oid, cfg in DISCOVERY.items():
            body = dict(cfg, availability_topic=T_AVAIL)   # no device block: the entity_id must stay sensor.rp_csi_*
            cl.publish("homeassistant/sensor/%s/config" % oid, json.dumps(body), qos=1, retain=True)
        cl.publish(T_AVAIL, "online", qos=1, retain=True)

    def on_message(cl, u, m):
        now = time.time()
        try:
            p = json.loads(m.payload)
            with lock:
                if m.topic == T_CMD:
                    svc.on_cmd(p, now)
                elif m.topic == T_CAL:
                    svc.on_cal_status(p, now)
        except Exception as e:   # noqa: BLE001 - a bad message must not kill the callback
            log("message %s: %s" % (m.topic, e))

    c.on_connect, c.on_message = on_connect, on_message
    c.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    c.loop_start()
    try:
        while True:
            now = time.time()
            time.sleep(max(0.5, PERIOD_S - (now % PERIOD_S) + 3))   # wall-clock 10 s grid + 3 s for the new zone-log row
            now = time.time()
            try:
                with lock:
                    p = svc.tick(now)
                if p is not None:
                    c.publish(T_STATE, json.dumps(p), qos=0, retain=True)
            except Exception as e:   # noqa: BLE001 - log one line and keep the loop alive
                log("tick: %s" % e)
    finally:
        c.publish(T_AVAIL, "offline", qos=1, retain=True)


if __name__ == "__main__":
    run_mqtt()
