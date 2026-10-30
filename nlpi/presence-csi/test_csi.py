"""Unit tests for csi_features and csi_svc.Engine (stdlib unittest, no MQTT). Run: python3 -m unittest test_csi -v"""
import json
import os
import random
import sqlite3
import tempfile
import unittest

import csi_features as cf
import csi_svc as svc

T0 = 1_800_000_000.0   # fixed fake epoch


def quiet_raw(rng, lvl=20.0):
    return {n: max(0.0, rng.gauss(lvl, 2.0)) for n in cf.NAMES}


class BaselineTests(unittest.TestCase):
    def feed(self, bl, n, fn, t0=T0):
        rng = random.Random(1)
        for i in range(n):
            t = t0 + 10 * i
            bl.tick(t)
            for name, v in fn(rng, i).items():
                bl.add(t, name, v)
        return t0 + 10 * n

    def test_no_reference_before_min_n(self):
        bl = cf.Baseline()
        self.feed(bl, cf.MIN_N - 10, lambda r, i: {"cyd_still": 20 + r.random()})
        bl.refresh(T0 + 10 * cf.MIN_N)
        self.assertIsNone(bl.z("cyd_still", 50))

    def test_quiet_is_near_zero_and_activity_positive(self):
        bl = cf.Baseline()
        t = self.feed(bl, 1000, lambda r, i: {"cyd_still": max(0.0, r.gauss(20, 2))})
        bl.refresh(t)
        self.assertLess(abs(bl.z("cyd_still", 18)), 3.0)
        self.assertGreater(bl.z("cyd_still", 200), 4.0)
        self.assertLessEqual(bl.z("cyd_still", 1e9), cf.Z_CLIP[1])
        self.assertIsNone(bl.z("cyd_still", None))
        self.assertIsNone(bl.z("unknown_name", 5))

    def test_scale_floor(self):
        bl = cf.Baseline()
        t = self.feed(bl, 500, lambda r, i: {"cyd_still": 20.0})
        bl.refresh(t)
        self.assertGreaterEqual(bl.ref["cyd_still"]["scale"], cf.SCALE_FLOOR)

    def test_follows_drift(self):
        """After the level doubles for longer than the window, the same raw value is no longer 'high'."""
        bl = cf.Baseline(window_s=3600)
        t = self.feed(bl, 400, lambda r, i: {"pcdesk_still": max(0.0, r.gauss(20, 2))})
        bl.refresh(t)
        z_before = bl.z("pcdesk_still", 40)
        t = self.feed(bl, 700, lambda r, i: {"pcdesk_still": max(0.0, r.gauss(40, 4))}, t0=t)
        bl.refresh(t)
        self.assertGreater(z_before, 3.0)
        self.assertLess(bl.z("pcdesk_still", 40), 2.0)

    def test_reset_and_none(self):
        bl = cf.Baseline()
        t = self.feed(bl, 500, lambda r, i: {"cyd_still": 20 + r.random()})
        bl.refresh(t)
        self.assertIn("cyd_still", bl.ref)
        bl.add(t, "cyd_still", None)   # ignored
        bl.reset("cyd_still")
        self.assertNotIn("cyd_still", bl.ref)
        self.assertNotIn("cyd_still", bl.pts)

    def test_dump_load_roundtrip(self):
        bl = cf.Baseline()
        t = self.feed(bl, 500, lambda r, i: {"cyd_still": 20 + r.random() * 3})
        bl.refresh(t)
        d = json.loads(json.dumps(bl.dump()))
        b2 = cf.Baseline()
        b2.load(d)
        self.assertAlmostEqual(b2.ref["cyd_still"]["loc"], bl.ref["cyd_still"]["loc"], places=2)
        self.assertAlmostEqual(b2.z("cyd_still", 40), bl.z("cyd_still", 40), places=1)

    def test_info(self):
        bl = cf.Baseline()
        t = self.feed(bl, 500, lambda r, i: {"cyd_still": 20 + r.random() * 3})
        bl.refresh(t)
        inf = bl.info(t)["cyd_still"]
        self.assertIn("span_h", inf)
        self.assertIsNotNone(inf["quiet_age_min"])


class ModelTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(3)
        self.samples = []
        cent = {"kitchen": {"cyd_still": 6, "pcdesk_still": 0, "workdesk_still": 0},
                "pc_desk": {"cyd_still": 0, "pcdesk_still": 7, "workdesk_still": 1},
                "bed": {"cyd_still": 0, "pcdesk_still": 0, "workdesk_still": 0}}
        for zone, c in cent.items():
            for _ in range(30):
                self.samples.append((zone, {k: v + rng.gauss(0, 0.5) for k, v in c.items()}))
        self.cent = cent

    def test_classifies_centroids(self):
        m = cf.ZoneModel().fit(self.samples)
        for zone, c in self.cent.items():
            d, used = m.dist(dict(c))
            self.assertEqual(cf.decide(d)[0], zone)
            self.assertEqual(len(used), 3)

    def test_missing_features_still_work(self):
        m = cf.ZoneModel().fit(self.samples)
        d, used = m.dist({"pcdesk_still": 7.0})
        self.assertEqual(used, ["pcdesk_still"])
        self.assertEqual(cf.decide(d)[0], "pc_desk")

    def test_sd_floor_and_weights(self):
        m = cf.ZoneModel(sd_floor=1.0).fit([("a", {"x": 1.0}), ("a", {"x": 1.0}), ("b", {"x": 3.0})])
        self.assertEqual(m.sd["x"], 1.0)
        mw = cf.ZoneModel(weights={"y": 0.0}).fit([("a", {"x": 0.0, "y": 0.0}), ("b", {"x": 5.0, "y": 0.0})])
        d, _ = mw.dist({"x": 0.0, "y": 100.0})
        self.assertEqual(cf.decide(d)[0], "a")   # zero weight: y is ignored

    def test_decide(self):
        self.assertEqual(cf.decide({}), (None, 0.0))
        self.assertEqual(cf.decide({"a": 1.0}), ("a", 0.0))
        z, c = cf.decide({"a": 1.0, "b": 2.0})
        self.assertEqual(z, "a")
        self.assertAlmostEqual(c, 0.5)
        self.assertEqual(cf.decide({"a": 1.0, "b": 1.0})[1], 0.0)


