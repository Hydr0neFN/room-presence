#!/usr/bin/env python3
"""Offline replay of the CSI shadow pipeline (baseline + classifier) on zone-log CSVs.

  csi_replay.py --logdir /var/lib/zone-log                 # cross-validated accuracy vs rule weak labels, vs BLE
  csi_replay.py --logdir DIR --days 2026-10-28,2026-10-29  # only some days
  csi_replay.py --logdir DIR --compare                     # also static-normalisation and baseline-window sweeps

CV = leave-one-day-out over the days that have weak labels: the model is fitted on the z windows of the other days and
scored on the held-out day. The weak labels are RULES on non-CSI context (csi_features.weak_label), not ground truth:
  pc_desk = PC active; kitchen = radar on; work_desk = work-desk light just operated; bed = night with the PC quiet.
BLE columns of the same CSVs are scored against the same labels: fp_zone (BLE fingerprint only) and zone (the live
yui_room_zone, which shares evidence with the rules, so its score is optimistic and shown only for reference).
The functions load_rows / run / seed_samples are also used by the service to bootstrap its baseline from the zone log.
Stdlib only (runs on nl-pi without numpy). Python >= 3.9.
"""
import argparse
import collections
import csv
import datetime
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import csi_features as cf  # noqa: E402


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_rows(logdir, days=None, last_n_days=None):
    files = sorted(glob.glob(os.path.join(logdir, "zone-v3-*.csv")))
    if days:
        files = [f for f in files if any(d in f for d in days)]
    elif last_n_days:
        files = files[-last_n_days:]
    rows = []
    for f in files:
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    dt = datetime.datetime.fromisoformat(r["ts"])
                except (ValueError, KeyError):
                    continue
                r["_t"] = time.mktime(dt.timetuple())
                r["_day"] = r["ts"][:10]
                r["_hour"] = dt.hour
                rows.append(r)
    return rows


def raw_of(r):
    return {n: num(r.get("csi_" + n)) for n in cf.NAMES}


def smooth(buf):
    acc = {}
    for zv in buf:
        for n, v in zv.items():
            acc.setdefault(n, []).append(v)
    return {n: sum(v) / len(v) for n, v in acc.items()}


def run(rows, baseline=None, smooth_n=cf.SMOOTH_N):
    """Feed the rows through a Baseline in order. Returns (baseline, [dict(row, t, z, zs, label)]).
    z = this window's z vector (before the window joins the baseline), zs = mean of the last smooth_n z vectors."""
    bl = baseline or cf.Baseline()
    buf = collections.deque(maxlen=smooth_n)
    out = []
    t0 = rows[0]["_t"] if rows else 0
    for r in rows:
        t = r["_t"]
        raw = raw_of(r)
        bl.tick(t)
        z = bl.zvec(raw)
        for n, v in raw.items():
            bl.add(t, n, v)
        buf.append(z)
        out.append({"row": r, "t": t, "z": z, "zs": smooth(buf), "label": cf.weak_from_row(r, r["_hour"]),
                    "warm": t < t0 + bl.window_s})   # first window of the log: no quiet reference yet, not scored
    return bl, out


