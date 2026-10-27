"""Offline evaluation of fingerprint zone matchers (stdlib only).

  python tools/fp_eval.py cv   export.csv [--scale zone|label] [--purge-s N] [--cv blocks|visits]
  python tools/fp_eval.py live fingerprints.json snaps.txt export.csv
  python tools/fp_eval.py weak export.csv zone-v2-*.csv [--cap 300] [--norm raw]

weak: rows of the zone-v2 log (nlpi/zone_log.py) with a non-empty weak_label (passive rules, nlpi/weak_label.py) are used as
labelled samples: train weak -> test on the manual labels, train manual -> test on the weak rows, and CV on the weak rows blocked
by day (fewer than 2 days: 4 contiguous time blocks with a 60 s purge, flagged). Per zone at most --cap rows (evenly spaced).

export.csv = label-site export (http://10.0.0.20:8097/api/export.csv, one row per label x node, columns label_id, time,
zone, node, phone_avg, csi_motion, csi_still ...). snaps.txt = repeated blocks "### HH:MM:SS" followed by the output of
ha/ha_states.py ("entity state" lines) for _signal, csi_still, rp_link_ entities.

The distance is the one of sensor.rp_fp_zone in ha/room_presence.yaml:
  d2 = (sum_ble (s-mu)^2 + w^2 sum_feat ((x-mu)/sd)^2) / (n_nodes + n_feat_used)      (unheard BLE = -100)
Methods: median (one centroid per zone, what is deployed), kmK (K sub-centroids per zone from k-medians, min distance),
kitK (sub-centroids for kitchen only), knnK (K nearest labels, majority vote).
Feature normalisation: raw (levels), ratio (x / median of the same snapshot over the CSI nodes, per feature type),
logratio (log of that). Scale sd: 'zone' = pstdev of the zone medians with a floor (as calibrate.py build_feat, what HA
uses), 'label' = pstdev over all training labels.
CV: contiguous time blocks (default 6) or leave-one-visit-out (a visit = run of consecutive labels of one zone); labels
within --purge-s seconds of the held-out block are dropped from training (the 15 s label windows overlap); held-out labels
whose zone is absent from the training set are not scored.
"""
import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

NODES = ["pcdesk", "cyd", "workdesk", "kitchen", "nlpi"]
CSI_NODES = ["pcdesk", "cyd", "workdesk", "kitchen"]
ZONES = ["kitchen", "work_desk", "pc_desk", "bed"]
FLOOR_BLE = -100.0
FLOOR = {"raw": 3.0, "ratio": 0.1, "logratio": 0.1}
WEIGHTS = [0, 1, 2, 3, 4, 6]


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class Row:
    def __init__(self, ts, zone, ble, motion, still, norm):
        self.ts, self.zone, self.ble = ts, zone, ble
        self.feat = featurize(motion, still, norm)


def featurize(motion, still, norm):
    """motion / still: {node: value or None} -> {feature name: value}."""
    f = {}
    for typ, d in (("motion", motion), ("still", still)):
        vals = {n: v for n, v in d.items() if v is not None}
        med = statistics.median(vals.values()) if vals else None
        for n, v in vals.items():
            if norm == "raw":
                f["csi_%s_%s" % (n, typ)] = v
            elif med and med > 0:
                r = max(v, 0.01) / med
                f["csi_%s_%s" % (n, typ)] = r if norm == "ratio" else math.log(r)
    return f


