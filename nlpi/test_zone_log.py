"""Unit test for zone_log.State.make_row (synthetic times and payloads, no MQTT)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import zone_log
from zone_log import HEAD, State


def col(row, name):
    return row[HEAD.index(name)]


class TestRow(unittest.TestCase):
    def setUp(self):
        self.t0 = time.mktime((2026, 10, 27, 14, 0, 0, 0, 0, -1))
        self.s = State()

    def ctx(self, now, **kw):
        p = {"zone": "pc_desk", "fp": "kitchen", "home": "on", "pc_online": "on", "pc_locked": "off", "pc_idle": "3",
             "radar": "off", "radar_cm": "110", "wd_light": "off", "wd_light_ts": now - 5000, "lightbar": "off",
             "lightbar_ts": now - 100, "link_kitchenradar": "1.1", "link_neopixel": "0.7", "link_kitchenrssi": "1.3", "kitchen_light": "off", "kitchen_light_ts": now - 60}
        p.update(kw)
        self.s.add_ctx(now, p)

    def test_row_has_head_width_and_values(self):
        t = self.t0
        self.ctx(t)
        for i in range(5):
            self.s.add_csi(t + i, "pcdesk", {"ok": 1, "motion": 20 + i, "still": 30})
            self.s.add_csi(t + i, "kitchen", {"ok": 0, "motion": 99, "still": 99})
        self.s.add_apple(t + 1, "cyd", {"irk": [{"id": "phone", "n": 2, "avg": -50}, {"id": "watch", "n": 1, "avg": -60}]})
        self.s.add_power(t + 2, {"watts": 252})
        row = self.s.make_row(t + 5)
        self.assertEqual(len(row), len(HEAD))
        self.assertEqual(col(row, "csi_pcdesk_motion"), 22.0)
        self.assertEqual(col(row, "csi_pcdesk_still"), 30.0)
        self.assertEqual(col(row, "csi_kitchen_motion"), "")      # ok=0 ignored
        self.assertEqual(col(row, "cyd_phone"), -50.0)
        self.assertEqual(col(row, "cyd_watch"), -60.0)
        self.assertEqual(col(row, "power_w"), 252)
        self.assertEqual(col(row, "wd_light_age_s"), 5005)
        self.assertEqual(col(row, "kitchen_light"), "off")
        self.assertEqual(col(row, "kitchen_light_age_s"), 65)
        self.assertEqual(col(row, "gaming"), "")
        self.assertNotIn("weak_label", HEAD)

    def test_negative_still_skipped(self):
        self.ctx(self.t0)
        self.s.add_csi(self.t0, "cyd", {"ok": 1, "motion": 10, "still": -1})
        row = self.s.make_row(self.t0 + 1)
        self.assertEqual(col(row, "csi_cyd_motion"), 10.0)
        self.assertEqual(col(row, "csi_cyd_still"), "")

    def test_stale_context_blank_no_label(self):
        self.ctx(self.t0)
        row = self.s.make_row(self.t0 + zone_log.CTX_STALE_S + 1)
        self.assertEqual(col(row, "zone"), "")

    def test_power_window_and_kitchen_label(self):
        t = self.t0
        self.ctx(t, pc_locked="on", pc_idle="9000")
        self.s.add_power(t, {"watts": 2100})
        self.s.add_power(t + 60, {"watts": 150})
        row = self.s.make_row(t + 61)
        self.assertEqual(col(row, "power_max120"), 2100)
        row = self.s.make_row(t + 130)                          # the 2100 W sample left the 120 s window
        self.ctx(t + 130, pc_locked="on", pc_idle="9000")
        row = self.s.make_row(t + 131)
        self.assertEqual(col(row, "power_max120"), 150)


if __name__ == "__main__":
    unittest.main()