class WeakLabelTests(unittest.TestCase):
    base = dict(home=True, pc_online=True, pc_locked=True, pc_idle=5000.0, radar=False, radar_cm=None, wd_light=False,
                wd_age_s=None, kitchen_light=False, hour=14)

    def lab(self, **kw):
        return cf.weak_label(dict(self.base, **kw))

    def test_rules(self):
        self.assertEqual(self.lab(home=False), "away")
        self.assertEqual(self.lab(pc_locked=False, pc_idle=10.0), "pc_desk")
        self.assertIsNone(self.lab(pc_locked=False, pc_idle=600.0))
        self.assertEqual(self.lab(radar=True, radar_cm=100.0), "kitchen")
        self.assertIsNone(self.lab(radar=True, radar_cm=400.0))
        self.assertEqual(self.lab(wd_light=True, wd_age_s=300.0), "work_desk")
        self.assertIsNone(self.lab(wd_light=True, wd_age_s=3000.0))
        self.assertEqual(self.lab(hour=2), "bed")
        self.assertIsNone(self.lab(hour=14))
        self.assertIsNone(self.lab(hour=2, kitchen_light=True))
        self.assertIsNone(self.lab(hour=2, radar=True, radar_cm=400.0))

    def test_unavailable_pc(self):
        """PC offline ('unavailable' strings in the context): night -> bed."""
        p = {"home": "on", "pc_online": "off", "pc_idle": "unavailable", "pc_locked": "unavailable", "radar": "off",
             "radar_cm": "111.0", "wd_light": "off", "wd_light_ts": 1.0, "kitchen_light": "off"}
        import time
        t = time.mktime(time.strptime("2026-10-30 02:00:00", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(cf.weak_from_ctx(p, t), "bed")
        t = time.mktime(time.strptime("2026-10-30 14:00:00", "%Y-%m-%d %H:%M:%S"))
        self.assertIsNone(cf.weak_from_ctx(p, t))
        self.assertEqual(cf.weak_from_ctx(dict(p, home="off"), t), "away")

    def test_from_row(self):
        r = {"home": "on", "pc_online": "on", "pc_locked": "off", "pc_idle": "3", "radar": "off", "radar_cm": "",
             "wd_light": "off", "wd_light_age_s": "", "kitchen_light": "off"}
        self.assertEqual(cf.weak_from_row(r, 12), "pc_desk")


# ---------------------------------------------------------------- Engine
LEVEL = {  # per-zone mean of the 'still' per-mille on each node (quiet = 20)
    "quiet": {"cyd": 20, "kitchen": 20, "pcdesk": 20, "workdesk": 20},
    "kitchen": {"cyd": 60, "kitchen": 120, "pcdesk": 20, "workdesk": 20},
    "pc_desk": {"cyd": 20, "kitchen": 20, "pcdesk": 130, "workdesk": 45},
    "work_desk": {"cyd": 20, "kitchen": 20, "pcdesk": 45, "workdesk": 130},
}


class Sim:
    def __init__(self, eng):
        self.eng, self.t, self.rng = eng, T0, random.Random(7)
        self.up = {n: 1000.0 for n in cf.NODES}

    def run(self, seconds, scene="quiet", ctx=None):
        out = None
        end = self.t + seconds
        while self.t < end:
            self.t += 1.0
            for n in cf.NODES:
                self.up[n] += 1
            if int(self.t - T0) % 3 == 0:
                for n in cf.NODES:
                    lvl = LEVEL[scene][n]
                    self.eng.on_csi(n, {"ok": 1, "motion": max(0, self.rng.gauss(lvl, lvl * 0.1)),
                                        "still": max(0, self.rng.gauss(lvl, lvl * 0.1)), "up": self.up[n]}, self.t)
            if ctx is not None and int(self.t - T0) % 10 == 0:
                self.eng.on_ctx(ctx, self.t)
            if int(self.t - T0) % 10 == 0:
                out = self.eng.tick(self.t)
        return out


def mkeng(tmp, use_seed=False, label_db=None):
    return svc.Engine(state_dir=tmp, label_db=label_db or os.path.join(tmp, "none.db"), use_seed=use_seed)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        svc.ZONELOG_DIR = os.path.join(self.tmp, "no-zone-log")   # keep bootstrap away from the real log

    def tearDown(self):
        self._td.cleanup()

    def calibrate(self, sim, zone, scene, seconds=70):
        sim.eng.on_cal_status({"state": "recording", "zone": zone}, sim.t)
        sim.run(seconds, scene)
        sim.eng.on_cal_status({"state": "done", "zone": zone}, sim.t)

    def test_unknown_before_baseline_and_json_safe(self):
        eng = mkeng(self.tmp)
        sim = Sim(eng)
        state, agree, row = sim.run(60)
        self.assertEqual(state["zone"], "unknown")
        json.dumps(state)
        json.dumps(agree)
        self.assertEqual(len(row), len(svc.feat_head()))

    def test_calibrate_then_classify(self):
        eng = mkeng(self.tmp)
        sim = Sim(eng)
        sim.run(3900)   # > MIN_N windows of quiet -> baselines exist
        for zone, scene in (("kitchen", "kitchen"), ("pc_desk", "pc_desk"), ("work_desk", "work_desk"), ("bed", "quiet")):
            self.calibrate(sim, zone, scene)
            sim.run(60, "quiet")
        self.assertEqual({s["zone"] for s in eng.samples}, {"kitchen", "pc_desk", "work_desk", "bed"})
        self.assertTrue(all(s["src"] == "cal" for s in eng.samples))
        for scene, want in (("kitchen", "kitchen"), ("pc_desk", "pc_desk"), ("work_desk", "work_desk"), ("quiet", "bed")):
            state, _, _ = sim.run(120, scene)
            self.assertEqual(state["zone"], want, (scene, state["scores"]))
            self.assertGreater(state["confidence"], 0.2)
            self.assertGreaterEqual(len(state["used_nodes"]), 3)
        self.assertEqual(state["baseline_nodes"], sorted(cf.NODES))
        self.assertIsNotNone(state["baseline_quiet_age_min"])

    def test_cal_aborted_keeps_old_samples(self):
        eng = mkeng(self.tmp)
        sim = Sim(eng)
        sim.run(3900)
        self.calibrate(sim, "kitchen", "kitchen")
        n = len(eng.samples)
        eng.on_cal_status({"state": "recording", "zone": "kitchen"}, sim.t)
        sim.run(30, "kitchen")
        eng.on_cal_status({"state": "idle", "zone": None}, sim.t)
        self.assertEqual(len(eng.samples), n)
        eng.on_cal_cmd({"action": "clear", "zone": "kitchen"})
        self.assertEqual(len(eng.samples), 0)

    def test_persistence_roundtrip(self):
        eng = mkeng(self.tmp)
        sim = Sim(eng)
        sim.run(3900)
        self.calibrate(sim, "kitchen", "kitchen")
        eng.save(sim.t)
        e2 = mkeng(self.tmp)
        self.assertEqual(len(e2.samples), len(eng.samples))
        self.assertEqual(sorted(e2.bl.ref), sorted(eng.bl.ref))
        self.assertIn("kitchen", e2.model.mu)
        self.assertEqual(len(e2.ring), len(eng.ring))

    def test_node_reboot_resets_baseline(self):
        eng = mkeng(self.tmp)
        sim = Sim(eng)
        sim.run(3900)
        self.assertIn("cyd_still", eng.bl.ref)
        sim.up["cyd"] = 5.0
        sim.run(10)
        self.assertNotIn("cyd_still", eng.bl.ref)
        self.assertIn("kitchen_still", eng.bl.ref)
        self.assertIn("cyd", eng.reboots)
        state, _, _ = sim.run(10)
        self.assertNotIn("cyd", state["baseline_nodes"])

    def test_label_ingest_and_undo(self):
        db = os.path.join(self.tmp, "labels.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE labels(id INTEGER PRIMARY KEY, ts INTEGER, zone TEXT, window_s INTEGER)")
        c.execute("CREATE TABLE samples(label_id INTEGER, node TEXT, phone_avg REAL, phone_n INTEGER, watch_avg REAL, "
                  "watch_n INTEGER, mov_best REAL, msgs INTEGER, csi_motion REAL, csi_still REAL, csi_n INTEGER)")
        c.execute("INSERT INTO labels VALUES(1, ?, 'kitchen', 15)", (int(T0 + 4000),))
        for node, m, s in (("cyd", 60, 60), ("kitchen", 120, 120), ("pcdesk", 20, -1), ("workdesk", 20, 20)):
            c.execute("INSERT INTO samples VALUES(1, ?, 0, 0, 0, 0, 0, 0, ?, ?, 5)", (node, m, s))
        c.execute("INSERT INTO labels VALUES(2, ?, 'attic', 15)", (int(T0 + 4000),))   # unknown zone
        c.commit()
        c.close()
        eng = mkeng(self.tmp, label_db=db)
        sim = Sim(eng)
        sim.run(3900, ctx={"zone": "work_desk", "fp": "bed", "home": "on"})
        self.assertTrue(eng.ingest_label(1, sim.t))
        self.assertFalse(eng.ingest_label(2, sim.t))
        self.assertFalse(eng.ingest_label(99, sim.t))
        self.assertEqual([s["src"] for s in eng.samples], ["label"])
        self.assertEqual(eng.label_evals[0]["live"], "work_desk")
        self.assertEqual(eng.label_evals[0]["fp"], "bed")
        c = sqlite3.connect(db)
        c.execute("DELETE FROM labels WHERE id=1")
        c.commit()
        c.close()
        eng.sync_labels()
        self.assertEqual(eng.samples, [])
        self.assertEqual(eng.label_evals, [])

    def test_seed_stands_in_until_min_real(self):
        eng = mkeng(self.tmp, use_seed=True)
        eng.seed = [{"zone": "kitchen", "z": {"cyd_still": 5.0, "kitchen_still": 5.0, "pcdesk_still": 0.0}, "ts": 1, "src": "seed"},
                    {"zone": "bed", "z": {"cyd_still": 0.0, "kitchen_still": 0.0, "pcdesk_still": 0.0}, "ts": 1, "src": "seed"}]
        eng.refit()
        self.assertEqual(eng.train_info["kitchen"]["src"], "seed")
        eng.samples = [{"zone": "kitchen", "z": {"cyd_still": 6.0}, "ts": i, "src": "cal"} for i in range(svc.MIN_REAL)]
        eng.refit()
        self.assertEqual(eng.train_info["kitchen"]["src"], "cal+label")
        self.assertEqual(eng.train_info["bed"]["src"], "seed")

    def test_agreement_counts(self):
        eng = mkeng(self.tmp)
        now = T0 + 100000
        for i in range(100):
            eng.ring.append((now - i * 10, "kitchen", "kitchen", "bed", "kitchen"))
        for i in range(100, 200):
            eng.ring.append((now - i * 10, "bed", "bed", "bed", "bed"))
        a = eng.agreement(now)
        self.assertEqual(a["weak_acc_1h"], 1.0)
        self.assertEqual(a["vs_live_1h"], 1.0)
        self.assertEqual(a["ble_fp_weak_acc_1h"], 0.5)
        self.assertEqual(a["agree_pct"], 100.0)
        eng.ring.clear()
        self.assertIsNone(eng.agreement(now)["agree_pct"])

    def test_append_feat_creates_header_once(self):
        eng = mkeng(self.tmp)
        sim = Sim(eng)
        _, _, row = sim.run(20)
        import time
        svc.append_feat(self.tmp, row, time.time())
        svc.append_feat(self.tmp, row, time.time())
        files = [f for f in os.listdir(self.tmp) if f.startswith("feat-")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.tmp, files[0])) as f:
            self.assertEqual(len(f.read().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()


class ZoneLogAndScoreTests(unittest.TestCase):
    def test_zone_log_migration_and_row(self):
        import importlib.util
        import csv as _csv
        here = os.path.dirname(os.path.abspath(__file__))
        zp = next(p for p in (os.path.join(here, "new", "zone-log", "zone_log.py"), os.path.join(here, "..", "zone-log", "zone_log.py"),
                           os.path.join(here, "..", "zone_log.py"))
                  if os.path.exists(p))
        spec = importlib.util.spec_from_file_location("zone_log_new", zp)
        zl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(zl)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "zone-v3-x.csv")
            old = zl.HEAD[:-2]
            with open(p, "w", newline="") as f:
                w = _csv.writer(f)
                w.writerow(old)
                w.writerow(["2026-10-30T10:00:00"] + ["a"] * (len(old) - 1))
            self.assertTrue(zl.migrate(p))
            rows = list(_csv.reader(open(p)))
            self.assertEqual(rows[0], zl.HEAD)
            self.assertEqual(len(rows[1]), len(zl.HEAD))
            self.assertEqual(rows[1][-2:], ["", ""])
            self.assertFalse(zl.migrate(p))                      # idempotent
            self.assertFalse(zl.migrate(os.path.join(d, "missing.csv")))
            st = zl.State()
            st.ctx_t = T0
            row = st.make_row(T0)
            self.assertEqual(len(row), len(zl.HEAD))
            self.assertEqual(row[-2:], ["", ""])
            st.add_csi_state({"zone": "bed", "confidence": 0.4, "ts": T0 - 5})
            self.assertEqual(st.make_row(T0)[-2:], ["bed", 0.4])
            self.assertEqual(st.make_row(T0 + 500)[-2:], ["", ""])   # stale

    def test_csi_score(self):
        import csi_score as cs
        rows = []
        base = {"home": "on", "pc_online": "on", "pc_locked": "off", "pc_idle": "3", "radar": "off", "radar_cm": "",
                "wd_light": "off", "wd_light_age_s": "", "kitchen_light": "off", "_day": "2026-10-30", "_hour": 12,
                "zone": "pc_desk", "fp_zone": "bed"}
        rows += [dict(base, csi_zone="pc_desk", csi_conf="0.5") for _ in range(8)]
        rows += [dict(base, csi_zone="bed", csi_conf="0.1") for _ in range(2)]
        rows += [dict(base, csi_zone="", csi_conf="") for _ in range(5)]      # skipped
        sc = cs.score(rows)
        n, acc, bal, _, _ = __import__("csi_replay").metrics(sc["ALL"]["csi_vs_weak"])
        self.assertEqual((n, acc), (10, 0.8))
        self.assertEqual(__import__("csi_replay").metrics(sc["ALL"]["ble_fp_vs_weak_same_rows"])[1], 0.0)
        sc = cs.score(rows, min_conf=0.3)
        self.assertEqual(__import__("csi_replay").metrics(sc["ALL"]["csi_vs_weak"])[0], 8)
