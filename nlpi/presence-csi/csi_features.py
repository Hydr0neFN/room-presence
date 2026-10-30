"""CSI shadow zoning: pure logic (no I/O, stdlib only), shared by the service, the offline replay and the scoring tool.

Per node the firmware sends presence/<node>/csi every ~3 s (motion, still in per-mille; ok=0 = no window). The zone log and the
service both reduce that to 10 s means. This module turns those 10 s means into baseline-normalised "z" features and classifies
them against per-zone centroids that were captured while the owner stood in each zone.

  Baseline   per (node, feature): rolling window of log1p(value); location = a LOW quantile (the quiet floor: night / away /
             nobody moving), scale = robust spread (MAD) of the whole window. z = (log1p(x) - floor) / scale, so "quiet" is ~0 and
             activity near the node is a positive number of sigmas. Drift of the floor (reboots, channel changes, a different
             day) is followed by the window, which is what the raw fingerprint features could not do.
  ZoneModel  nearest centroid in z space, pooled per-feature scale, per-feature clipping, distance = RMS over the used features.
"""
import math
import time
from collections import deque

NODES = ("cyd", "kitchen", "pcdesk", "workdesk")
KINDS = ("motion", "still")
NAMES = tuple(f"{n}_{k}" for n in NODES for k in KINDS)
ZONES = ("kitchen", "work_desk", "pc_desk", "bed", "bathroom")

WINDOW_S = 8 * 3600     # baseline window (replay: 8 h best; 24 h cannot follow the day-to-day drift)
LOC_Q = 0.1             # quiet floor = this quantile of log1p(value)
REFRESH_S = 300
MIN_N = 360             # 1 h of 10 s points before a baseline exists
SCALE_FLOOR = 0.15      # log units
Z_CLIP = (-4.0, 12.0)
SMOOTH_N = 6            # classify the mean of the last 6 windows (60 s)


def g(x):
    return math.log1p(max(float(x), 0.0))


