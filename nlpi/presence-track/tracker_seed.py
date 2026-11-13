#!/usr/bin/env python3
"""Seed-row CLI for the presence tracker: copy the features of zone-log rows into the seed file.

  tracker_seed.py --logdir /var/lib/zone-log --seeds /var/lib/presence-track/seeds.json \
                  --src manual --zone bathroom --from 2026-11-12T06:00 --to 2026-11-12T06:10 [--replace]
  tracker_seed.py --seeds /var/lib/presence-track/seeds.json --list

The seed file is {"cal": {zone: [feature dicts]}, "manual": {zone: [feature dicts]}}; the model uses both sources together.
--replace clears the destination list before adding. Stdlib only.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracker as tr  # noqa: E402


def in_range(ts, t_from, t_to):
    """from <= ts < to (either bound may be empty)."""
    dt = tr.parse_ts(ts)
    if dt is None:
        return False
    lo = tr.parse_ts(t_from) if t_from else None
    hi = tr.parse_ts(t_to) if t_to else None
    return (lo is None or dt >= lo) and (hi is None or dt < hi)


def add(logdir, path, src, zone, t_from, t_to, replace=False):
    """Add the features of the matching rows to seeds[src][zone]; -> number of rows added."""
    rows = []
    for f in tr.list_days(logdir):
        for row in tr.load_rows(f):
            if in_range(row["ts"], t_from, t_to):
                rows.append(tr.features(row))
    seeds = tr.load_seeds(path)
    cur = [] if replace else list(seeds.get(src, {}).get(zone, []))
    seeds.setdefault(src, {})[zone] = cur + rows
    tr.save_seeds(path, seeds)
    return len(rows)


def counts(path):
    """-> {(src, zone): number of seed rows}."""
    seeds = tr.load_seeds(path)
    return {(src, zone): len(rows) for src, by in seeds.items() for zone, rows in by.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logdir", default="/var/lib/zone-log")
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--src", choices=tr.SEED_SOURCES)
    ap.add_argument("--zone", choices=tr.ZONES)
    ap.add_argument("--from", dest="t_from", default="")
    ap.add_argument("--to", dest="t_to", default="")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for (src, zone), n in sorted(counts(a.seeds).items()):
            print("%s %s %d" % (src, zone, n))
        return
    if not (a.src and a.zone and a.t_from and a.t_to):
        ap.error("--src, --zone, --from and --to are required unless --list")
    print(add(a.logdir, a.seeds, a.src, a.zone, a.t_from, a.t_to, a.replace))


if __name__ == "__main__":
    main()
