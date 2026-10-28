"""Replay the zone-v3 CSV through the REAL Jinja templates of ha/room_presence.yaml (HA functions mocked).
  py -3.13 -I tools/ha_template_replay.py [csv ...]        (default analysis/data/zone-v3-*.csv)
Checks that the templates render (no Jinja errors) and prints transitions / shares like analysis/a10_sim_fix.py.
Mocked: states(), is_state(), state_attr(), now(), this, delay_on / delay_off (counted in 10 s rows), the statistics
sensors (10 min stdev of cyd / kitchen phone, power max 120 s). Not mocked: HA scheduling (updates are evaluated in a fixed
order once per row)."""
import csv, glob, json, os, statistics as st, sys
from collections import Counter, deque
from datetime import datetime, timedelta
import jinja2, yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
cfg = yaml.safe_load(open(os.path.join(ROOT, 'ha', 'room_presence.yaml'), encoding='utf-8'))
NODES = ['pcdesk', 'cyd', 'workdesk', 'kitchen', 'nlpi']


def collect():
    out = {}
    for blk in cfg['template']:
        for kind in ('sensor', 'binary_sensor'):
            for e in blk.get(kind, []):
                out[e['unique_id']] = e
    return out


T = collect()


class St:
    def __init__(self, state, last_changed, attrs=None):
        self.state, self.last_changed, self.attributes = state, last_changed, attrs or {}


class World:
    def __init__(self):
        self.s = {}
        self._cache = {}
        self.attrs = {}
        self.now = datetime(2026, 10, 28)
        self.env = jinja2.Environment(undefined=jinja2.StrictUndefined, extensions=['jinja2.ext.do'])
        self.env.filters['float'] = self._float
        self.env.filters['int'] = self._int
        self.env.filters['states'] = lambda eid: self.states_fn(eid)
        self.env.globals.update(states=self.states_fn, is_state=self.is_state, state_attr=self.state_attr, now=lambda: self.now)

    @staticmethod
    def _float(v, default=jinja2.utils.missing):
        try:
            return float(v)
        except (TypeError, ValueError):
            if default is jinja2.utils.missing:
                raise ValueError('float without default: %r' % (v,))
            return default

    @staticmethod
    def _int(v, default=0):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def set(self, eid, state, attrs=None):
        old = self.s.get(eid)
        lc = old.last_changed if (old and old.state == str(state)) else self.now
        self.s[eid] = St(str(state), lc, attrs)

    class _States:
        def __init__(self, w): self.w = w
        def __call__(self, eid):
            o = self.w.s.get(eid)
            return o.state if o else 'unknown'
        def __getattr__(self, dom):
            w = self.w
            class D:
                def __getattr__(s, name): return w.s.get(dom + '.' + name)
            return D()

    @property
    def states_fn(self):
        return World._States(self)

    def is_state(self, eid, v):
        o = self.s.get(eid)
        return o is not None and o.state in ([v] if isinstance(v, str) else v)

    def state_attr(self, eid, a):
        o = self.s.get(eid)
        return (o.attributes or {}).get(a) if o else None

    def render(self, tpl, this=None, **extra):
        t = self._cache.get(tpl)
        if t is None:
            t = self._cache[tpl] = self.env.from_string(tpl)
        ctx = dict(extra)
        if this is not None:
            ctx['this'] = this
        return t.render(**ctx).strip()


def num(v):
    try:
        return float(v) if v not in ('', None) else None
    except ValueError:
        return None