def load(path, norm):
    by = defaultdict(lambda: {"ble": {}, "motion": {}, "still": {}})
    meta = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            i = int(r["label_id"])
            meta[i] = (datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").timestamp(), r["zone"])
            n = r["node"]
            b = num(r["phone_avg"])
            by[i]["ble"][n] = FLOOR_BLE if b is None else max(b, FLOOR_BLE)
            by[i]["motion"][n] = num(r["csi_motion"]) if n in CSI_NODES else None
            by[i]["still"][n] = num(r["csi_still"]) if n in CSI_NODES else None
    rows = []
    for i in sorted(by):
        d = by[i]
        ble = {n: d["ble"].get(n, FLOOR_BLE) for n in NODES}
        rows.append(Row(meta[i][0], meta[i][1], ble, d["motion"], d["still"], norm))
    return rows


def proto_of(rows):
    ble = {n: statistics.median(r.ble[n] for r in rows) for n in NODES}
    names = {n for r in rows for n in r.feat}
    feat = {n: statistics.median(r.feat[n] for r in rows if n in r.feat) for n in names}
    return ble, feat


def make_scale(train, mode, norm):
    names = sorted({n for r in train for n in r.feat})
    scale = {}
    for n in names:
        if mode == "zone":
            vals = []
            for z in {r.zone for r in train}:
                v = [r.feat[n] for r in train if r.zone == z and n in r.feat]
                if v:
                    vals.append(statistics.median(v))
        else:
            vals = [r.feat[n] for r in train if n in r.feat]
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        scale[n] = max(sd, FLOOR[norm])
    return scale


def dist(ble, feat, proto, w, scale):
    pb, pf = proto
    d = sum((ble[n] - pb.get(n, FLOOR_BLE)) ** 2 for n in NODES)
    k = 0
    if w > 0:
        for n, x in feat.items():
            mu, sd = pf.get(n), scale.get(n, 0.0)
            if mu is not None and sd > 0:
                d += (w * (x - mu) / sd) ** 2
                k += 1
    return math.sqrt(d / (len(NODES) + k))


def kmedians(members, k, w, scale):
    """Deterministic k-medians in the matcher's own metric; farthest-first init."""
    k = max(1, min(k, len(members) // 4))
    if k == 1:
        return [proto_of(members)]
    cents = [proto_of(members)]
    first = min(members, key=lambda r: dist(r.ble, r.feat, cents[0], w, scale))
    cents = [(dict(first.ble), dict(first.feat))]
    while len(cents) < k:
        far = max(members, key=lambda r: min(dist(r.ble, r.feat, c, w, scale) for c in cents))
        cents.append((dict(far.ble), dict(far.feat)))
    for _ in range(12):
        groups = [[] for _ in cents]
        for r in members:
            j = min(range(len(cents)), key=lambda j: dist(r.ble, r.feat, cents[j], w, scale))
            groups[j].append(r)
        new = [proto_of(g) if g else c for g, c in zip(groups, cents)]
        if new == cents:
            break
        cents = new
    return cents


def fit(method, train, w, scale_mode, norm):
    """Return predict(row) -> zone."""
    scale = make_scale(train, scale_mode, norm)
    zones = sorted({r.zone for r in train})
    byz = {z: [r for r in train if r.zone == z] for z in zones}
    if method.startswith("knn"):
        kk = int(method[3:])
        pool = [(r.zone, (r.ble, r.feat)) for r in train]

        def predict(row):
            ds = sorted((dist(row.ble, row.feat, p, w, scale), z) for z, p in pool)[:kk]
            votes = Counter(z for _, z in ds)
            top = max(votes.values())
            tied = [z for z, c in votes.items() if c == top]
            return min(tied, key=lambda z: sum(d for d, zz in ds if zz == z) / votes[z])
        return predict
    protos = {}
    for z in zones:
        if method == "median":
            protos[z] = [proto_of(byz[z])]
        elif method.startswith("km"):
            protos[z] = kmedians(byz[z], int(method[2:]), w, scale)
        elif method.startswith("kit"):
            protos[z] = kmedians(byz[z], int(method[3:]), w, scale) if z == "kitchen" else [proto_of(byz[z])]
        else:
            raise ValueError(method)

    def predict(row):
        return min(zones, key=lambda z: min(dist(row.ble, row.feat, p, w, scale) for p in protos[z]))
    return predict


def make_folds(rows, cv, nblocks=6):
    if cv == "blocks":
        size = len(rows) / nblocks
        return [rows[int(round(i * size)):int(round((i + 1) * size))] for i in range(nblocks)]
    out, cur = [], [rows[0]]
    for r in rows[1:]:
        if r.zone == cur[-1].zone:
            cur.append(r)
        else:
            out.append(cur)
            cur = [r]
    return out + [cur]


def cross_validate(rows, method, w, scale_mode, norm, cv, purge):
    conf = Counter()
    for test in make_folds(rows, cv):
        lo, hi = min(r.ts for r in test) - purge, max(r.ts for r in test) + purge
        ids = {id(r) for r in test}
        train = [r for r in rows if id(r) not in ids and (purge == 0 or not lo <= r.ts <= hi)]
        if not train:
            continue
        have = {r.zone for r in train}
        predict = fit(method, train, w, scale_mode, norm)
        for r in test:
            if r.zone in have:
                conf[(r.zone, predict(r))] += 1
    return conf


def acc(conf):
    n = sum(conf.values())
    return sum(c for (t, p), c in conf.items() if t == p) / n if n else float("nan")


def recall(conf, z):
    n = sum(c for (t, p), c in conf.items() if t == z)
    return (sum(c for (t, p), c in conf.items() if t == z and p == z), n)


def bal(conf):
    rs = [a / n for a, n in (recall(conf, z) for z in ZONES) if n]
    return sum(rs) / len(rs) if rs else float("nan")


def cmd_cv(a):
    methods = ["median", "km2", "km3", "kit2", "kit3", "knn1", "knn3", "knn5"]
    norms = ["raw", "ratio", "logratio"]
    data = {nm: load(a.csv, nm) for nm in norms}
    print("labels: %d, zones: %s" % (len(data["raw"]), dict(Counter(r.zone for r in data["raw"]))))
    print("cv=%s purge=%ds scale=%s; cell = accuracy (balanced accuracy)\n" % (a.cv, a.purge_s, a.scale))
    results = {}
    for nm in norms:
        print("norm=%s" % nm)
        print("%-8s" % "method" + "".join("%14s" % ("w=%d" % w) for w in WEIGHTS))
        for m in methods:
            line = "%-8s" % m
            for w in WEIGHTS:
                c = cross_validate(data[nm], m, w, a.scale, nm, a.cv, a.purge_s)
                results[(nm, m, w)] = c
                line += "%14s" % ("%.2f (%.2f)" % (acc(c), bal(c)))
            print(line)
        print()
    print("per-zone recall (correct/n), w=%s" % a.recall_w)
    print("%-22s" % "norm/method" + "".join("%14s" % z for z in ZONES))
    for nm in norms:
        for m in methods:
            c = results[(nm, m, a.recall_w)]
            print("%-22s" % ("%s/%s" % (nm, m)) + "".join("%14s" % ("%d/%d" % recall(c, z)) for z in ZONES))
    print("\nfeature ablation, median, norm=raw (accuracy at w):")
    raw = data["raw"]
    for label, keep in (("all", None), ("motion only", "motion"), ("still only", "still")):
        line = "%-12s" % label
        for w in WEIGHTS:
            sub = raw
            if keep:
                sub = []
                for r in raw:
                    r2 = Row(r.ts, r.zone, r.ble, {}, {}, "raw")
                    r2.feat = {n: v for n, v in r.feat.items() if n.endswith("_" + keep)}
                    sub.append(r2)
            line += "%14s" % ("%.2f" % acc(cross_validate(sub, "median", w, a.scale, "raw", a.cv, a.purge_s)))
        print(line)


def load_weak(paths, norm):
    """Rows of zone-v2 CSVs with a weak_label; returns (rows, day_of_row list)."""
    rows, days = [], []
    for path in paths:
        with open(path, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if not r.get("weak_label"):
                    continue
                ts = datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S")
                ble = {n: FLOOR_BLE if num(r.get(n + "_phone")) is None else max(num(r[n + "_phone"]), FLOOR_BLE) for n in NODES}
                motion = {n: num(r.get("csi_%s_motion" % n)) for n in CSI_NODES}
                still = {n: num(r.get("csi_%s_still" % n)) for n in CSI_NODES}
                rows.append(Row(ts.timestamp(), r["weak_label"], ble, motion, still, norm))
                days.append(ts.strftime("%Y-%m-%d"))
    order = sorted(range(len(rows)), key=lambda i: rows[i].ts)
    return [rows[i] for i in order], [days[i] for i in order]


def cap_rows(rows, cap):
    out = []
    for z in sorted({r.zone for r in rows}):
        zr = [r for r in rows if r.zone == z]
        step = max(1, len(zr) // cap) if cap else 1
        out += zr[::step][:cap] if cap else zr
    return out


def score(predict, test):
    conf = Counter((r.zone, predict(r)) for r in test)
    return conf


def cmd_weak(a):
    manual = load(a.csv, a.norm)
    weak, days = load_weak(a.weak, a.norm)
    wz = Counter(r.zone for r in weak)
    print("manual labels: %d %s" % (len(manual), dict(Counter(r.zone for r in manual))))
    print("weak rows: %d %s over days %s" % (len(weak), dict(wz), sorted(set(days))))
    if len(wz) < 2:
        print("weak rows cover fewer than 2 zones: nothing to evaluate yet (collect more; see weak_label.py rules)")
        return
    wcap = cap_rows(weak, a.cap)
    methods = ["median", "kit3", "knn5"]

    def table(title, runs):
        print(chr(10) + title + "; cell = accuracy (balanced accuracy)")
        print("%-8s" % "method" + "".join("%14s" % ("w=%d" % w) for w in WEIGHTS))
        confs = {}
        for m in methods:
            line = "%-8s" % m
            for w in WEIGHTS:
                c = runs(m, w)
                confs[(m, w)] = c
                line += "%14s" % (("%.2f (%.2f)" % (acc(c), bal(c))) if c else "n/a")
            print(line)
        for m in methods:
            c = confs[(m, a.recall_w)]
            if c:
                print("  recall w=%d %-7s" % (a.recall_w, m) + "  ".join("%s %d/%d" % ((z,) + recall(c, z)) for z in ZONES))

    def weak_to_manual(m, w):
        have = {r.zone for r in wcap}
        test = [r for r in manual if r.zone in have]
        return score(fit(m, wcap, w, a.scale, a.norm), test) if test else Counter()

    def manual_to_weak(m, w):
        have = {r.zone for r in manual}
        test = [r for r in cap_rows(weak, 0) if r.zone in have]
        test = test[::max(1, len(test) // 2000)]
        return score(fit(m, manual, w, a.scale, a.norm), test) if test else Counter()

    uniq_days = sorted(set(days))
    if len(uniq_days) >= 2:
        folds = [[r for r, d in zip(weak, days) if d == day] for day in uniq_days]
        purge, how = 0, "day-blocked (%d days)" % len(uniq_days)
    else:
        n = len(weak)
        folds = [weak[i * n // 4:(i + 1) * n // 4] for i in range(4)]
        purge, how = 60, "WARNING only 1 day: 4 contiguous time blocks, 60 s purge"

    def weak_cv(m, w):
        conf = Counter()
        for test in folds:
            if not test:
                continue
            lo, hi = min(r.ts for r in test) - purge, max(r.ts for r in test) + purge
            ids = {id(r) for r in test}
            train = cap_rows([r for r in weak if id(r) not in ids and (purge == 0 or not lo <= r.ts <= hi)], a.cap)
            have = {r.zone for r in train}
            if len(have) < 2:
                continue
            p = fit(m, train, w, a.scale, a.norm)
            for r in test:
                if r.zone in have:
                    conf[(r.zone, p(r))] += 1
        return conf

    table("train weak -> test manual (%d rows after cap)" % len(wcap), weak_to_manual)
    table("train manual -> test weak", manual_to_weak)
    table("CV on weak rows, " + how, weak_cv)


def parse_snaps(path):
    snaps, cur = [], None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("###"):
            cur = {"t": line[3:].strip()}
            snaps.append(cur)
        elif cur is not None and " " in line and not line.startswith("DONE"):
            k, v = line.split(None, 1)
            cur[k] = v
    return snaps


def live_row(snap, norm):
    ble = {n: max(num(snap.get("sensor.rp_%s_signal" % n)) or -127.0, FLOOR_BLE) for n in NODES}
    motion = {n: num(snap.get("sensor.rp_link_%s_motion" % n)) for n in CSI_NODES}
    still = {n: num(snap.get("sensor.rp_%s_csi_still" % n)) for n in CSI_NODES}
    return Row(0, "?", ble, motion, still, norm)


def cmd_live(a):
    fps = json.load(open(a.fingerprints, encoding="utf-8"))
    snaps = parse_snaps(a.snaps)
    snaps = [s for s in snaps if "sensor.rp_pcdesk_signal" in s]
    print("snapshots: %d (%s .. %s)" % (len(snaps), snaps[0]["t"], snaps[-1]["t"]))
    print("context per snapshot: yui_room_zone / rp_fp_zone(HA, w=%s) / pc_idle" % snaps[0].get("input_number.rp_feat_weight_db"))
    for s in snaps:
        print("  %s %s / %s / idle %s" % (s["t"], s.get("sensor.yui_room_zone"), s.get("sensor.rp_fp_zone"), s.get("sensor.rp_pc_idle")))
    # deployed matcher: fingerprints.json medians, scale = pstdev over zone medians (calibrate.py build_feat)
    zf = {z: v["feat"] for z, v in fps.items() if v.get("feat")}
    names = sorted({n for f in zf.values() for n in f})
    scale = {n: max(statistics.pstdev([f[n] for f in zf.values() if n in f]), FLOOR["raw"]) for n in names}
    protos = {z: ({n: v["nodes"].get(n, FLOOR_BLE) for n in NODES}, v.get("feat", {})) for z, v in fps.items()}
    print("\nscale used by HA:", {k: round(v, 1) for k, v in scale.items()})
    print("\nlive features (mean over snapshots) vs fingerprint medians:")
    rr = [live_row(s, "raw") for s in snaps]
    for n in names:
        xs = [r.feat[n] for r in rr if n in r.feat]
        print("  %-20s live %6.1f  " % (n, statistics.mean(xs)) + "  ".join("%s %.1f" % (z[:4], protos[z][1].get(n, float("nan"))) for z in sorted(protos)))
    print("\nlive BLE (mean): " + "  ".join("%s %.0f" % (n, statistics.mean(r.ble[n] for r in rr)) for n in NODES))
    print("fingerprint BLE: " + "  ".join("%s:%s" % (z[:4], [int(protos[z][0][n]) for n in NODES]) for z in sorted(protos)))
    print("\nwinner per snapshot (zone initials k=kitchen w=work_desk p=pc_desk b=bed), tally over %d snapshots" % len(snaps))
    abbr = {"kitchen": "k", "work_desk": "w", "pc_desk": "p", "bed": "b"}
    print("deployed matcher (median from fingerprints.json), raw features:")
    for w in WEIGHTS:
        win = []
        for r in rr:
            win.append(min(protos, key=lambda z: dist(r.ble, r.feat, protos[z], w, scale)))
        print("  w=%d %s  %s" % (w, "".join(abbr[z] for z in win), dict(Counter(win))))
    # label-trained variants, trained on all labels
    for norm in ("raw", "ratio", "logratio"):
        rows = load(a.labels, norm)
        lv = [live_row(s, norm) for s in snaps]
        for m in ("median", "km3", "kit3", "knn5"):
            print("trained on all labels, norm=%s method=%s:" % (norm, m))
            for w in WEIGHTS:
                win = [fit(m, rows, w, "zone", norm)(r) for r in lv]
                print("  w=%d %s  %s" % (w, "".join(abbr[z] for z in win), dict(Counter(win))))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cv")
    c.add_argument("csv")
    c.add_argument("--scale", choices=["zone", "label"], default="zone")
    c.add_argument("--purge-s", type=int, default=0)
    c.add_argument("--cv", choices=["blocks", "visits"], default="blocks")
    c.add_argument("--recall-w", type=int, default=3)
    c.add_argument("--ble-nodes", default=",".join(NODES), help="comma list of BLE nodes used in the distance")
    wk = sub.add_parser("weak")
    wk.add_argument("csv", help="manual label export")
    wk.add_argument("weak", nargs="+", help="zone-v2-*.csv files")
    wk.add_argument("--cap", type=int, default=300, help="max rows per zone used for training (evenly spaced)")
    wk.add_argument("--norm", choices=["raw", "ratio", "logratio"], default="raw")
    wk.add_argument("--scale", choices=["zone", "label"], default="zone")
    wk.add_argument("--recall-w", type=int, default=1)
    lv = sub.add_parser("live")
    lv.add_argument("fingerprints")
    lv.add_argument("snaps")
    lv.add_argument("labels")
    a = ap.parse_args()
    if a.cmd == "cv":
        NODES[:] = [n for n in NODES if n in a.ble_nodes.split(",")]
        cmd_cv(a)
    elif a.cmd == "weak":
        cmd_weak(a)
    else:
        cmd_live(a)


if __name__ == "__main__":
    sys.exit(main())
