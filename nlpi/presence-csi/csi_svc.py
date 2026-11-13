"""CSI shadow zoning service (nl-pi): device-free zone estimate from the WiFi CSI summaries, published NEXT TO the live zone.
It never writes anything the live logic reads: sensor.yui_room_zone, rp_feat_weight_db and the fingerprints are untouched.

  in   presence/<cyd|kitchen|pcdesk|workdesk>/csi   CSI summaries (~3 s)
       presence/zone/state                          HA context: live zone, BLE fp zone, PC / radar / light evidence (10 s)
       presence/calibrate/status + /cmd             the existing calibrate buttons: a finished recording also stores CSI samples
       presence/label/event + labels.db             label-site taps: the CSI of that moment becomes a sample (undo removes it)
  out  presence/csi/state       retained JSON  {zone, confidence, scores, used_nodes, baseline ..}   -> sensor.rp_csi_zone
       presence/csi/agreement   retained JSON  {agree_pct, vs live / weak labels / BLE ...}          -> sensor.rp_csi_agreement
       homeassistant/sensor/rp_csi_{zone,agreement}/config   MQTT discovery (retained), presence/csi/availability (LWT)
  files (STATE_DIR, default /var/lib/presence-csi): state.json.gz (baseline window + agreement ring + samples + label evals),
       feat-YYYY-MM-DD.csv (10 s raw + z features for later experiments, 14 days kept)

Windows are 10 s means like the zone log. Features: log1p(value) minus a rolling quiet floor over 8 h, in units of the window's
robust spread (csi_features.Baseline). A node that reboots loses its history (its firmware 'still' baseline restarts).
Training data per zone: calibrate-button and label-site samples; until a zone has >= MIN_REAL of them, weak-rule 'seed' windows
bootstrapped from the zone log stand in (marked in the attributes). Env: MQTT_* (presence-ble.env), CSI_DIR, ZONELOG_DIR,
LABEL_DB, CSI_SEED=0 to disable the seed fallback.
"""
import csv
import gzip
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque

import csi_features as cf
import csi_replay as rp

STATE_DIR = os.environ.get("CSI_DIR", "/var/lib/presence-csi")
ZONELOG_DIR = os.environ.get("ZONELOG_DIR", "/var/lib/zone-log")
LABEL_DB = os.environ.get("LABEL_DB", "/var/lib/presence-labels/labels.db")
USE_SEED = os.environ.get("CSI_SEED", "1") != "0"
PERIOD_S = 10
SETTLE_S = 5            # same as calibrate.record_zone
MIN_REAL = 3            # cal/label samples a zone needs before the seed stops standing in for it
MIN_FEATS = 3           # fewer usable z features than this -> state 'unknown'
CTX_STALE_S = 90
RING_LEN = 8640         # 24 h of 10 s windows
KEEP_DAYS = 14
SAVE_EVERY_S = 600
CSI_TOPIC_PREFIX = os.environ.get("CSI_TOPIC_PREFIX", "presence/csi")
T_STATE, T_AGREE, T_AVAIL = CSI_TOPIC_PREFIX + "/state", CSI_TOPIC_PREFIX + "/agreement", CSI_TOPIC_PREFIX + "/availability"
EXTRA = ("rssi", "rssi_sd", "mstd")


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def rnd(x, nd=2):
    return None if x is None else round(x, nd)