def seed_samples(res, per_zone=1500):
    """Evenly thinned (zone, zs) samples of the weak-labelled windows, for bootstrapping a model before any calibration."""
    by = collections.defaultdict(list)
    for x in res:
        if x["label"] in cf.ZONES and len(x["zs"]) >= 3 and not x.get("warm"):
            by[x["label"]].append(x)
    out = []
    for zone, xs in by.items():
        step = max(1, len(xs) // per_zone)
        for x in xs[::step]:
            out.append({"zone": zone, "z": {k: round(v, 3) for k, v in x["zs"].items()}, "ts": int(x["t"]), "src": "seed"})
    return out


def new_model():
    return cf.ZoneModel(weights=cf.FEATURE_WEIGHTS)


def predict(model, zs):
    if len(zs) < 3:
        return None
    d, _ = model.dist(zs)
    return cf.decide(d)[0]


def metrics(conf):
    """conf {(true, pred): n} -> (n, acc, balanced acc, {zone: recall}, {zone: n})."""
    zones = sorted({a for a, _ in conf})
    n = sum(conf.values())
    acc = sum(v for (a, b), v in conf.items() if a == b) / max(n, 1)
    rec, cnt = {}, {}
    for z in zones:
        tot = sum(v for (a, _), v in conf.items() if a == z)
        cnt[z] = tot
        rec[z] = sum(v for (a, b), v in conf.items() if a == z and b == z) / max(tot, 1)
    bal = sum(rec.values()) / max(len(rec), 1)
    return n, acc, bal, rec, cnt


def fmt(name, conf):
    n, acc, bal, rec, cnt = metrics(conf)
    return "%-34s n=%5d  acc=%.3f  bal=%.3f  recall %s" % (
        name, n, acc, bal, " ".join("%s=%.2f" % (z, rec[z]) for z in sorted(rec)))


def cv_by_day(res, sub=None, model_factory=new_model):
    """Leave-one-day-out. sub(x) -> bool restricts the scored windows. Returns the confusion Counter."""
    days = sorted({x["row"]["_day"] for x in res})
    conf = collections.Counter()
    for d in days:
        train = [(x["label"], x["zs"]) for x in res if x["row"]["_day"] != d and x["label"] in cf.ZONES and len(x["zs"]) >= 3
                 and not x.get("warm")]
        if len({z for z, _ in train}) < 2:
            continue
        m = model_factory().fit(train)
        for x in res:
            if x["row"]["_day"] == d and x["label"] in cf.ZONES and not x.get("warm") and (sub is None or sub(x)):
                p = predict(m, x["zs"])
                if p:
                    conf[(x["label"], p)] += 1
    return conf


def ble_conf(res, col, sub=None):
    conf = collections.Counter()
    for x in res:
        if x["label"] in cf.ZONES and x["row"].get(col) in cf.ZONES and not x.get("warm") and (sub is None or sub(x)):
            conf[(x["label"], x["row"][col])] += 1
    return conf


def static_results(rows):
    """Baseline-free comparison: one global median/MAD per feature over the whole log."""
    stat = {}
    for n in cf.NAMES:
        v = sorted(cf.g(num(r["csi_" + n])) for r in rows if num(r.get("csi_" + n)) is not None)
        med = cf.quantile(v, 0.5)
        stat[n] = (med, max(cf.quantile(sorted(abs(x - med) for x in v), 0.5) * 1.4826, cf.SCALE_FLOOR))
    buf = collections.deque(maxlen=cf.SMOOTH_N)
    out = []
    t0 = rows[0]["_t"]
    for r in rows:
        z = {n: (cf.g(v) - stat[n][0]) / stat[n][1] for n, v in raw_of(r).items() if v is not None}
        buf.append(z)
        out.append({"row": r, "t": r["_t"], "z": z, "zs": smooth(buf), "label": cf.weak_from_row(r, r["_hour"]),
                    "warm": r["_t"] < t0 + cf.WINDOW_S})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logdir", default="/var/lib/zone-log")
    ap.add_argument("--days", default="")
    ap.add_argument("--compare", action="store_true", help="also sweep baseline window / quantile and a static normalisation")
    a = ap.parse_args()
    rows = load_rows(a.logdir, [d for d in a.days.split(",") if d] or None)
    if not rows:
        sys.exit("no zone-v3 rows in " + a.logdir)
    bl, res = run(rows)
    days = sorted({r["_day"] for r in rows})
    labs = collections.Counter(x["label"] for x in res)
    print("rows %d  days %s  first %s  last %s" % (len(rows), ",".join(days), rows[0]["ts"], rows[-1]["ts"]))
    print("weak labels:", dict(labs))
    night = lambda x: x["row"]["_hour"] >= 23 or x["row"]["_hour"] < 7  # noqa: E731
    print("\n== zones scored against rule weak labels (windows with a z vector of >= 3 features) ==")
    print(fmt("CSI baseline+classifier, all", cv_by_day(res)))
    print(fmt("CSI night 23-07", cv_by_day(res, night)))
    print(fmt("CSI day 07-23", cv_by_day(res, lambda x: not night(x))))
    print(fmt("BLE fingerprint (fp_zone)", ble_conf(res, "fp_zone")))
    print(fmt("BLE live zone (optimistic)", ble_conf(res, "zone")))
    for d in days:
        sub = lambda x, d=d: x["row"]["_day"] == d  # noqa: E731
        print(fmt("CSI held-out day " + d, cv_by_day(res, sub)))
    conf = cv_by_day(res)
    print("\nconfusion (rows = weak label, cols = CSI):")
    zs = sorted({a for a, _ in conf} | {b for _, b in conf})
    print("%-10s" % "" + "".join("%10s" % z for z in zs))
    for t in zs:
        print("%-10s" % t + "".join("%10d" % conf.get((t, p), 0) for p in zs))
    if a.compare:
        print("\n== variants ==")
        print(fmt("static global normalisation", cv_by_day(static_results(rows))))
        for wh in (4, 6, 8, 12, 24):
            for q in (0.1, 0.2):
                _, r2 = run(rows, cf.Baseline(window_s=wh * 3600, loc_q=q))
                print(fmt("baseline window %2dh q=%.1f" % (wh, q), cv_by_day(r2)))


if __name__ == "__main__":
    main()
