"""Before/after simulation of the 2026-10-28 HA fixes on the logged zone-v3 CSVs (10 s rows).
  py -3.13 -I analysis/a10_sim_fix.py
Mirrors ha/room_presence.yaml: node health (stale > 5 min), no kitchen default, night bed rule, anti-flap, node weights.
BLE only (rp_feat_weight_db = 0), fingerprints.json = what ran live."""
import sys, os, json, statistics as st
from collections import Counter, deque
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
from common import *
import weak_labels

fp = json.load(open(D + '/fingerprints.json'))
ZN = ['pc_desk', 'work_desk', 'kitchen', 'bed']
files = sorted(glob.glob(D + '/zone-v3-*.csv'))
wl = weak_labels.label_csv(files)
rows = [r for r, _, _ in wl]
lab = [l for _, l, _ in wl]

OLD = dict(use_stale=False, nodes=NODES, w={}, kitchen_evidence=False, bed=False, pc_hold=60, radar_hold=0, dwell=0)
NEW = dict(use_stale=True, nodes=['pcdesk', 'cyd', 'workdesk', 'kitchen'],
           w={'pcdesk': 1.0, 'cyd': 1.0, 'workdesk': 0.5, 'kitchen': 0.5}, kitchen_evidence=True, bed=True,
           pc_hold=300, radar_hold=30, dwell=45)


def run(cfg, verbose=False):
    N = len(rows)
    stale_cnt = {n: 0 for n in NODES}
    hist = {n: deque(maxlen=60) for n in NODES}
    fpz = None
    zone = None
    zone_since = 0
    last_radar = -999
    zs, fps = [], []
    for i, r in enumerate(rows):
        ph = {n: num(r[n + '_phone']) for n in NODES}
        for n in NODES:
            hist[n].append(ph[n])
        heard_any = any(v is not None for v in ph.values())
        stale = {}
        for n in NODES:
            others = any(ph[m] is not None for m in NODES if m != n)
            stale_cnt[n] = stale_cnt[n] + 1 if (ph[n] is None and others) else 0
            stale[n] = cfg['use_stale'] and stale_cnt[n] >= 30
        # fingerprint distance
        ds = {}
        for z in ZN:
            d = k = 0
            for n in cfg['nodes']:
                if stale[n]:
                    continue
                w = cfg['w'].get(n, 1.0)
                sg = max(ph[n] if ph[n] is not None else -127, -100)
                d += w * (sg - fp[z]['nodes'][n]) ** 2
                k += w
            ds[z] = (d / k) ** .5 if k >= (1.5 if cfg['use_stale'] else 0.1) else None
        if r['home'] != 'on' or any(v is None for v in ds.values()):
            best = None
        else:
            b = min(ds, key=ds.get)
            best = fpz if (fpz in ds and ds[fpz] - ds[b] < 2.5) else b
        fpz = best if best else fpz
        fps.append(best)
        # evidence
        idle = num(r['pc_idle'])
        online, locked = r['pc_online'] == 'on', r['pc_locked']
        pc_active = online and locked == 'off' and idle is not None and idle < 60
        radar = r['radar'] == 'on' and (num(r['radar_cm']) or 9999) < 250
        if radar:
            last_radar = i
        radar_h = radar or (i - last_radar) * 10 <= cfg['radar_hold']
        pmax = num(r['power_max120']) or 0
        klag = num(r['kitchen_light_age_s'])
        k_ev = radar_h or pmax >= 1000 or (klag is not None and klag <= 900)
        m = int(r['ts'][11:13]) * 60 + int(r['ts'][14:16])
        sds = []
        for n in ('cyd', 'kitchen'):
            v = [x for x in hist[n] if x is not None]
            if len(hist[n]) >= 60 and len(v) >= 40:
                sds.append(st.pstdev(v))
        pc_idle_long = (not online) or locked == 'on' or idle is None or idle > 1800
        night_bed = (30 <= m < 450 and pc_idle_long and len(sds) == 2 and max(sds) <= 3.5 and not radar_h)
        prev = zone or 'unknown'
        soft = False
        if r['home'] == 'off':
            z = 'away'
        elif pc_active:
            z = 'pc_desk'
        elif cfg['pc_hold'] > 60 and prev == 'pc_desk' and online and locked == 'off' and idle is not None and idle < cfg['pc_hold'] and not radar_h:
            z = 'pc_desk'
        elif radar_h:
            z = 'kitchen'
        elif not heard_any:
            z = 'home'
        elif cfg['bed'] and night_bed:
            z = 'bed'
        elif best is None:
            z = 'home'; soft = True
        elif best == 'kitchen' and cfg['kitchen_evidence'] and not k_ev:
            z = 'home'; soft = True
        else:
            z = best; soft = True
        if cfg['dwell'] and soft and zone not in (None, 'away') and z != zone and (i - zone_since) * 10 < cfg['dwell']:
            z = zone
        if z != zone:
            zone, zone_since = z, i
        zs.append(z)
    return zs, fps


