"""Score the shadow CSI zone against the BLE fingerprint and the live zone, from the zone log (csi_zone / csi_conf columns).

  python3 csi_score.py [--logdir /var/lib/zone-log] [--days 2026-10-30,2026-10-31] [--min-conf 0.0]

Per day and overall: CSI vs rule weak labels (csi_features.weak_label, NOT ground truth: pc_desk / kitchen / work_desk / bed
evidence from PC, radar, lights, clock), the same windows scored for BLE fp_zone and the live zone (the live zone shares evidence with
the rules, so it is optimistic), and plain agreement csi_zone == zone / fp_zone. Rows without a csi_zone (service down, or files
written before the column existed) are skipped. Stdlib only.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import csi_features as cf  # noqa: E402
import csi_replay as rp  # noqa: E402


def score(rows, min_conf=0.0):
    """-> {day or 'ALL': {name: Counter((true, pred))}} plus agreement counters."""
    out = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for r in rows:
        cz = r.get("csi_zone") or ""
        if cz not in cf.ZONES:
            continue
        conf = rp.num(r.get("csi_conf")) or 0.0
        if conf < min_conf:
            continue
        weak = cf.weak_from_row(r, r["_hour"])
        for key in (r["_day"], "ALL"):
            o = out[key]
            o["n_scored"][("rows", "rows")] += 1
            for other, col in (("live", "zone"), ("fp", "fp_zone")):
                v = r.get(col)
                if v in cf.ZONES:
                    o["agree_" + other][(v, cz)] += 1
            if weak in cf.ZONES:
                o["csi_vs_weak"][(weak, cz)] += 1
                for other, col in (("ble_fp", "fp_zone"), ("live", "zone")):
                    v = r.get(col)
                    if v in cf.ZONES:
                        o[other + "_vs_weak_same_rows"][(weak, v)] += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--logdir", default=os.environ.get("ZONELOG_DIR", "/var/lib/zone-log"))
    ap.add_argument("--days", default="")
    ap.add_argument("--min-conf", type=float, default=0.0)
    a = ap.parse_args(argv)
    rows = rp.load_rows(a.logdir, days=[d for d in a.days.split(",") if d] or None)
    sc = score(rows, a.min_conf)
    if not sc:
        print("no rows with a csi_zone yet in", a.logdir)
        return 1
    for key in sorted(sc, key=lambda k: (k == "ALL", k)):
        o = sc[key]
        print("== %s: %d scored windows" % (key, sum(o["n_scored"].values())))
        for name in ("csi_vs_weak", "ble_fp_vs_weak_same_rows", "live_vs_weak_same_rows", "agree_live", "agree_fp"):
            if o.get(name):
                label = {"agree_live": "csi == live zone (true=live)", "agree_fp": "csi == BLE fp (true=fp)"}.get(name, name)
                print(rp.fmt(label, o[name]))
    print("weak labels are rule-based (no CSI), not ground truth; 'live' shares evidence with them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
