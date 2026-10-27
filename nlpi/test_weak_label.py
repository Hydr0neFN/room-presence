"""Unit tests for weak_label.classify (synthetic inputs only)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from weak_label import classify


def ctx(**kw):
    c = {"home": "on", "minute": 14 * 60, "pc_online": "on", "pc_locked": "on", "pc_idle": 900, "radar": "off", "radar_cm": 400,
         "power_max120": 150, "wd_light": "off", "wd_light_age": 5000, "wd_phone": -85, "wd_phone_med": -88, "phone_sd_10m": 8}
    c.update(kw)
    return c


class TestWeakLabel(unittest.TestCase):
    def test_nothing_is_empty(self):
        self.assertEqual(classify(ctx()), ("", []))

    def test_pc_desk(self):
        self.assertEqual(classify(ctx(pc_locked="off", pc_idle=5))[0], "pc_desk")
        self.assertEqual(classify(ctx(pc_locked="off", pc_idle=60))[0], "")        # idle limit is < 60
        self.assertEqual(classify(ctx(pc_locked="on", pc_idle=5))[0], "")          # locked
        self.assertEqual(classify(ctx(pc_online="off", pc_locked="off", pc_idle=5))[0], "")
        self.assertEqual(classify(ctx(pc_locked="off", pc_idle=None))[0], "")

    def test_kitchen_radar(self):
        self.assertEqual(classify(ctx(radar="on", radar_cm=110))[0], "kitchen")
        self.assertEqual(classify(ctx(radar="on", radar_cm=250))[0], "")           # limit is < 250
        self.assertEqual(classify(ctx(radar="on", radar_cm=None))[0], "")
        self.assertEqual(classify(ctx(radar="off", radar_cm=100))[0], "")

    def test_kitchen_power(self):
        self.assertEqual(classify(ctx(power_max120=2000))[0], "kitchen")
        self.assertEqual(classify(ctx(power_max120=999))[0], "")
        self.assertEqual(classify(ctx(power_max120=None))[0], "")

    def test_light_alone_is_not_presence(self):
        self.assertEqual(classify(ctx(wd_light="on", wd_phone=None))[0], "")
        self.assertEqual(classify(ctx(wd_light="on", wd_phone=-95, wd_phone_med=-88))[0], "")   # phone weaker than median
        self.assertEqual(classify(ctx(wd_light="on", wd_phone_med=None))[0], "")                 # no baseline yet
        self.assertEqual(classify(ctx(wd_light="off", wd_phone=-70))[0], "")

    def test_work_desk_a(self):
        c = ctx(wd_light="on", pc_locked="off", pc_idle=400, wd_phone=-80, wd_phone_med=-88)
        self.assertEqual(classify(c), ("work_desk", ["work_desk"]))
        self.assertEqual(classify(dict(c, pc_idle=200))[0], "")                    # idle too short for A, light old
        self.assertEqual(classify(dict(c, pc_online="off"))[0], "")
        self.assertEqual(classify(ctx(wd_light="on", wd_phone=-80))[0], "work_desk")  # locked PC counts as inactive

    def test_work_desk_b_recent_switch(self):
        c = ctx(wd_light="on", pc_locked="off", pc_idle=90, wd_light_age=120, wd_phone=-90, wd_phone_med=-88)
        self.assertEqual(classify(c)[0], "work_desk")                              # within 3 dB of the median
        self.assertEqual(classify(dict(c, wd_phone=-95))[0], "")
        self.assertEqual(classify(dict(c, wd_light_age=700))[0], "")
        self.assertEqual(classify(dict(c, pc_idle=30)), ("pc_desk", ["pc_desk"]))   # PC in use: pc_desk only

    def test_bed(self):
        c = ctx(minute=3 * 60, phone_sd_10m=1.5)
        self.assertEqual(classify(c)[0], "bed")
        self.assertEqual(classify(dict(c, minute=20))[0], "")                      # before 00:30
        self.assertEqual(classify(dict(c, minute=7 * 60 + 30))[0], "")             # 07:30 excluded
        self.assertEqual(classify(dict(c, phone_sd_10m=4.0))[0], "")
        self.assertEqual(classify(dict(c, phone_sd_10m=None))[0], "")
        self.assertEqual(classify(dict(c, pc_locked="off", pc_idle=2000))[0], "")
        self.assertEqual(classify(dict(c, radar="on", radar_cm=500))[0], "")
        self.assertEqual(classify(dict(c, radar=""))[0], "")

    def test_conflict_is_empty(self):
        c = ctx(pc_locked="off", pc_idle=5, radar="on", radar_cm=100)
        label, fired = classify(c)
        self.assertEqual(label, "")
        self.assertEqual(sorted(fired), ["kitchen", "pc_desk"])

    def test_away_is_empty(self):
        self.assertEqual(classify(ctx(home="off", pc_locked="off", pc_idle=5)), ("", []))

    def test_garbage_values(self):
        self.assertEqual(classify(ctx(pc_idle="unavailable", pc_locked="off", radar_cm="unknown", power_max120="x")), ("", []))


if __name__ == "__main__":
    unittest.main()