class Engine:
    """All logic, no MQTT: feed messages in, call tick(now) every PERIOD_S, read the returned payloads."""

    def __init__(self, state_dir=STATE_DIR, label_db=LABEL_DB, use_seed=USE_SEED):
        self.dir, self.label_db, self.use_seed = state_dir, label_db, use_seed
        self.lock = threading.RLock()
        self.msgs = {n: deque() for n in cf.NODES}       # node -> (t, payload)
        self.last_up, self.boot_epoch, self.reboots = {}, {}, {}
        self.bl = cf.Baseline()
        self.zbuf = deque(maxlen=cf.SMOOTH_N)
        self.samples = []        # {zone, z, src: cal|label, ts, id?}
        self.seed = []           # {zone, z, src: seed}
        self.label_evals = []    # {ts, id, label, csi, live, fp}
        self.ring = deque(maxlen=RING_LEN)   # (t, csi, live, fp, weak)
        self.ctx, self.ctx_t = {}, 0.0
        self.cal = {"state": "idle", "zone": None, "t0": 0.0, "z": []}
        self.model, self.train_info = cf.ZoneModel(weights=cf.FEATURE_WEIGHTS), {}
        self.cur = {"zone": "unknown", "conf": 0.0}
        self.agree_cache, self.agree_t = {}, 0.0
        self.last_save = time.time()
        self.notes = []
        os.makedirs(self.dir, exist_ok=True)
        self._load()
        self.refit()

    # ------------------------------------------------------------- persistence / bootstrap
    def _path(self):
        return os.path.join(self.dir, "state.json.gz")

    def _load(self):
        p = self._path()
        if os.path.exists(p):
            try:
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    d = json.load(f)
                self.bl.load(d.get("baseline", {}))
                self.samples = d.get("samples", [])
                self.seed = d.get("seed", [])
                self.label_evals = d.get("label_evals", [])
                self.ring.extend(tuple(x) for x in d.get("ring", []))
                self.boot_epoch = {k: float(v) for k, v in d.get("boot_epoch", {}).items()}
                self.notes.append("state loaded")
                return
            except (OSError, ValueError, KeyError) as e:
                self.notes.append("state unreadable: %s" % e)
        self.bootstrap()

    def bootstrap(self):
        """First start: replay the last days of the zone log through the baseline and cut the seed samples from it."""
        try:
            rows = rp.load_rows(ZONELOG_DIR, last_n_days=3)
        except OSError:
            rows = []
        if rows:
            self.bl, res = rp.run(rows, self.bl)
            self.bl.refresh(rows[-1]["_t"])
            self.seed = rp.seed_samples(res)
            self.notes.append("bootstrapped from %d zone-log rows, %d seed samples" % (len(rows), len(self.seed)))

    def save(self, now=None):
        d = {"baseline": self.bl.dump(), "samples": self.samples, "seed": self.seed, "label_evals": self.label_evals[-500:],
             "ring": [list(x) for x in self.ring], "boot_epoch": self.boot_epoch, "saved": int(now or time.time())}
        tmp = self._path() + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self._path())
        self.last_save = now or time.time()

    # ------------------------------------------------------------- model
    def refit(self):
        with self.lock:
            by = defaultdict(list)
            for s in self.samples:
                by[s["zone"]].append(s)
            train, info = [], {}
            for zone in cf.ZONES:
                real = by.get(zone, [])
                if len(real) >= MIN_REAL:
                    use, src = real, "cal+label"
                else:
                    seeded = [s for s in self.seed if s["zone"] == zone] if self.use_seed else []
                    use, src = (seeded, "seed") if seeded else (real, "cal+label")
                if use:
                    train += [(zone, s["z"]) for s in use]
                    info[zone] = {"src": src, "n": len(use)}
            self.model = cf.ZoneModel(weights=cf.FEATURE_WEIGHTS).fit(train) if train else cf.ZoneModel(weights=cf.FEATURE_WEIGHTS)
            self.train_info = info

    # ------------------------------------------------------------- inputs
    def on_csi(self, node, p, now):
        if node not in self.msgs:
            return
        with self.lock:
            up = p.get("up")
            if up is not None:
                up = float(up)
                lu = self.last_up.get(node)
                boot = now - up
                known = self.boot_epoch.get(node)
                if (lu is not None and up < lu - 30) or (known is not None and boot > known + 120):
                    self.reboots[node] = now
                    for k in cf.KINDS:
                        self.bl.reset("%s_%s" % (node, k))
                    self.notes.append("%s rebooted: baseline reset" % node)
                self.last_up[node] = up
                self.boot_epoch[node] = boot if known is None or boot > known + 120 else known
            if int(p.get("ok", 0)) == 1 and p.get("motion") is not None:
                q = self.msgs[node]
                q.append((now, p))
                while q and q[0][0] < now - 3 * PERIOD_S:
                    q.popleft()

    def on_ctx(self, p, now):
        with self.lock:
            self.ctx, self.ctx_t = p, now

    def on_cal_status(self, p, now):
        """Existing calibrate service status: capture z windows while it records, keep them only if it finishes 'done'."""
        with self.lock:
            st, zone = p.get("state"), p.get("zone")
            c = self.cal
            if st == "recording":
                if c["state"] != "recording" or c["zone"] != zone:
                    self.cal = {"state": "recording", "zone": zone, "t0": now, "z": []}
            elif st == "done" and c["state"] == "recording" and c["zone"] == zone:
                if zone in cf.ZONES and len(c["z"]) >= 3:
                    self.samples = [s for s in self.samples if not (s["src"] == "cal" and s["zone"] == zone)]
                    self.samples += [{"zone": zone, "z": z, "ts": int(t), "src": "cal"} for t, z in c["z"]]
                    self.notes.append("cal %s: %d samples" % (zone, len(c["z"])))
                    self.refit()
                self.cal = {"state": "idle", "zone": None, "t0": 0.0, "z": []}
            elif st in ("idle", "error", "offline") and c["state"] == "recording":
                self.cal = {"state": "idle", "zone": None, "t0": 0.0, "z": []}

    def on_cal_cmd(self, p):
        if p.get("action") == "clear" and p.get("zone") in cf.ZONES:
            with self.lock:
                self.samples = [s for s in self.samples if not (s["src"] == "cal" and s["zone"] == p["zone"])]
                self.refit()

    def ingest_label(self, lid, now):
        """label-site tap: its per-node 15 s CSI means become one sample (z against the current baseline)."""
        try:
            c = sqlite3.connect("file:%s?mode=ro" % self.label_db, uri=True, timeout=5)
            lab = c.execute("SELECT zone, ts FROM labels WHERE id=?", (lid,)).fetchone()
            rows = c.execute("SELECT node, csi_motion, csi_still FROM samples WHERE label_id=?", (lid,)).fetchall()
            c.close()
        except sqlite3.Error:
            return False
        if not lab or lab[0] not in cf.ZONES:
            return False
        raw = {}
        for node, m, s in rows:
            if node in cf.NODES:
                raw["%s_motion" % node] = m
                raw["%s_still" % node] = s if s is not None and s >= 0 else None
        with self.lock:
            z = self.bl.zvec(raw)
            if len(z) < MIN_FEATS:
                return False
            self.label_evals.append({"ts": int(lab[1]), "id": lid, "label": lab[0], "csi": self.cur["zone"],
                                     "live": self.ctx.get("zone") if now - self.ctx_t < CTX_STALE_S else None,
                                     "fp": self.ctx.get("fp") if now - self.ctx_t < CTX_STALE_S else None})
            self.samples.append({"zone": lab[0], "z": {k: round(v, 3) for k, v in z.items()}, "ts": int(lab[1]), "src": "label",
                                 "id": lid})
            self.refit()
        return True

    def sync_labels(self):
        """Drop label samples whose label was undone in label-site."""
        try:
            c = sqlite3.connect("file:%s?mode=ro" % self.label_db, uri=True, timeout=5)
            ids = {r[0] for r in c.execute("SELECT id FROM labels")}
            c.close()
        except sqlite3.Error:
            return
        with self.lock:
            keep = [s for s in self.samples if s["src"] != "label" or s.get("id") in ids]
            if len(keep) != len(self.samples):
                self.samples = keep
                self.label_evals = [e for e in self.label_evals if e["id"] in ids]
                self.refit()

    # ------------------------------------------------------------- the 10 s step
    def window(self, now):
        raw, extra = {}, {}
        for node in cf.NODES:
            ps = [p for t, p in self.msgs[node] if t > now - PERIOD_S]
            m = [float(p["motion"]) for p in ps]
            s = [float(p["still"]) for p in ps if p.get("still") is not None and float(p["still"]) >= 0]
            raw[node + "_motion"] = mean(m)
            raw[node + "_still"] = mean(s)
            for k in EXTRA:
                extra["%s_%s" % (node, k)] = mean([float(p[k]) for p in ps if p.get(k) is not None])
            gs = [p["gstd"] for p in ps if isinstance(p.get("gstd"), list) and len(p["gstd"]) == 8]
            for i in range(8):
                extra["%s_g%d" % (node, i)] = mean([g[i] for g in gs])
        return raw, extra

    def tick(self, now):
        """-> (state_payload, agreement_payload, feature_row)."""
        with self.lock:
            raw, extra = self.window(now)
            self.bl.tick(now)
            z = self.bl.zvec(raw)
            for n, v in raw.items():
                self.bl.add(now, n, v)
            self.zbuf.append(z)
            zs = rp.smooth(self.zbuf)
            if self.cal["state"] == "recording" and now - self.cal["t0"] >= SETTLE_S and len(z) >= MIN_FEATS:
                self.cal["z"].append((now, {k: round(v, 3) for k, v in z.items()}))
            dists, used = ({}, [])
            zone, conf = "unknown", 0.0
            if len(self.model.mu) >= 2 and len(zs) >= MIN_FEATS:
                dists, used = self.model.dist(zs)
                best, conf = cf.decide(dists)
                if best and len(used) >= MIN_FEATS:
                    zone = best
            fresh = now - self.ctx_t < CTX_STALE_S
            live = self.ctx.get("zone") if fresh else None
            fp = self.ctx.get("fp") if fresh else None
            weak = cf.weak_from_ctx(self.ctx, now) if fresh else None
            self.cur = {"zone": zone, "conf": conf}
            self.ring.append((int(now), zone, live, fp, weak))
            if now - self.agree_t >= 60:
                self.agree_cache, self.agree_t = self.agreement(now), now
            used_nodes = sorted({u.rsplit("_", 1)[0] for u in used if u.endswith("_still")})
            info = self.bl.info(now)
            ages = [v["quiet_age_min"] for k, v in info.items() if k.endswith("_still") and v["quiet_age_min"] is not None]
            state = {"zone": zone, "confidence": conf, "scores": {k: rnd(v, 3) for k, v in sorted(dists.items())},
                     "used_nodes": used_nodes, "n_features": len(used), "live_zone": live, "fp_zone": fp, "weak_label": weak,
                     "z_still": {k.rsplit("_", 1)[0]: rnd(v, 2) for k, v in zs.items() if k.endswith("_still")},
                     "baseline": {k.rsplit("_", 1)[0]: v for k, v in info.items() if k.endswith("_still")},
                     "baseline_span_h": max([v["span_h"] for k, v in info.items() if k.endswith("_still")], default=0),
                     "baseline_quiet_age_min": min(ages) if ages else None,
                     "baseline_nodes": sorted(k.rsplit("_", 1)[0] for k in info if k.endswith("_still")),
                     "training": self.train_info, "calibrating": self.cal["zone"] if self.cal["state"] == "recording" else None,
                     "reboots": {n: int(t) for n, t in self.reboots.items()}, "ts": int(now)}
            row = [time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), zone, conf, live or "", fp or "", weak or ""]
            for node in cf.NODES:
                row += [rnd(raw[node + "_motion"], 1), rnd(raw[node + "_still"], 1)]
                row += [rnd(extra["%s_%s" % (node, k)], 1) for k in EXTRA]
                row += [rnd(extra["%s_g%d" % (node, i)], 1) for i in range(8)]
            row += [rnd(z.get(n), 2) for n in cf.NAMES]
            return state, dict(self.agree_cache), row

    # ------------------------------------------------------------- agreement
    def agreement(self, now):
        def acc(pairs):
            return (sum(1 for a, b in pairs if a == b) / len(pairs)) if pairs else None

        def bal(pairs):
            by = defaultdict(list)
            for truth, pred in pairs:
                by[truth].append(truth == pred)
            rec = [sum(v) / len(v) for v in by.values() if len(v) >= 5]
            return (sum(rec) / len(rec)) if rec else None

        out = {}
        for tag, h in (("1h", 3600), ("24h", 86400)):
            recs = [r for r in self.ring if r[0] >= now - h]
            live = [(r[1], r[2]) for r in recs if r[1] in cf.ZONES and r[2] in cf.ZONES]
            fpp = [(r[1], r[3]) for r in recs if r[1] in cf.ZONES and r[3] in cf.ZONES]
            weak = [r for r in recs if r[4] in cf.ZONES]
            wc = [(r[4], r[1]) for r in weak if r[1] in cf.ZONES]
            wb_fp = [(r[4], r[3]) for r in weak if r[3] in cf.ZONES and r[1] in cf.ZONES]
            wb_live = [(r[4], r[2]) for r in weak if r[2] in cf.ZONES and r[1] in cf.ZONES]
            out["vs_live_" + tag] = rnd(acc(live), 3)
            out["n_live_" + tag] = len(live)
            out["vs_fp_" + tag] = rnd(acc(fpp), 3)
            out["weak_acc_" + tag] = rnd(acc(wc), 3)
            out["weak_bal_" + tag] = rnd(bal(wc), 3)
            out["n_weak_" + tag] = len(wc)
            out["ble_fp_weak_bal_" + tag] = rnd(bal(wb_fp), 3)
            out["ble_fp_weak_acc_" + tag] = rnd(acc(wb_fp), 3)
            out["live_weak_bal_" + tag] = rnd(bal(wb_live), 3)   # shares evidence with the rules: optimistic
        ev = [e for e in self.label_evals if e["ts"] >= now - 7 * 86400]
        out["labels_7d"] = len(ev)
        out["csi_vs_labels_7d"] = rnd(acc([(e["label"], e["csi"]) for e in ev]), 3)
        out["live_vs_labels_7d"] = rnd(acc([(e["label"], e["live"]) for e in ev if e["live"]]), 3)
        out["fp_vs_labels_7d"] = rnd(acc([(e["label"], e["fp"]) for e in ev if e["fp"]]), 3)
        base = out.get("weak_bal_24h")
        out["agree_pct"] = None if base is None else round(100 * base, 1)
        out["ts"] = int(now)
        return out


