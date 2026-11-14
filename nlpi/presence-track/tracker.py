"""Presence tracker: pure logic (stdlib only) for the fused zone estimate that replaces the CSI zone classifier.

  in   zone-v3-YYYY-MM-DD.csv rows (one per 10 s, the zone log): BLE RSSI of phone/watch per node, CSI motion / still per node,
       plus the context columns that the weak rule reads (home, PC lock/idle, radar, work-desk light, kitchen light)
  out  per row a zone estimate (kitchen, work_desk, pc_desk, bed, bathroom), the posterior over the zones, a clamp reason and
       the walking burst state; tracker_svc publishes it on presence/csi/state, tracker_replay scores it offline

The model is a Gaussian naive Bayes over the per-zone feature distributions (mean + variance of raw RSSI and of log1p(CSI)),
with a variance floor per feature kind. The filter is an HMM whose transition matrix is a fixed "stay" probability that is
low for a few ticks after CSI motion bursts on two or more nodes. Weak labels (csi_features.weak_from_row) train the model and
can clamp the posterior for a row; they never change the state directly, so one noisy rule flip cannot move the zone.
Seeds add zones the weak rules never produce (bathroom) and repair weak zones with too few features.
Stdlib only, Python >= 3.9 (nl-pi has 3.13, no numpy).
"""
import csv
import datetime
import glob
import json
import math
import os
import sys
import time
from collections import deque

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(os.path.dirname(_HERE), "presence-csi"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import csi_features as cf  # noqa: E402

ZONES = ("kitchen", "work_desk", "pc_desk", "bed", "bathroom")   # zones the filter can output
WEAK_ZONES = ("kitchen", "work_desk", "pc_desk", "bed")          # zones the weak rules can produce
BLE_NODES = ("cyd", "kitchen", "nlpi", "pcdesk", "workdesk")     # nodes with phone / watch RSSI columns
CSI_NODES = ("cyd", "kitchen", "pcdesk", "workdesk")             # nodes with CSI motion / still columns
TRAIN_DAYS = 7           # days of zone log used for a model
TRAIN_SUB = 3            # keep every Nth row when training (30 s sampling)
MIN_N = 60               # values a feature needs in a zone before it enters the model
VAR_FLOOR_CSI = 0.05     # variance floor for log1p(CSI) features
VAR_FLOOR_RSSI = 16.0    # variance floor for BLE RSSI features (4 dBm sd)
TEMP = 0.75              # emission temperature: divide the summed log density by len(common) ** TEMP
S_QUIET = 0.9995         # HMM stay probability without a recent burst
S_MOVE = 0.85            # HMM stay probability while a burst is recent
HOLD_TICKS = 3           # ticks after a burst that use S_MOVE
BURST_WIN = 180          # walking detector window per CSI node (30 min of 10 s rows)
BURST_MIN = 30           # values before the walking detector can fire
BURST_K = 4.0            # burst threshold: median + K * MAD
BURST_REL = 0.25         # burst threshold: median * (1 + REL)
BURST_ABS = 3.0          # burst threshold: median + ABS (raw motion units)
BURST_NEED = 2           # nodes that must burst in the same row to reset the stay probability
SWITCH_CONF = 0.7        # the reported zone changes only when the new zone's posterior reaches this (a clamp always wins)
HELD_MAX = 12            # ... or after this many rows (2 min) with another zone ahead; a held zone reports >= HELD_CONF
HELD_CONF = 0.15         # confidence floor of a held zone (= HA rp_csi_min_conf, so HA keeps it instead of the BLE fallback)
CLAMP_EPS = 1e-4         # posterior of the other zones on a weak-rule clamp
POST_FLOOR = 1e-6        # lowest stored posterior value
SEED_VAR_MULT = 1.5      # seed variance before a feature has MIN_N seed values
MIN_ZONE_FEATS = 6       # fewer features than this and a zone is replaced by its seeds
SEED_SOURCES = ("cal", "manual")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_ts(ts):
    """Local ISO timestamp string -> datetime (naive) or None."""
    try:
        return datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def row_hour(row):
    """Hour of a zone-log row: the parsed _hour of load_rows, else from its ts, else 0."""
    h = row.get("_hour")
    if isinstance(h, int):
        return h
    dt = parse_ts(row.get("ts"))
    return dt.hour if dt is not None else 0


def features(row):
    """Zone-log row (strings, empty = no value) -> {feature name: float}. Missing / unparsable values are skipped."""
    out = {}
    for node in BLE_NODES:
        v = _f(row.get(node + "_watch"))
        if v is not None:
            out["w_" + node] = v
        v = _f(row.get(node + "_phone"))
        if v is not None:
            out["p_" + node] = v
    for node in CSI_NODES:
        for kind in ("motion", "still"):
            v = _f(row.get("csi_%s_%s" % (node, kind)))
            if v is not None and v >= 0:   # -1 = the node has no still baseline yet
                out["c_%s_%s" % (node, kind)] = math.log1p(v)
    return out


def fit(samples):
    """samples: iterable of (zone, {name: value}) -> {zone: {name: (mean, var)}} with MIN_N and variance floors."""
    by = {}
    for zone, feats in samples:
        by.setdefault(zone, []).append(feats)
    model = {}
    for zone, rows in by.items():
        cols = {}
        for feats in rows:
            for name, v in feats.items():
                cols.setdefault(name, []).append(v)
        entry = {}
        for name, vals in cols.items():
            if len(vals) < MIN_N:
                continue
            mu = sum(vals) / len(vals)
            var = sum((x - mu) ** 2 for x in vals) / len(vals)
            entry[name] = (mu, max(var, VAR_FLOOR_CSI if name.startswith("c_") else VAR_FLOOR_RSSI))
        model[zone] = entry
    return model


def add_seeds(model, seeds):
    """Add / repair zones from seed feature rows. seeds = {zone: [feature dict, ...]}; model is updated in place."""
    pooled = {}
    for entry in model.values():
        for name, (mu, var) in entry.items():
            pooled.setdefault(name, []).append(var)
    pooled = {name: sum(v) / len(v) for name, v in pooled.items()}
    for zone in ZONES:
        rows = seeds.get(zone) or []
        if not rows or (zone in model and len(model[zone]) >= MIN_ZONE_FEATS):
            continue
        cols = {}
        for feats in rows:
            for name, v in feats.items():
                cols.setdefault(name, []).append(v)
        entry = {}
        for name, vals in cols.items():
            if len(vals) < 3 or name not in pooled:
                continue
            mu = sum(vals) / len(vals)
            if len(vals) >= MIN_N:
                var = sum((x - mu) ** 2 for x in vals) / len(vals)
                var = max(var, VAR_FLOOR_CSI if name.startswith("c_") else VAR_FLOOR_RSSI)
            else:
                var = pooled[name] * SEED_VAR_MULT
            entry[name] = (mu, var)
        model[zone] = entry
    for zone in list(model):
        if not model[zone]:
            del model[zone]
    return model


def common_features(model, feats):
    """Feature names present in feats AND in every zone of the model (sorted, so the sum is deterministic)."""
    if not model:
        return []
    names = set(feats)
    for entry in model.values():
        names &= set(entry)
    return sorted(names)


def emission(model, feats):
    """{zone: Gaussian log density over the common features / len(common) ** TEMP}, or None without common features."""
    common = common_features(model, feats)
    if not common:
        return None
    out = {}
    for zone, entry in model.items():
        acc = 0.0
        for name in common:
            mu, var = entry[name]
            acc += -0.5 * math.log(var) - 0.5 * (feats[name] - mu) ** 2 / var
        out[zone] = acc / (len(common) ** TEMP)
    return out


def median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


class Burst:
    """Walking detector on the raw CSI motion of each node: a node bursts when its value jumps out of its recent median."""

    def __init__(self):
        self.motion = {n: deque(maxlen=BURST_WIN) for n in CSI_NODES}

    def step(self, row):
        """-> number of CSI nodes that burst in this row (the value enters the window after the test)."""
        hits = 0
        for node in CSI_NODES:
            v = _f(row.get("csi_%s_motion" % node))
            if v is None:
                continue
            q = self.motion[node]
            if len(q) >= BURST_MIN:
                med = median(q)
                mad = median([abs(x - med) for x in q]) * 1.4826
                if v > med + max(BURST_K * mad, BURST_REL * med, BURST_ABS):
                    hits += 1
            q.append(v)
        return hits


class Tracker:
    """Motion-gated HMM over the model's zones. post survives away rows; a burst only opens the transition matrix.

    The reported zone is sticky: it follows the posterior's winner only at SWITCH_CONF or on a clamp."""

    def __init__(self, model):
        self.model = model
        zones = list(model)
        self.post = {z: 1.0 / len(zones) for z in zones} if zones else {}
        self.since = 10 ** 6
        self.burst = Burst()
        self.zone = None     # reported zone (sticky, see SWITCH_CONF)
        self.held = 0        # rows the reported zone has been held against another winner

    def step(self, row):
        """One zone-log row -> zone, confidence, posterior, burst state, clamp, feature count, weak label, away flag."""
        hits = self.burst.step(row)
        burst = hits >= BURST_NEED
        if len(self.model) < 2:
            return {"zone": "unknown", "confidence": 0.0, "post": {}, "burst": burst,
                    "since_burst_s": self.since * 10, "clamp": None, "n_features": 0, "weak": None, "away": False}
        weak = cf.weak_from_row(row, row_hour(row))
        if row.get("home") == "off" or weak == "away":
            return {"zone": "unknown", "confidence": 0.0, "post": {z: round(v, 3) for z, v in self.post.items()},
                    "burst": burst, "since_burst_s": self.since * 10, "clamp": None, "n_features": 0,
                    "weak": weak, "away": True}
        self.since = 0 if burst else self.since + 1
        s = S_MOVE if self.since <= HOLD_TICKS else S_QUIET
        zones = list(self.model)
        n = len(zones)
        prior = {z: s * self.post[z] + (1.0 - s) * (1.0 - self.post[z]) / (n - 1) for z in zones}
        feats = features(row)
        e = emission(self.model, feats)
        if e:
            mx = max(e.values())
            po = {z: prior[z] * math.exp(e[z] - mx) for z in zones}
            nfeat = len(common_features(self.model, feats))
        else:
            po, nfeat = dict(prior), 0
        clamp = None
        if weak in ("pc_desk", "kitchen") and weak in self.model:
            clamp = "pc_active" if weak == "pc_desk" else "radar"
            po = {z: (1.0 if z == weak else CLAMP_EPS) for z in zones}
        tot = sum(po.values())
        post = {z: max(po[z] / tot, POST_FLOOR) for z in zones} if tot > 0 else {z: 1.0 / n for z in zones}
        self.post = post
        top = max(zones, key=lambda z: post[z])
        if self.zone not in post or clamp or post[top] >= SWITCH_CONF:
            self.zone, self.held = top, 0
        elif top != self.zone:
            self.held += 1    # a weak, short-lived winner (turning over in bed) does not move the reported zone
            if self.held > HELD_MAX:
                self.zone, self.held = top, 0
        else:
            self.held = 0
        zone = self.zone
        conf = post[zone] if zone == top else max(post[zone], HELD_CONF)
        return {"zone": zone, "confidence": round(conf, 3), "held": self.held, "post": {z: round(post[z], 3) for z in zones},
                "burst": burst, "since_burst_s": self.since * 10, "clamp": clamp, "n_features": nfeat,
                "weak": weak, "away": False}


def load_rows(path):
    """CSV rows of one zone log file (strings, file order); rows without a parsable ts are skipped. Missing file -> []."""
    rows = []
    try:
        fh = open(path, newline="", encoding="utf-8")
    except OSError:
        return rows
    with fh:
        for r in csv.DictReader(fh):
            dt = parse_ts(r.get("ts"))
            if dt is None:
                continue
            r["_t"] = time.mktime(dt.timetuple())
            r["_day"] = r["ts"][:10]
            r["_hour"] = dt.hour
            rows.append(r)
    return rows


def list_days(logdir):
    """Sorted paths of the zone-v3-*.csv files in logdir."""
    return sorted(glob.glob(os.path.join(logdir, "zone-v3-*.csv")))


def merge_seeds(by_src):
    """{"cal": {zone: rows}, "manual": {zone: rows}} -> {zone: rows} with both sources together."""
    out = {}
    for src in SEED_SOURCES:
        for zone, rows in (by_src.get(src) or {}).items():
            out.setdefault(zone, []).extend(rows)
    return out


def _seed_map(seeds):
    if not seeds:
        return {}
    if "cal" in seeds or "manual" in seeds:
        return merge_seeds(seeds)
    return seeds


def train(logdir, day_files, seeds=None):
    """Fit on rows[::TRAIN_SUB] of day_files with a weak label in WEAK_ZONES, then add the seed rows.

    -> (model, {zone: {"n": rows used, "src": "weak"|"seed", "feats": number of features}}).
    """
    seed_map = _seed_map(seeds)
    samples, by = [], {}
    for path in day_files:
        for row in load_rows(path)[::TRAIN_SUB]:
            weak = cf.weak_from_row(row, row_hour(row))
            if weak in WEAK_ZONES:
                samples.append((weak, features(row)))
                by[weak] = by.get(weak, 0) + 1
    model = fit(samples)
    pre = {z: set(entry) for z, entry in model.items()}
    add_seeds(model, seed_map)
    info = {}
    for zone, entry in model.items():
        seeded = (zone not in pre or len(pre[zone]) < MIN_ZONE_FEATS) and bool(seed_map.get(zone))
        info[zone] = {"n": len(seed_map.get(zone) or []) if seeded else by.get(zone, 0),
                      "src": "seed" if seeded else "weak", "feats": len(entry)}
    return model, info


def load_seeds(path):
    """Seed file -> {"cal": {zone: [feature dicts]}, "manual": {zone: [feature dicts]}}; missing / bad file -> empty."""
    out = {src: {} for src in SEED_SOURCES}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return out
    for src in SEED_SOURCES:
        for zone, rows in (d.get(src) or {}).items():
            out[src][zone] = list(rows)
    return out


def save_seeds(path, seeds_by_src):
    """Write the seed file (JSON, cal + manual) atomically, creating its directory."""
    d = {src: seeds_by_src.get(src) or {} for src in SEED_SOURCES}
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, path)
