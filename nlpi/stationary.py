"""Stationary-device filter for BLE adverts on rotating private addresses.

Apple devices change their address about every 15 min, so one physical device
shows up as a stream of short-lived addresses. observe() keeps RSSI history per
address and flags it once the device has looked stationary for min_age_s, so the
caller can ignore those adverts for location. RSSI bands of devices judged
stationary are remembered for band_ttl_s, which lets a fresh address be matched
provisionally to a device seen before the rotation.

All times are caller-supplied floats in seconds; there is no wall clock here.
"""

MIN_WIN_SAMPLES = 3
MIN_PROV_SAMPLES = 4
MAX_BANDS = 8
STALE_S = 1200.0
SEEN_S = 60.0


class _Track:
    """RSSI history for one address."""
    __slots__ = ("first", "last", "win_start", "w_sum", "w_n",
                 "w_min", "w_max", "n_win", "moving", "stat", "prov")

    def __init__(self, now):
        self.first = now
        self.last = now
        self.win_start = now
        self.w_sum = 0
        self.w_n = 0
        self.w_min = None      # min / max of closed-window averages
        self.w_max = None
        self.n_win = 0         # closed windows with >= MIN_WIN_SAMPLES samples
        self.moving = False
        self.stat = False
        self.prov = False


class StationaryFilter:
    def __init__(self, window_s=30.0, spread_db=6.0, min_age_s=300.0, min_windows=8,
                 band_db=3.0, band_ttl_s=10800.0, max_tracks=64):
        self.window_s = window_s
        self.spread_db = spread_db
        self.min_age_s = min_age_s
        self.min_windows = min_windows
        self.band_db = band_db
        self.band_ttl_s = band_ttl_s
        self.max_tracks = max_tracks
        self.tracks = {}       # address -> _Track
        self.bands = []        # list of [center, last_confirmed]

    def observe(self, addr, rssi, now):
        tr = self.tracks.get(addr)
        if tr is None:
            tr = self.tracks[addr] = _Track(now)
            if len(self.tracks) > self.max_tracks:
                self._trim(now)
        if now - tr.win_start >= self.window_s:
            self._close_window(tr, now)
        tr.w_sum += rssi
        tr.w_n += 1
        tr.last = now
        if not tr.moving and not tr.stat and (tr.w_n >= MIN_PROV_SAMPLES or tr.n_win >= 1):
            cur = tr.w_sum / tr.w_n
            tr.prov = any(abs(b[0] - cur) <= self.band_db for b in self._live_bands(now))
        return (tr.stat or tr.prov) and not tr.moving

    def static_count(self, now):
        return sum(1 for tr in self.tracks.values() if tr.stat and now - tr.last <= SEEN_S)

    def _close_window(self, tr, now):
        if tr.w_n >= MIN_WIN_SAMPLES:
            avg = tr.w_sum / tr.w_n
            tr.w_min = avg if tr.w_min is None else min(tr.w_min, avg)
            tr.w_max = avg if tr.w_max is None else max(tr.w_max, avg)
            tr.n_win += 1
        tr.win_start = now
        tr.w_sum = 0
        tr.w_n = 0
        if tr.n_win < 1:
            return
        if tr.w_max - tr.w_min > self.spread_db:
            tr.moving = True
            tr.stat = False
            tr.prov = False
        elif now - tr.first >= self.min_age_s and tr.n_win >= self.min_windows:
            tr.stat = True
            self._add_band((tr.w_min + tr.w_max) / 2.0, now)

    def _trim(self, now):
        for addr in [a for a, tr in self.tracks.items() if now - tr.last > STALE_S]:
            del self.tracks[addr]
        while len(self.tracks) > self.max_tracks:
            del self.tracks[min(self.tracks, key=lambda a: self.tracks[a].last)]

    def _live_bands(self, now):
        self.bands = [b for b in self.bands if now - b[1] <= self.band_ttl_s]
        return self.bands

    def _add_band(self, center, now):
        bands = self._live_bands(now)
        for b in bands:
            if abs(b[0] - center) <= self.band_db:
                b[0] = 0.7 * b[0] + 0.3 * center
                b[1] = now
                return
        bands.append([center, now])
        if len(bands) > MAX_BANDS:
            bands.remove(min(bands, key=lambda b: b[1]))