# ------------------------------------------------------------------ feature log
def feat_head():
    h = ["ts", "csi_zone", "csi_conf", "live", "fp", "weak"]
    for n in cf.NODES:
        h += ["%s_motion" % n, "%s_still" % n] + ["%s_%s" % (n, k) for k in EXTRA] + ["%s_g%d" % (n, i) for i in range(8)]
    return h + ["z_" + n for n in cf.NAMES]


def append_feat(dirpath, row, now):
    path = os.path.join(dirpath, "feat-%s.csv" % time.strftime("%Y-%m-%d", time.localtime(now)))
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(feat_head())
            cutoff = now - KEEP_DAYS * 86400
            for fn in os.listdir(dirpath):
                fp = os.path.join(dirpath, fn)
                if fn.startswith("feat-") and fn.endswith(".csv") and os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
        w.writerow(row)


# ------------------------------------------------------------------ MQTT wrapper
DISCOVERY = {
    "rp_csi_zone": {"name": "RP csi zone", "unique_id": "rp_csi_zone", "state_topic": T_STATE,
                    "value_template": "{{ value_json.zone }}", "json_attributes_topic": T_STATE, "icon": "mdi:wifi-marker",
                    "expire_after": 90},
    "rp_csi_label": {"name": "RP csi label", "unique_id": "rp_csi_label", "state_topic": T_STATE,
                     "value_template": "{{ value_json.zone }} \u00b7 {{ (value_json.confidence * 100) | round(0) | int }}%",
                     "icon": "mdi:wifi-marker", "expire_after": 90},
    "rp_csi_agreement": {"name": "RP csi agreement", "unique_id": "rp_csi_agreement", "state_topic": T_AGREE,
                         "value_template": "{{ value_json.agree_pct }}", "json_attributes_topic": T_AGREE,
                         "unit_of_measurement": "%", "icon": "mdi:check-decagram", "expire_after": 300},
}


