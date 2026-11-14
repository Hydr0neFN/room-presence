"""Unit tests for tracker, tracker_svc.Service and tracker_seed (stdlib unittest, no MQTT, temp dirs only).

Run: python3 -m unittest discover -s nlpi/presence-track -p "test_*.py"
"""
import csv
import datetime
import json
import math
import os
import random
import tempfile
import time
import unittest

import tracker as tr
import tracker_seed as seed_cli
import tracker_svc as svc

# Typical per-zone means of the synthetic rows (RSSI dBm, CSI motion/still per mille). Quiet is ~20 per mille.
RSSI = {
    "kitchen": {"cyd": -70, "kitchen": -45, "nlpi": -65, "pcdesk": -75, "workdesk": -72},
    "work_desk": {"cyd": -75, "kitchen": -72, "nlpi": -65, "pcdesk": -58, "workdesk": -45},
    "pc_desk": {"cyd": -72, "kitchen": -75, "nlpi": -62, "pcdesk": -42, "workdesk": -55},
    "bed": {"cyd": -60, "kitchen": -78, "nlpi": -70, "pcdesk": -80, "workdesk": -78},
    "bathroom": {"cyd": -65, "kitchen": -60, "nlpi": -68, "pcdesk": -78, "workdesk": -75},
    "quiet": {"cyd": -72, "kitchen": -72, "nlpi": -68, "pcdesk": -72, "workdesk": -72},
}
CSI = {
    "kitchen": {"cyd": 45, "kitchen": 160, "pcdesk": 20, "workdesk": 20},
    "work_desk": {"cyd": 20, "kitchen": 20, "pcdesk": 55, "workdesk": 170},
    "pc_desk": {"cyd": 20, "kitchen": 20, "pcdesk": 180, "workdesk": 45},
    "bed": {"cyd": 20, "kitchen": 20, "pcdesk": 20, "workdesk": 20},
    "bathroom": {"cyd": 35, "kitchen": 45, "pcdesk": 20, "workdesk": 20},
    "quiet": {"cyd": 20, "kitchen": 20, "pcdesk": 20, "workdesk": 20},
}
CTX = {
    "pc_desk": dict(home="on", pc_online="on", pc_locked="off", pc_idle="5", radar="off", radar_cm="",
                    wd_light="off", wd_light_age_s="", kitchen_light="off"),
    "kitchen": dict(home="on", pc_online="on", pc_locked="on", pc_idle="5000", radar="on", radar_cm="100",
                    wd_light="off", wd_light_age_s="", kitchen_light="off"),
    "work_desk": dict(home="on", pc_online="on", pc_locked="on", pc_idle="5000", radar="off", radar_cm="",
                      wd_light="on", wd_light_age_s="60", kitchen_light="off"),
    "bed": dict(home="on", pc_online="on", pc_locked="on", pc_idle="5000", radar="off", radar_cm="",
                wd_light="off", wd_light_age_s="", kitchen_light="off"),
    "bathroom": dict(home="on", pc_online="on", pc_locked="on", pc_idle="5000", radar="off", radar_cm="",
                     wd_light="off", wd_light_age_s="", kitchen_light="off"),
    "quiet": dict(home="on", pc_online="on", pc_locked="on", pc_idle="5000", radar="off", radar_cm="",
                  wd_light="off", wd_light_age_s="", kitchen_light="off"),
    "away": dict(home="off", pc_online="off", pc_locked="", pc_idle="", radar="off", radar_cm="",
                 wd_light="off", wd_light_age_s="", kitchen_light="off"),
}


def make_row(rng, scene, ts="2026-11-10T12:00:00", noise=1.0):
    """Synthetic zone-log row: context of the scene, RSSI / CSI around that scene's typical levels."""
    row = {"ts": ts}
    row.update(CTX[scene])
    for n in tr.BLE_NODES:
        row[n + "_phone"] = "%.1f" % rng.gauss(RSSI[scene][n], 2.0 * noise)
        row[n + "_watch"] = "%.1f" % rng.gauss(RSSI[scene][n] - 3, 2.0 * noise)
    for n in tr.CSI_NODES:
        row["csi_%s_motion" % n] = "%.1f" % max(0.0, rng.gauss(CSI[scene][n], 5.0 * noise))
        row["csi_%s_still" % n] = "%.1f" % max(0.0, rng.gauss(CSI[scene][n], 5.0 * noise))
    return row


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def epoch(s):
    return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))


