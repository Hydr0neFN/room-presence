#!/usr/bin/env python3
"""Causal replay of the presence tracker on the zone log (stdlib only, offline).

  tracker_replay.py --logdir /var/lib/zone-log
  tracker_replay.py --logdir DIR --first 2026-11-05 --seeds /var/lib/presence-track/seeds.json
  tracker_replay.py --logdir DIR --truth truth.json --timeline 2026-11-05T18:00

For every day D from --first (default: the 8th zone-log file) a model is fitted on the TRAIN_DAYS files before D (not D
itself) plus the seed rows, and a fresh Tracker runs over all rows of D. The summary line compares the predictions with the
rule weak labels (zones with >= 20 rows), and reports zone transitions and walking bursts per hour.
--truth FILE: JSON list of [start_ts, end_ts, zone], local ISO, end exclusive; scored the same way.
--timeline TS: print every zone change at or after TS as "ts zone confidence".
"""
import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracker as tr  # noqa: E402


def day_of(path):
    name = os.path.basename(path)
    return name[len("zone-v3-"):-len(".csv")]


def run_day(rows, model):
    """Fresh Tracker over the rows of one day -> [(row, result), ...]."""
    tracker = tr.Tracker(model)
    return [(row, tracker.step(row)) for row in rows]


def metrics(pairs, field):
    """Confusion over rows whose truth (res['weak'] or a truth span) is in ZONES, restricted to zones with >= min rows."""
    conf = collections.Counter()
    for row, res in pairs:
        truth = res["weak"] if field == "weak" else res.get(field)
        if truth in tr.ZONES:
            conf[(truth, res["zone"])] += 1
    total = {z: sum(v for (a, b), v in conf.items() if a == z) for z in tr.ZONES}
    rec = {z: conf.get((z, z), 0) / total[z] for z in tr.ZONES if total[z] >= (20 if field == "weak" else 1)}
    bal = sum(rec.values()) / len(rec) if rec else 0.0
    return sum(total.values()), bal, rec


def transitions_per_hour(pairs):
    zones = [res["zone"] for _, res in pairs if not res["away"]]
    changes = sum(1 for a, b in zip(zones, zones[1:]) if a != b)
    return changes / len(zones) * 360 if zones else 0.0


def bursts_per_hour(pairs):
    return sum(1 for _, res in pairs if res["burst"]) / len(pairs) * 360 if pairs else 0.0


def truth_spans(path):
    """JSON [[start, end, zone], ...] -> [(epoch start, epoch end, zone)]; end exclusive."""
    import json
    with open(path, encoding="utf-8") as f:
        truth = json.load(f)
    out = []
    for start, end, zone in truth:
        a, b = tr.parse_ts(start), tr.parse_ts(end)
        if a is not None and b is not None and zone in tr.ZONES:
            out.append((time.mktime(a.timetuple()), time.mktime(b.timetuple()), zone))
    return out


def apply_truth(pairs, spans):
    out = []
    for row, res in pairs:
        t = row.get("_t")
        for a, b, zone in spans:
            if a <= t < b:
                out.append((row, dict(res, truth=zone)))
                break
    return out


def rec_text(rec):
    return " ".join("%s=%.2f" % (z, rec[z]) for z in sorted(rec))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logdir", default="/var/lib/zone-log")
    ap.add_argument("--first", default="")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--truth", default="")
    ap.add_argument("--timeline", default="")
    a = ap.parse_args()

    files = tr.list_days(a.logdir)
    if len(files) < 2:
        sys.exit("need at least two zone-v3 files in " + a.logdir)
    start = 7 if len(files) > 7 else len(files) - 1
    seeds = tr.merge_seeds(tr.load_seeds(a.seeds)) if a.seeds else {}
    pairs, prev = [], None
    model_info = {}
    for i, path in enumerate(files):
        day = day_of(path)
        if a.first:
            if day < a.first:
                continue
        elif i < start:
            continue
        model, info = tr.train(a.logdir, files[max(0, i - tr.TRAIN_DAYS):i], seeds)
        model_info = info
        rows = tr.load_rows(path)
        for row, res in run_day(rows, model):
            pairs.append((row, res))
            if a.timeline and row["ts"] >= a.timeline and res["zone"] != prev:
                print("%s %s %s" % (row["ts"], res["zone"], res["confidence"]))
            prev = res["zone"]
    if not pairs:
        sys.exit("no rows replayed")

    n, bal, rec = metrics(pairs, "weak")
    print("track-v1  weak rows=%d  bal=%.3f  recall %s  transitions/h=%.1f  bursts/h=%.1f  zones=%d" % (
        n, bal, rec_text(rec), transitions_per_hour(pairs), bursts_per_hour(pairs), len(model_info)))
    if a.truth:
        tp = apply_truth(pairs, truth_spans(a.truth))
        n, bal, rec = metrics(tp, "truth")
        print("track-v1  truth rows=%d  bal=%.3f  recall %s" % (n, bal, rec_text(rec)))


if __name__ == "__main__":
    main()
