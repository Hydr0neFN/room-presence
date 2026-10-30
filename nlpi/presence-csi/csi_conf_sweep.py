#!/usr/bin/env python3
"""Pick the CSI-lead confidence threshold: sweep min confidence on the leave-one-day-out replay of csi_replay.

  python3 csi_conf_sweep.py --logdir /var/lib/zone-log [--days a,b,c]

For each threshold t: coverage (share of weak-labelled windows with conf >= t), accuracy / balanced accuracy of the CSI zone
on the covered windows (vs rule weak labels, NOT ground truth), BLE fp_zone accuracy on the SAME windows, and zone changes
per hour of the 'CSI lead' sequence (CSI zone if conf >= t, else BLE fp zone) vs the BLE fp and live zone sequences.
Stdlib only.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import csi_features as cf  # noqa: E402
import csi_replay as rp  # noqa: E402


def predictions(res):
    """leave-one-day-out -> {id(x): (pred, conf)} for EVERY non-warm window of the held-out day (not only labelled ones)."""
    days = sorted({x["row"]["_day"] for x in res})
    out = {}
    for d in days:
        train = [(x["label"], x["zs"]) for x in res if x["row"]["_day"] != d and x["label"] in cf.ZONES and len(x["zs"]) >= 3
                 and not x.get("warm")]
        if len({z for z, _ in train}) < 2:
            continue
        m = rp.new_model().fit(train)
        for x in res:
            if x["row"]["_day"] == d and not x.get("warm") and len(x["zs"]) >= 3:
                dist, _ = m.dist(x["zs"])
                out[id(x)] = cf.decide(dist)
    return out


def changes_per_hour(seq):
    seq = [s for s in seq if s]
    if len(seq) < 2:
        return 0.0
    ch = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    return ch / (len(seq) * 10.0 / 3600.0)   # zone-log rows are 10 s apart


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logdir", default="/var/lib/zone-log")
    ap.add_argument("--days", default="")
    ap.add_argument("--thresholds", default="0,0.05,0.1,0.15,0.2,0.25,0.3,0.4,0.5")
    a = ap.parse_args()
    rows = rp.load_rows(a.logdir, days=[d for d in a.days.split(",") if d] or None)
    _, res = rp.run(rows)
    pred = predictions(res)
    print("windows: %d total, %d with a held-out CSI prediction, %d labelled" % (
        len(res), len(pred), sum(1 for x in res if id(x) in pred and x["label"] in cf.ZONES)))
    cs = [c for _, c in pred.values()]
    cs.sort()
    if cs:
        print("conf quantiles over all predicted windows: " + " ".join("p%d=%.3f" % (q, cs[min(len(cs) - 1, int(len(cs) * q / 100))])
                                                                       for q in (10, 25, 50, 75, 90)))
    print("%-5s %-9s %-24s %-24s %s" % ("thr", "coverage", "CSI acc/bal (covered)", "BLE-fp acc/bal (same)", "changes/h: lead | fp | live"))
    fp_seq = [x["row"].get("fp_zone") if x["row"].get("fp_zone") in cf.ZONES else None for x in res if id(x) in pred]
    live_seq = [x["row"].get("zone") for x in res if id(x) in pred]
    for t in [float(s) for s in a.thresholds.split(",")]:
        cc, bc, tot, cov = collections.Counter(), collections.Counter(), 0, 0
        lead = []
        for x in res:
            p = pred.get(id(x))
            if not p:
                continue
            zone, conf = p
            fp = x["row"].get("fp_zone") if x["row"].get("fp_zone") in cf.ZONES else None
            lead.append(zone if conf >= t else fp)
            if x["label"] in cf.ZONES:
                tot += 1
                if conf >= t:
                    cov += 1
                    cc[(x["label"], zone)] += 1
                    if fp:
                        bc[(x["label"], fp)] += 1
        n, acc, bal, _, _ = rp.metrics(cc) if cc else (0, 0, 0, 0, 0)
        n2, acc2, bal2, _, _ = rp.metrics(bc) if bc else (0, 0, 0, 0, 0)
        print("%-5.2f %5.1f%%    %.3f / %.3f (n=%-5d)   %.3f / %.3f (n=%-5d)   %.0f | %.0f | %.0f" % (
            t, 100.0 * cov / max(tot, 1), acc, bal, n, acc2, bal2, n2,
            changes_per_hour(lead), changes_per_hour(fp_seq), changes_per_hour(live_seq)))
    print("labels are rule-based weak labels (no CSI), not ground truth.")


if __name__ == "__main__":
    main()