HEAD = (["ts", "home", "pc_online", "pc_locked", "pc_idle", "radar", "radar_cm", "wd_light", "wd_light_age_s",
         "kitchen_light"] + [n + "_phone" for n in tr.BLE_NODES] + [n + "_watch" for n in tr.BLE_NODES]
        + ["csi_%s_%s" % (n, k) for n in tr.CSI_NODES for k in ("motion", "still")])


def write_rows(path, rows, header=HEAD):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


class FeatureTests(unittest.TestCase):
    def test_parses_skips_and_log1p(self):
        row = {"cyd_watch": "-61.5", "cyd_phone": "", "kitchen_phone": "-70", "csi_cyd_motion": "0",
               "csi_cyd_still": "-1", "csi_kitchen_motion": "3", "csi_kitchen_still": "abc"}
        f = tr.features(row)
        self.assertEqual(f["w_cyd"], -61.5)
        self.assertNotIn("p_cyd", f)
        self.assertEqual(f["p_kitchen"], -70.0)
        self.assertEqual(f["c_cyd_motion"], 0.0)
        self.assertNotIn("c_cyd_still", f)          # negative still is skipped
        self.assertAlmostEqual(f["c_kitchen_motion"], math.log1p(3))
        self.assertNotIn("c_kitchen_still", f)      # unparsable is skipped


class FitTests(unittest.TestCase):
    def test_min_n_and_var_floors(self):
        samples = []
        for i in range(60):
            f = {"c_keep": 2.0, "w_const": 2.0, "w_spread": float(i)}
            if i < 59:
                f["c_short"] = 1.0
            samples.append(("a", f))
        m = tr.fit(samples)
        self.assertNotIn("c_short", m["a"])
        self.assertAlmostEqual(m["a"]["c_keep"][1], tr.VAR_FLOOR_CSI)
        self.assertAlmostEqual(m["a"]["w_const"][1], tr.VAR_FLOOR_RSSI)
        self.assertGreater(m["a"]["w_spread"][1], tr.VAR_FLOOR_RSSI)
        self.assertAlmostEqual(m["a"]["w_spread"][0], 29.5)


class SeedTests(unittest.TestCase):
    def test_pooled_var_and_own_var(self):
        model = {"kitchen": {"w_cyd": (0.0, 4.0), "c_cyd_motion": (1.0, 0.2)},
                 "pc_desk": {"w_cyd": (5.0, 9.0), "c_cyd_motion": (2.0, 0.1)},
                 "bed": {("f%d" % i): (0.0, 1.0) for i in range(tr.MIN_ZONE_FEATS)}}
        own = [{"w_cyd": float(i), "c_cyd_motion": 1.0 if i % 2 == 0 else 3.0} for i in range(3)]
        many = [{"w_cyd": 2.0, "c_cyd_motion": 1.0 if i % 2 == 0 else 3.0} for i in range(60)]
        bed_before = dict(model["bed"])
        tr.add_seeds(model, {"bathroom": own, "work_desk": many, "bed": many})
        self.assertAlmostEqual(model["bathroom"]["w_cyd"][1], 6.5 * tr.SEED_VAR_MULT)   # pooled * mult
        self.assertAlmostEqual(model["work_desk"]["c_cyd_motion"][1], 1.0)              # own variance
        self.assertEqual(model["bed"], bed_before)                  # >= MIN_ZONE_FEATS: seeds ignored


class EmissionTests(unittest.TestCase):
    def test_only_common_features(self):
        model = {"a": {"x": (0.0, 1.0), "y": (0.0, 1.0)}, "b": {"x": (2.0, 4.0), "y": (2.0, 4.0)}}
        e = tr.emission(model, {"x": 1.0, "z": 99.0})
        self.assertIsNotNone(e)
        want_a = (-0.5 * math.log(1.0) - 0.5) / 1.0 ** tr.TEMP
        want_b = (-0.5 * math.log(4.0) - 0.5 * (1.0 - 2.0) ** 2 / 4.0) / 1.0 ** tr.TEMP
        self.assertAlmostEqual(e["a"], want_a)
        self.assertAlmostEqual(e["b"], want_b)
        self.assertIsNone(tr.emission(model, {"z": 1.0}))