def seg_stats(z):
    ch = [i for i, (a, b) in enumerate(zip(z, z[1:])) if a != b]
    segs = []; s = 0
    for i in ch:
        segs.append(i + 1 - s); s = i + 1
    segs.append(len(z) - s)
    return len(ch), sum(1 for x in segs if x < 6), st.median(segs) * 10


def report(name, zs, fps):
    idx = [i for i, r in enumerate(rows) if r['home'] == 'on']
    hz = [zs[i] for i in idx]
    c = Counter(hz); n = len(hz)
    tr, sh, med = seg_stats(zs)
    # fp-level accuracy vs weak labels (independent of PC rule): rows with label != pc_desk circular? report all + per-class recall
    lab_idx = [i for i in idx if lab[i] in ('pc_desk', 'kitchen', 'work_desk', 'bed')]
    acc = sum(1 for i in lab_idx if zs[i] == lab[i]) / max(len(lab_idx), 1)
    facc = sum(1 for i in lab_idx if fps[i] == lab[i]) / max(len(lab_idx), 1)
    rec = {}
    for L in ('pc_desk', 'kitchen', 'work_desk'):
        li = [i for i in idx if lab[i] == L]
        rec[L] = (sum(1 for i in li if fps[i] == L), sum(1 for i in li if zs[i] == L), len(li))
    print(f"{name:7s} transitions {tr:3d}  segs<60s {sh:3d}  median seg {med:5.0f}s | home-rows {n} shares " +
          ", ".join(f"{k} {100*v/n:.1f}%" for k, v in c.most_common()))
    print(f"        vs weak labels ({len(lab_idx)} rows): zone acc {100*acc:.1f}%  fp acc {100*facc:.1f}% | recall fp/zone: " +
          ", ".join(f"{L} {a}/{b}/{t}" for L, (a, b, t) in rec.items()))
    kit = [i for i in idx if zs[i] == 'kitchen']
    ev = sum(1 for i in kit if rows[i]['radar'] == 'on' or (num(rows[i]['power_max120']) or 0) >= 1000 or (num(rows[i]['kitchen_light_age_s']) or 1e9) <= 900)
    print(f"        kitchen rows {len(kit)} ({ev} with evidence now)")


if __name__ == '__main__':
    print("rows", len(rows), rows[0]['ts'], '->', rows[-1]['ts'], "home rows", sum(1 for r in rows if r['home'] == 'on'))
    report('OLDsim', *run(OLD))
    print("logged zone/fp (production):")
    report('logged', [r['zone'] for r in rows], [r['fp_zone'] for r in rows])
    report('NEW', *run(NEW))
    for name, mod in [('-stale', dict(use_stale=False)), ('-kitEv', dict(kitchen_evidence=False)), ('-bed', dict(bed=False)),
                      ('-hold', dict(pc_hold=60, radar_hold=0, dwell=0)), ('-nodeW', dict(nodes=NODES, w={}))]:
        cfg = dict(NEW); cfg.update(mod)
        report(name, *run(cfg))
    print("--- node weight variants (fp acc vs weak labels is partly circular: weak pc_desk = PC rule)")
    for name, nodes, w in [('4n equal', ['pcdesk', 'cyd', 'workdesk', 'kitchen'], {}),
                           ('4n flat0.5', ['pcdesk', 'cyd', 'workdesk', 'kitchen'], {'workdesk': .5, 'kitchen': .5}),
                           ('4n cyd2', ['pcdesk', 'cyd', 'workdesk', 'kitchen'], {'cyd': 2, 'workdesk': .5, 'kitchen': .5}),
                           ('5n equal', NODES, {})]:
        cfg = dict(NEW); cfg.update(nodes=nodes, w=w)
        print(name); report(name, *run(cfg))