def run_mqtt():
    import paho.mqtt.client as mqtt
    eng = Engine()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="csi_shadow") if hasattr(mqtt, "CallbackAPIVersion") \
        else mqtt.Client(client_id="csi_shadow")
    c.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    c.will_set(T_AVAIL, "offline", qos=1, retain=True)

    def on_connect(cl, u, f, rc):
        for t in ("presence/+/csi", "presence/zone/state", "presence/calibrate/status", "presence/calibrate/cmd",
                  "presence/label/event"):
            cl.subscribe(t, qos=1)
        for oid, cfg in (DISCOVERY.items() if CSI_TOPIC_PREFIX == "presence/csi" else ()):
            body = dict(cfg, availability_topic=T_AVAIL)   # no device block: the entity_id must stay sensor.rp_csi_*
            cl.publish("homeassistant/sensor/%s/config" % oid, json.dumps(body), qos=1, retain=True)
        cl.publish(T_AVAIL, "online", qos=1, retain=True)

    def on_message(cl, u, m):
        now = time.time()
        try:
            p = json.loads(m.payload)
            parts = m.topic.split("/")
            if m.topic == "presence/zone/state":
                eng.on_ctx(p, now)
            elif m.topic == "presence/calibrate/status":
                eng.on_cal_status(p, now)
            elif m.topic == "presence/calibrate/cmd":
                eng.on_cal_cmd(p)
            elif m.topic == "presence/label/event":
                threading.Timer(2.0, eng.ingest_label, args=(int(p["id"]), now)).start()
            elif len(parts) == 3 and parts[2] == "csi":
                eng.on_csi(parts[1], p, now)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass

    c.on_connect, c.on_message = on_connect, on_message
    c.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    c.loop_start()
    last_sync = 0.0
    try:
        while True:
            now = time.time()
            time.sleep(max(0.5, PERIOD_S - (now % PERIOD_S)))
            now = time.time()
            state, agree, row = eng.tick(now)
            c.publish(T_STATE, json.dumps(state), qos=0, retain=True)
            c.publish(T_AGREE, json.dumps(agree), qos=0, retain=True)
            try:
                append_feat(STATE_DIR, row, now)
            except OSError:
                pass
            if now - last_sync > 60:
                eng.sync_labels()
                last_sync = now
            if now - eng.last_save > SAVE_EVERY_S:
                eng.save(now)
    finally:
        try:
            eng.save()
        except OSError:
            pass
        c.publish(T_AVAIL, "offline", qos=1, retain=True)


if __name__ == "__main__":
    run_mqtt()