class BurstTests(unittest.TestCase):
    def test_steady_jump_and_warmup(self):
        b, rng = tr.Burst(), random.Random(5)
        for _ in range(40):
            b.step({"csi_cyd_motion": str(20 + rng.gauss(0, 0.5)), "csi_pcdesk_motion": str(20 + rng.gauss(0, 0.5))})
        self.assertEqual(b.step({"csi_cyd_motion": "500", "csi_pcdesk_motion": "600"}), 2)
        b2 = tr.Burst()
        for _ in range(5):
            self.assertEqual(b2.step({"csi_cyd_motion": "900", "csi_pcdesk_motion": "900"}), 0)   # < BURST_MIN


def two_zone_model():
    """kitchen at w_cyd=0, bed at w_cyd=5, both with unit variance (raw RSSI is not floored here, the model is explicit)."""
    return {"kitchen": {"w_cyd": (0.0, 1.0)}, "bed": {"w_cyd": (5.0, 1.0)}}


def simple_row(scene="quiet", w="-0.0", motion="20", ts="2026-11-10T12:00:00"):
    row = dict(CTX[scene], ts=ts, cyd_watch=w)
    for n in tr.CSI_NODES:
        row["csi_%s_motion" % n] = motion
    return row


class TrackerTests(unittest.TestCase):
    def test_holds_until_burst_then_follows_clear_evidence(self):
        t = tr.Tracker(two_zone_model())
        for _ in range(40):
            res = t.step(simple_row(w="0.0"))
        self.assertEqual(res["zone"], "kitchen")
        res = t.step(simple_row(w="2.6"))            # bed slightly better: weak evidence, no burst
        self.assertEqual(res["zone"], "kitchen")
        self.assertFalse(res["burst"])
        t.step(simple_row(w="2.6", motion="600"))    # two nodes jump -> burst opens the transition matrix
        for _ in range(4):
            res = t.step(simple_row(w="5.0"))
        self.assertEqual(res["zone"], "bed")

    def test_clamp_and_hold_after_pc_input_stops(self):
        t = tr.Tracker({"pc_desk": {"w_cyd": (0.0, 1.0)}, "bed": {"w_cyd": (1.5, 1.0)}})
        res = t.step(simple_row("pc_desk", w="0.75"))
        self.assertEqual(res["zone"], "pc_desk")
        self.assertEqual(res["clamp"], "pc_active")
        self.assertGreater(res["confidence"], 0.99)
        for _ in range(60):
            res = t.step(simple_row("quiet", w="0.75"))
        self.assertEqual(res["zone"], "pc_desk")
        self.assertIsNone(res["clamp"])

    def test_reported_zone_is_sticky_below_switch_conf(self):
        t = tr.Tracker(two_zone_model())
        for _ in range(40):
            t.step(simple_row(w="0.0"))
        t.step(simple_row(w="2.5", motion="600"))    # burst: the transition matrix opens
        res = t.step(simple_row(w="2.8"))            # bed wins, but only just
        self.assertGreater(res["post"]["bed"], res["post"]["kitchen"])
        self.assertLess(res["post"]["bed"], tr.SWITCH_CONF)
        self.assertEqual(res["zone"], "kitchen")
        self.assertEqual(res["held"], 1)
        self.assertGreaterEqual(res["confidence"], tr.HELD_CONF)
        for _ in range(3):
            res = t.step(simple_row(w="5.0"))        # clear evidence
        self.assertGreaterEqual(res["post"]["bed"], tr.SWITCH_CONF)
        self.assertEqual(res["zone"], "bed")
        self.assertEqual(res["held"], 0)

    def test_held_zone_reports_floor_confidence_and_gives_up_after_held_max(self):
        model = {"kitchen": {"w_cyd": (0.0, 1.0)}, "bed": {"w_cyd": (5.0, 1.0)}, "pc_desk": {"w_cyd": (5.2, 1.0)}}
        t = tr.Tracker(model)
        seen = []
        for i in range(tr.HELD_MAX + 1):
            t.zone = "kitchen" if i == 0 else t.zone
            t.post = {"kitchen": 0.05, "bed": 0.48, "pc_desk": 0.47}   # nobody reaches SWITCH_CONF, kitchen is gone
            res = t.step(simple_row(w="5.1"))
            self.assertLess(max(res["post"].values()), tr.SWITCH_CONF)
            seen.append(res["zone"])
            if res["zone"] == "kitchen":
                self.assertEqual(res["confidence"], tr.HELD_CONF)   # floor: HA keeps the held zone
        self.assertEqual(seen[:tr.HELD_MAX], ["kitchen"] * tr.HELD_MAX)
        self.assertNotEqual(seen[-1], "kitchen")                    # the hold is limited: no stuck zone

    def test_away_row_keeps_posterior(self):
        t = tr.Tracker(two_zone_model())
        t.step(simple_row(w="0.0"))
        before = dict(t.post)
        res = t.step(simple_row("away", w="5.0"))
        self.assertEqual(res["zone"], "unknown")
        self.assertEqual(res["confidence"], 0.0)
        self.assertTrue(res["away"])
        self.assertEqual(t.post, before)