def main(files):
    fpj = json.load(open(os.path.join(ROOT, 'analysis', 'data', 'fingerprints.json')))
    zones = {z: dict(v['nodes']) for z, v in fpj.items()}
    w = World()
    w.set('sensor.rp_fingerprints', '1', {'nodes': NODES, 'zones': zones, 'feat': {}})
    for k, v in (('rp_feat_weight_db', 0), ('rp_fp_hysteresis', 2.5), ('rp_hysteresis_db', 6), ('rp_min_rssi', -90), ('rp_kitchen_max_cm', 250)):
        w.set('input_number.' + k, v)
    delay = {}     # eid -> counter of consecutive rows the raw state differed
    hist = {n: deque(maxlen=60) for n in NODES}
    pw = deque(maxlen=12)
    zs, fps, ts, rowsout = [], [], [], []

    def bsens(uid, row_idx):
        e = T[uid]
        eid = 'binary_sensor.' + uid
        raw = w.render(e['state']).lower() == 'true'
        d_on = {'00:05:00': 30, '00:10:00': 60}.get(e.get('delay_on'), 0)
        d_off = {'00:00:30': 3}.get(e.get('delay_off'), 0)
        cur = w.s.get(eid).state == 'on' if eid in w.s else False
        c = delay.get(eid, 0)
        if raw != cur:
            c += 1
            if (raw and c > d_on) or (not raw and c > d_off):
                cur = raw; c = 0
        else:
            c = 0
        delay[eid] = c
        w.set(eid, 'on' if cur else 'off', {'nodes': w.render(e['attributes']['nodes'])} if 'attributes' in e else None)

    for path in files:
        for r in csv.DictReader(open(path, newline='', encoding='utf-8')):
            w.now = datetime.strptime(r['ts'], '%Y-%m-%dT%H:%M:%S')
            for n in NODES:
                ph = num(r[n + '_phone'])
                hist[n].append(ph)
                if n != 'pcdesk' or True:
                    w.set(f'sensor.rp_{n}_signal', ph if ph is not None else -127)
                w.set(f'sensor.rp_{n}_apple_n', 3)
            for n in ('cyd', 'kitchen'):
                v = [x for x in hist[n] if x is not None]
                if v:
                    w.set(f'sensor.rp_{n}_phone_heard', v[-1])
                a = {'age_coverage_ratio': 1.0 if len(v) >= 40 and len(hist[n]) >= 60 else 0.1}
                w.set(f'sensor.rp_{n}_phone_sd10m', round(st.pstdev(v), 2) if len(v) >= 2 else 'unknown', a)
            w.set('binary_sensor.yui_home', r['home'])
            w.set('binary_sensor.rp_pc_online', r['pc_online'] if r['pc_online'] in ('on', 'off') else 'unavailable')
            w.set('binary_sensor.rp_pc_locked', r['pc_locked'] if r['pc_locked'] in ('on', 'off') else 'unavailable')
            w.set('sensor.rp_pc_idle', r['pc_idle'] if num(r['pc_idle']) is not None else 'unavailable')
            w.set('binary_sensor.kitchenradar_presence', r['radar'])
            w.set('sensor.kitchenradar_detection_distance', r['radar_cm'] or 'unavailable')
            w.set('sensor.rp_power_max120', r['power_max120'] or 'unavailable')
            age = num(r['kitchen_light_age_s'])
            ts_op = (w.now - timedelta(seconds=age)).timestamp() if age is not None else 0
            w.set('input_datetime.rp_kitchen_light_op', 'x', {'timestamp': ts_op})
            # evaluation order
            for n in NODES:
                bsens(f'rp_{n}_ble_stale', 0)
            bsens('rp_ble_stale_alert', 0)
            bsens('rp_kitchen_radar_hint', 0)
            bsens('rp_kitchen_evidence', 0)
            bsens('rp_night_bed', 0)
            prev_fp = w.s.get('sensor.rp_fp_zone')
            fp = w.render(T['rp_fp_zone']['state'], this=prev_fp or St('none', w.now))
            w.set('sensor.rp_fp_zone', fp)
            prev_z = w.s.get('sensor.yui_room_zone')
            z = w.render(T['yui_room_zone']['state'], this=prev_z or St('unknown', w.now))
            w.set('sensor.yui_room_zone', z)
            zs.append(z); fps.append(fp); ts.append(w.now); rowsout.append(r)
    ch = [i for i, (a, b) in enumerate(zip(zs, zs[1:])) if a != b]
    segs = []; s = 0
    for i in ch:
        segs.append(i + 1 - s); s = i + 1
    segs.append(len(zs) - s)
    home = [z for z, r in zip(zs, rowsout) if r['home'] == 'on']
    c = Counter(home)
    print('rows', len(zs), 'transitions', len(ch), 'segs<60s', sum(1 for x in segs if x < 6), 'median seg %.0fs' % (st.median(segs) * 10))
    print('home-row shares:', ', '.join(f'{k} {100 * v / len(home):.1f}%' for k, v in c.most_common()))
    print('logged zone shares:', ', '.join(f'{k} {100 * v / len(home):.1f}%' for k, v in Counter(r['zone'] for r in rowsout if r['home'] == 'on').most_common()))
    return zs, fps, rowsout


if __name__ == '__main__':
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(ROOT, 'analysis', 'data', 'zone-v3-*.csv')))
    main(files)
