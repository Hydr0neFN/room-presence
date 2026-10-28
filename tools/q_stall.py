import sqlite3, sys, datetime
c = sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro', uri=True)
ents = sys.argv[3:]
t0 = datetime.datetime.fromisoformat(sys.argv[1]).timestamp(); t1 = datetime.datetime.fromisoformat(sys.argv[2]).timestamp()
for e in ents:
    q = ("select s.last_updated_ts, s.state from states s join states_meta m on s.metadata_id=m.metadata_id "
         "where m.entity_id=? and s.last_updated_ts between ? and ? order by 1")
    rows = c.execute(q, (e, t0, t1)).fetchall()
    print(e, len(rows))
    last = None; out = []
    for ts, st in rows:
        if st != last:
            out.append((datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%m-%d %H:%M:%S'), st)); last = st
    print(' changes', len(out))
    step = max(1, len(out) // 25)
    print(' ', out[::step][:30], out[-3:])