def scene_rows(scene, n=240, day="2026-11-10", hour=12, seed=1):
    rng = random.Random(seed)
    base = datetime.datetime.fromisoformat("%sT%02d:00:00" % (day, hour))
    return [make_row(rng, scene, (base + datetime.timedelta(seconds=10 * i)).isoformat()) for i in range(n)]


def day_rows():
    return (scene_rows("bed", hour=2, seed=2) + scene_rows("work_desk", hour=8, seed=3)
            + scene_rows("kitchen", hour=12, seed=4) + scene_rows("pc_desk", hour=16, seed=5))


class TrainTests(unittest.TestCase):
    def test_weak_zones_and_seeded_bathroom(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "zone-v3-2026-11-10.csv")
            write_rows(path, day_rows())
            model, info = tr.train(d, [path])
            self.assertEqual(set(info), set(tr.WEAK_ZONES))
            for zone in tr.WEAK_ZONES:
                self.assertEqual(info[zone]["src"], "weak")
                self.assertGreaterEqual(info[zone]["feats"], tr.MIN_ZONE_FEATS)
            self.assertNotIn("bathroom", model)
            bathroom = [tr.features(r) for r in scene_rows("bathroom", n=5, seed=6)]
            model, info = tr.train(d, [path], {"bathroom": bathroom})
            self.assertIn("bathroom", model)
            self.assertEqual(info["bathroom"]["src"], "seed")
            self.assertEqual(info["bathroom"]["n"], 5)
            self.assertEqual(set(info), set(tr.WEAK_ZONES) | {"bathroom"})


def train_rows(day):
    return (scene_rows("bed", day=day, hour=2, seed=2) + scene_rows("work_desk", day=day, hour=8, seed=3)
            + scene_rows("kitchen", day=day, hour=12, seed=4) + scene_rows("pc_desk", day=day, hour=16, seed=5))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name
        self.logdir = os.path.join(self.dir, "log")
        self.track = os.path.join(self.dir, "track")
        os.makedirs(self.logdir)

    def tearDown(self):
        self._td.cleanup()

    def write_day(self, t, rows):
        write_rows(os.path.join(self.logdir, "zone-v3-%s.csv" % svc.day_of(t)), rows)

    def make_service(self, now):
        self.write_day(now - 86400, train_rows(svc.day_of(now - 86400)))
        return svc.Service(self.logdir, self.track, refit_hour=-1, now=now)

    def test_fresh_row_then_stale(self):
        now = time.time()
        s = self.make_service(now)
        p = s.tick(now)
        self.assertIsNone(p)                                  # nothing new yet
        row = make_row(random.Random(9), "kitchen", ts=iso(now))
        self.write_day(now, [row])
        p = s.tick(now + 1)
        self.assertIsNotNone(p)
        for k in ("zone", "confidence", "ts"):
            self.assertIn(k, p)
        self.assertEqual(p["ts"], int(now + 1))
        row2 = make_row(random.Random(10), "kitchen", ts=iso(now + 10))
        self.write_day(now, [row, row2])
        self.assertIsNone(s.tick(now + 60))                   # row2 processed but 50 s old

    def test_midnight_file_change(self):
        day1 = epoch("2026-11-13T23:59:50")
        self.write_day(day1 - 86400, train_rows("2026-11-12"))
        self.write_day(day1, [make_row(random.Random(11), "kitchen", ts=iso(day1))])
        s = svc.Service(self.logdir, self.track, refit_hour=-1, now=day1)
        self.assertIsNone(s.tick(day1 + 1))                   # only the warm-up row exists
        self.write_day(day1 + 10, [make_row(random.Random(12), "kitchen", ts=iso(day1 + 10))])
        p = s.tick(day1 + 11)
        self.assertIsNotNone(p)
        self.assertTrue(p["row_ts"].startswith("2026-11-14"))

    def test_calibrate_stores_seeds_and_refits(self):
        now = time.time()
        s = self.make_service(now)
        s.on_cal_status({"state": "recording", "zone": "kitchen"}, now)
        rows = [make_row(random.Random(20 + i), "kitchen", ts=iso(now + 10 * i)) for i in range(4)]
        self.write_day(now, rows)
        self.assertIsNotNone(s.tick(now + 1))
        s.on_cal_status({"state": "done", "zone": "kitchen"}, now + 50)
        self.assertEqual(len(s.seeds["cal"]["kitchen"]), 4)
        self.assertEqual(s.cal["state"], "idle")
        self.assertEqual(s.trained_at, int(now + 50))
        self.assertGreaterEqual(len(s.model), 2)
        saved = tr.load_seeds(os.path.join(self.track, "seeds.json"))
        self.assertEqual(len(saved["cal"]["kitchen"]), 4)


class TailTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._td.name, "zone-v3-2026-11-14.csv")

    def tearDown(self):
        self._td.cleanup()

    def rows(self, n, start=0):
        return [make_row(random.Random(40 + i), "kitchen", ts=iso(epoch("2026-11-14T10:00:00") + 10 * i))
                for i in range(start, start + n)]

    def test_reads_only_appended_complete_rows(self):
        t = tr.Tail()
        self.assertEqual(t.read(self.path), [])                # missing file
        write_rows(self.path, self.rows(3))
        got = t.read(self.path)
        self.assertEqual([r["ts"] for r in got], [r["ts"] for r in tr.load_rows(self.path)])
        self.assertIn("_t", got[0])
        self.assertEqual(t.read(self.path), [])                # nothing new
        write_rows(self.path, self.rows(5))                    # same 3 rows + 2 more
        full = tr.load_rows(self.path)
        with open(self.path, "ab") as f:
            f.write(b"2026-11-14T11:00:00,kit")                # a row that is still being written
        got = t.read(self.path)
        self.assertEqual([r["ts"] for r in got], [r["ts"] for r in full[3:]])
        self.assertEqual(tr.features(got[0]), tr.features(full[3]))
        self.assertEqual(t.read(self.path), [])
        with open(self.path, "ab") as f:
            f.write(b"chen\n")
        self.assertEqual([r["ts"] for r in t.read(self.path)], ["2026-11-14T11:00:00"])

    def test_restarts_on_a_shorter_file_and_on_a_new_path(self):
        t = tr.Tail()
        write_rows(self.path, self.rows(4))
        self.assertEqual(len(t.read(self.path)), 4)
        write_rows(self.path, self.rows(1, start=7))           # the file became shorter
        self.assertEqual(len(t.read(self.path)), 1)
        other = self.path.replace("11-14", "11-15")
        write_rows(other, self.rows(2))
        self.assertEqual(len(t.read(other)), 2)


class SeedFileTests(unittest.TestCase):
    def test_roundtrip_and_add_replace(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "seeds.json")
            seeds = {"cal": {"kitchen": [{"w_cyd": -50.0}]}, "manual": {}}
            tr.save_seeds(path, seeds)
            self.assertEqual(tr.load_seeds(path), seeds)
            log = os.path.join(d, "log")
            os.makedirs(log)
            rows = [make_row(random.Random(i), "bathroom", ts="2026-11-12T06:0%d:00" % i) for i in range(4)]
            write_rows(os.path.join(log, "zone-v3-2026-11-12.csv"), rows)
            span = ("2026-11-12T06:00", "2026-11-12T06:05")
            self.assertEqual(seed_cli.add(log, path, "manual", "bathroom", span[0], span[1]), 4)
            self.assertEqual(len(tr.load_seeds(path)["manual"]["bathroom"]), 4)
            self.assertEqual(seed_cli.add(log, path, "manual", "bathroom", span[0], span[1]), 4)
            self.assertEqual(len(tr.load_seeds(path)["manual"]["bathroom"]), 8)
            self.assertEqual(seed_cli.add(log, path, "manual", "bathroom", span[0], span[1], replace=True), 4)
            self.assertEqual(len(tr.load_seeds(path)["manual"]["bathroom"]), 4)
            c = seed_cli.counts(path)
            self.assertEqual(c[("manual", "bathroom")], 4)
            self.assertEqual(c[("cal", "kitchen")], 1)


if __name__ == "__main__":
    unittest.main()
