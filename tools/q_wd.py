"""workdesk outage detail (run in HA container): gaps > 60 s in csi_n, rssi before/after, heap before/after (jump up = reboot)."""
import sqlite3, sys, time
hours = float(sys.argv[1]) if len(sys.argv) > 1 else 30
node = sys.argv[2] if len(sys.argv) > 2 else 'workdesk'
c = sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro', uri=True)
t1 = time.time(); t0 = t1 - hours * 3600
def rows(e):
    return c.execute("select s.last_updated_ts, s.state from states s join states_meta m on s.metadata_id=m.metadata_id "
                     "where m.entity_id=? and s.last_updated_ts between ? and ? order by 1", (e, t0, t1)).fetchall()
def at(r, t, before=True):
    best = None
    for a, s in r:
        if (a <= t) == before:
            if before: best = s
            else: return s
    return best
n = rows('sensor.rp_%s_csi_n' % node); rs = rows('sensor.rp_%s_csi_rssi' % node); hp = rows('sensor.rp_%s_csi_heap' % node)
for (a, _), (b, _) in zip(n, n[1:]):
    if b - a > 60:
        f = lambda t: time.strftime('%m-%d %H:%M:%S', time.gmtime(t))
        print(f(a), '->', f(b), '%5d s' % (b - a), 'rssi %s->%s' % (at(rs, a), at(rs, b, False)), 'heap %s->%s' % (at(hp, a), at(hp, b, False)))
