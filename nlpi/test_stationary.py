"""Unit tests for stationary.StationaryFilter (synthetic times and RSSI only)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stationary import StationaryFilter

STEP = 6.0  # seconds between adverts (10 per minute)


def feed(f, addr, rssi, start, n, step=STEP):
    """Observe n adverts and return (results, next_time)."""
    out = []
    now = start
    for _ in range(n):
        out.append(f.observe(addr, rssi, now))
        now += step
    return out, now


def make_static(f, addr, start=0.0):
    """Six minutes of steady -60 +-1 at 10 adverts/min; returns next_time."""
    out = []
    now = start
    for i in range(60):
        out.append(f.observe(addr, -61 if i % 2 else -60, now))
        now += STEP
    return out, now


class TestStationaryFilter(unittest.TestCase):
    def test_steady_becomes_static(self):
        f = StationaryFilter()
        results, now = make_static(f, "addr-1")
        self.assertFalse(results[10])      # 1 min: not static yet
        self.assertFalse(results[49])      # 294 s: just before min_age_s
        self.assertTrue(results[50])       # 300 s: 10 closed windows, aged enough
        self.assertTrue(results[-1])

    def test_walking_never_static(self):
        f = StationaryFilter()
        now = 0.0
        seen = []
        for w in range(24):                # 12 min, window means swing 15 dB
            rssi = -50 if w % 2 == 0 else -65
            out, now = feed(f, "addr-walk", rssi, now, 5)
            seen.extend(out)
        self.assertFalse(any(seen))
        self.assertFalse(f.observe("addr-walk", -50, now))

    def test_new_address_provisional(self):
        f = StationaryFilter()
        _, now = make_static(f, "addr-old")
        out, _ = feed(f, "addr-new", -61, now + 100.0, 4)
        self.assertEqual(out[:3], [False, False, False])
        self.assertTrue(out[3])
        far, _ = feed(f, "addr-far", -40, now + 100.0, 10)
        self.assertFalse(any(far))

    def test_static_then_moves(self):
        f = StationaryFilter()
        _, now = make_static(f, "addr-move")
        out, now = feed(f, "addr-move", -45, now, 6)
        self.assertTrue(out[0])            # first jump advert re-closes the old window
        self.assertFalse(out[5])           # a full -45 window closes: spread 15 dB
        self.assertFalse(f.observe("addr-move", -45, now))

    def test_bands_expire(self):
        f = StationaryFilter()
        _, now = make_static(f, "addr-band")
        out, _ = feed(f, "addr-proxy", -61, now + 100.0, 4)
        self.assertTrue(out[3])
        later = now + 100.0 + f.band_ttl_s + STEP
        self.assertFalse(f.observe("addr-proxy", -61, later))
        fresh, _ = feed(f, "addr-fresh", -61, later, 4)
        self.assertFalse(any(fresh))

    def test_static_count(self):
        f = StationaryFilter()
        _, now = make_static(f, "addr-c1")
        self.assertEqual(f.static_count(now), 1)
        feed(f, "addr-c2", -60, now, 4)                   # recent but not static
        self.assertEqual(f.static_count(now), 1)
        self.assertEqual(f.static_count(now + 61.0), 0)   # both too old for the 60 s window

    def test_max_tracks(self):
        f = StationaryFilter(max_tracks=3)
        for i in range(5):
            f.observe(f"addr-{i}", -60, float(i))
        self.assertEqual(sorted(f.tracks), ["addr-2", "addr-3", "addr-4"])
        g = StationaryFilter(max_tracks=2)
        g.observe("addr-a", -60, 0.0)
        g.observe("addr-b", -60, 1200.0)
        g.observe("addr-c", -60, 1250.0)
        self.assertEqual(sorted(g.tracks), ["addr-b", "addr-c"])  # addr-a stale


if __name__ == "__main__":
    unittest.main()