def quantile(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return None
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def median(vals):
    return quantile(sorted(vals), 0.5)


class Baseline:
    """Rolling robust baseline. add() every 10 s window, z() to normalise, refresh() is called by add() when due."""

    def __init__(self, window_s=WINDOW_S, loc_q=LOC_Q, refresh_s=REFRESH_S, min_n=MIN_N, scale_floor=SCALE_FLOOR):
        self.window_s, self.loc_q, self.refresh_s, self.min_n, self.scale_floor = window_s, loc_q, refresh_s, min_n, scale_floor
        self.pts = {}          # name -> deque[(t, g)]
        self.ref = {}          # name -> dict(loc, scale, n, span_s, t)
        self.last_quiet = {}   # name -> t of the newest point within 0.5 sigma of the floor
        self.last_refresh = 0.0

    def add(self, t, name, raw):
        if raw is None:
            return
        v = g(raw)
        self.pts.setdefault(name, deque()).append((t, v))
        r = self.ref.get(name)
        if r and v <= r["loc"] + 0.5 * r["scale"]:
            self.last_quiet[name] = t

    def tick(self, t):
        if t - self.last_refresh >= self.refresh_s:
            self.refresh(t)

    def refresh(self, t):
        self.last_refresh = t
        for name, q in self.pts.items():
            while q and q[0][0] < t - self.window_s:
                q.popleft()
            if len(q) < self.min_n:
                self.ref.pop(name, None)
                continue
            vals = sorted(v for _, v in q)
            med = quantile(vals, 0.5)
            mad = quantile(sorted(abs(v - med) for v in vals), 0.5) * 1.4826
            self.ref[name] = {"loc": quantile(vals, self.loc_q), "scale": max(mad, self.scale_floor), "n": len(vals),
                              "span_s": q[-1][0] - q[0][0], "t": t}

    def z(self, name, raw):
        r = self.ref.get(name)
        if r is None or raw is None:
            return None
        return max(Z_CLIP[0], min(Z_CLIP[1], (g(raw) - r["loc"]) / r["scale"]))

    def zvec(self, raw):
        """raw {name: value|None} -> {name: z} for the names that have a baseline."""
        out = {}
        for name, v in raw.items():
            z = self.z(name, v)
            if z is not None:
                out[name] = z
        return out

    def reset(self, name):
        """Node rebooted: its firmware 'still' baseline restarted, so the old window no longer describes it."""
        self.pts.pop(name, None)
        self.ref.pop(name, None)
        self.last_quiet.pop(name, None)

    def info(self, t):
        """Per-name summary for the HA attributes."""
        out = {}
        for name, r in self.ref.items():
            lq = self.last_quiet.get(name)
            out[name] = {"loc": round(r["loc"], 3), "scale": round(r["scale"], 3), "span_h": round(r["span_s"] / 3600, 1),
                         "quiet_age_min": round((t - lq) / 60) if lq else None}
        return out

    def dump(self):
        return {"window_s": self.window_s, "pts": {n: [[int(t), round(v, 3)] for t, v in q] for n, q in self.pts.items()},
                "last_quiet": self.last_quiet}

    def load(self, d):
        for n, pts in d.get("pts", {}).items():
            self.pts[n] = deque((float(t), float(v)) for t, v in pts)
        self.last_quiet = {n: float(t) for n, t in d.get("last_quiet", {}).items()}
        newest = max((q[-1][0] for q in self.pts.values() if q), default=None)
        if newest:
            self.refresh(newest)
            self.last_refresh = 0.0


SD_FLOOR = 1.0
FEATURE_WEIGHTS = {f"{n}_motion": 0.15 for n in NODES}   # replay: motion alone 0.28 balanced accuracy, still alone 0.67


class ZoneModel:
    """Nearest centroid in z space. samples: iterable of (zone, {name: z})."""

    def __init__(self, sd_floor=SD_FLOOR, weights=None):
        self.sd_floor = sd_floor
        self.weights = weights or {}   # name -> weight (default 1); motion is noisy, see FEATURE_WEIGHTS
        self.mu = {}      # zone -> {name: median z}
        self.sd = {}      # name -> pooled within-zone spread
        self.n = {}       # zone -> number of samples

    def fit(self, samples):
        by = {}
        for zone, zv in samples:
            by.setdefault(zone, []).append(zv)
        self.mu, self.n = {}, {z: len(v) for z, v in by.items()}
        dev = {}
        for zone, vs in by.items():
            mu = {}
            for name in {n for zv in vs for n in zv}:
                col = [zv[name] for zv in vs if name in zv]
                mu[name] = median(col)
                dev.setdefault(name, []).extend(abs(x - mu[name]) for x in col)
            self.mu[zone] = mu
        self.sd = {name: max(1.4826 * median(d), self.sd_floor) for name, d in dev.items()}
        return self

    def dist(self, zv):
        """{zone: RMS distance over the names both the sample and the zone centroid have}, plus the names used."""
        out, used = {}, set()
        for zone, mu in self.mu.items():
            acc, k = 0.0, 0.0
            for name, z in zv.items():
                if name in mu:
                    w = self.weights.get(name, 1.0)
                    acc += w * ((z - mu[name]) / self.sd.get(name, self.sd_floor)) ** 2
                    k += w
                    used.add(name)
            if k:
                out[zone] = math.sqrt(acc / k)
        return out, sorted(used)

    def dump(self):
        return {"mu": self.mu, "sd": self.sd, "n": self.n}


def smooth_dist(history):
    """history: iterable of {zone: dist}; per-zone mean over the entries that have the zone."""
    acc, cnt = {}, {}
    for d in history:
        for z, v in d.items():
            acc[z] = acc.get(z, 0.0) + v
            cnt[z] = cnt.get(z, 0) + 1
    return {z: acc[z] / cnt[z] for z in acc}


def decide(dists):
    """-> (zone, confidence) from {zone: distance}. confidence = relative margin to the runner-up, 0..1."""
    if not dists:
        return None, 0.0
    order = sorted(dists.items(), key=lambda kv: kv[1])
    if len(order) == 1:
        return order[0][0], 0.0
    d1, d2 = order[0][1], order[1][1]
    return order[0][0], round(max(0.0, (d2 - d1) / max(d2, 1e-9)), 3)


# ---------------------------------------------------------------- weak labels (rules, no CSI used)
def weak_label(c):
    """c: dict home(bool) pc_online(bool) pc_locked(bool|None) pc_idle(float|None) radar(bool) radar_cm(float|None)
    wd_light(bool) wd_age_s(float|None) kitchen_light(bool) hour(int) -> zone | 'away' | None.
    High-precision rules from context that does not touch CSI; 'bed' is the loosest (night, PC quiet, nothing else on)."""
    if not c["home"]:
        return "away"
    idle = c["pc_idle"]
    if c["pc_online"] and c["pc_locked"] is False and idle is not None and idle <= 60:
        return "pc_desk"
    pc_quiet = idle is None or idle >= 300
    rad = c["radar"]
    if rad and (c["radar_cm"] is None or c["radar_cm"] <= 150) and pc_quiet:
        return "kitchen"
    if c["wd_light"] and c["wd_age_s"] is not None and c["wd_age_s"] <= 900 and not rad and pc_quiet:
        return "work_desk"
    h = c["hour"]
    if (h >= 23 or h < 7) and (idle is None or idle >= 1800) and not rad and not c["wd_light"] and not c["kitchen_light"]:
        return "bed"
    return None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def weak_from_row(r, hour):
    """zone-log CSV row (strings) -> weak label."""
    return weak_label({"home": r.get("home") != "off", "pc_online": r.get("pc_online") == "on",
                       "pc_locked": (r.get("pc_locked") == "on") if r.get("pc_locked") in ("on", "off") else None,
                       "pc_idle": _f(r.get("pc_idle")), "radar": r.get("radar") == "on", "radar_cm": _f(r.get("radar_cm")),
                       "wd_light": r.get("wd_light") == "on", "wd_age_s": _f(r.get("wd_light_age_s")),
                       "kitchen_light": r.get("kitchen_light") == "on", "hour": hour})


def weak_from_ctx(p, now):
    """presence/zone/state payload (+ epoch now) -> weak label. Ages come from the *_ts epochs."""
    ts = _f(p.get("wd_light_ts"))
    return weak_label({"home": p.get("home") != "off", "pc_online": p.get("pc_online") == "on",
                       "pc_locked": (p.get("pc_locked") == "on") if p.get("pc_locked") in ("on", "off") else None,
                       "pc_idle": _f(p.get("pc_idle")), "radar": p.get("radar") == "on", "radar_cm": _f(p.get("radar_cm")),
                       "wd_light": p.get("wd_light") == "on", "wd_age_s": (now - ts) if ts and ts > 0 else None,
                       "kitchen_light": p.get("kitchen_light") == "on", "hour": time.localtime(now).tm_hour})
