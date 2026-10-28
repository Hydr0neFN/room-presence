"""CSI-stream gap / RSSI summary per node from the HA recorder (run inside the HA container, read-only).
  ssh nl-pi-lan "docker exec -i homeassistant python - <hours_back> [gap_s]" < tools/q_gaps.py
Counts gaps > gap_s (default 10) between recorded updates of sensor.rp_<node>_csi_n (~2 Hz) and prints the 5 longest."""
import sqlite3, sys, time, statistics as st
hours = float(sys.argv[1]) if len(sys.argv) > 1 else 12
gap = float(sys.argv[2]) if len(sys.argv) > 2 else 10
c = sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro', uri=True)
t1 = time.time(); t0 = t1 - hours * 3600
def rows(e):
    return c.execute("select s.last_updated_ts, s.state from states s join states_meta m on s.metadata_id=m.metadata_id "
                     "where m.entity_id=? and s.last_updated_ts between ? and ? order by 1", (e, t0, t1)).fetchall()
for n in ('pcdesk', 'workdesk', 'kitchen', 'cyd'):
    r = rows('sensor.rp_%s_csi_n' % n)
    ts = [a for a, _ in r]
    gaps = sorted(((b - a, a) for a, b in zip(ts, ts[1:]) if b - a > gap), reverse=True)
    rs = [float(s) for _, s in rows('sensor.rp_%s_csi_rssi' % n) if s not in ('unknown', 'unavailable')]
    tot = sum(g for g, _ in gaps)
    print('%-8s rows %5d gaps>%gs: %3d total %5.0f s (%.2f%%) longest %s | rssi mean %.1f sd %.1f' % (
        n, len(r), gap, len(gaps), tot, 100 * tot / (hours * 3600),
        [(int(g), time.strftime('%H:%M', time.gmtime(a))) for g, a in gaps[:4]],
        st.mean(rs) if rs else 0, st.pstdev(rs) if len(rs) > 1 else 0))
