"""csi-lead acceptance tests (offline A1-A6, A9, A11) for ha/room_presence.yaml.

test-from-plan, 2026-10-30. Run (see the brief):

    RP_CONFIG=ha/room_presence.yaml RP_CSI_T=0.15 RP_CSI_T_ENTITY=input_number.rp_csi_min_conf \
        python -m unittest ha.test_csi_lead -v

Candidate = RP_CONFIG. Baseline MASTER = ``git show csi-shadow:ha/room_presence.yaml``
(loaded by :func:`master_cfg`, UTF-8).

Notes on the handoff overrides (the final constants win over plan rows):
  * O4: the candidate template emits a closed reason key set that has no
    ``no_fingerprint`` (see the enumeration at the top of the reason entity in the
    yaml). The master free text ``沒有指紋，在家`` therefore maps to the candidate key
    ``ble_fallback`` (the plan's original mapping), not ``no_fingerprint``.
    ``no_fingerprint`` stays in ALLOWED_REASONS (harmless, never emitted).
  * O5/O7: RULES is the six real rule keys; night_bed is special-cased (A5.1a/b);
    the sticky-CSI TODO is neither in the constants nor exercised here.
"""
import itertools
import json
import os
import re
import subprocess
import unittest
from datetime import datetime, timedelta, timezone

import jinja2
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                 # parent of the test file's directory
MASTER_REF = "csi-shadow"
MASTER_PATH = "ha/room_presence.yaml"

if "RP_CONFIG" not in os.environ:
    raise SystemExit("set RP_CONFIG")
if "RP_CSI_T" not in os.environ:
    raise SystemExit("set RP_CSI_T")

T = float(os.environ["RP_CSI_T"])
EPS = 0.001 if T <= 1.0 else 0.1
T_HI = T + 10 * EPS
T_LO = T - EPS

NOW = datetime(2026, 10, 30, 10, 0, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(seconds=600)
EPOCH = NOW.timestamp()                      # exact integer float (0 microseconds, UTC)

# ---------------------------------------------------------------------------
# Final constants (reports/rp-phase1-20261030.md, per the handoff O1-O9)
# ---------------------------------------------------------------------------
RULES = ["pc_active", "away", "pc_hold", "kitchen_radar", "bathroom_radar", "phone_unheard"]
RULE_NAME = {k: k for k in RULES}
DWELL_NAME = "min_dwell"
CSI_SLOT = 6
CSI_SOFT = True
CSI_KITCHEN_NEEDS_EVIDENCE = False
BATHROOM_RADAR_ENTITY = "binary_sensor.rp_bathroom_radar_hint"
BATHROOM_ZONE = "bathroom"
BATHROOM_RULE = "bathroom_radar"
ALLOWED_REASONS = {"csi", "ble_fallback", "night_bed", "no_fingerprint"} | set(RULE_NAME.values())

# Master free text -> canonical key (first prefix that matches; substring fallback).
MASTER_KEYS = [
    ("PC 使用中", "pc_active"),
    ("不在家", "away"),
    ("PC 已解鎖", "pc_hold"),
    ("廚房雷達", "kitchen_radar"),
    ("所有節點都聽不到手機", "phone_unheard"),
    ("夜間床規則", "night_bed"),
    ("沒有指紋", "ble_fallback"),
    ("指紋", "ble_fallback"),
]


def MASTER_KEY(text):
    text = (text or "").strip()
    for pre, key in MASTER_KEYS:
        if text.startswith(pre):
            return key
    for pre, key in MASTER_KEYS:
        if pre in text:
            return key
    return None


def EXPECT_REASON(key):
    # RULE_NAME maps every rule key to itself, so the identity covers the 6 rules,
    # csi, night_bed, ble_fallback and no_fingerprint alike.
    return key if key in ALLOWED_REASONS else RULE_NAME[key]


# ---------------------------------------------------------------------------
# Jinja/HA shim
# ---------------------------------------------------------------------------
MISSING = object()
ENV = jinja2.Environment(undefined=jinja2.StrictUndefined,
                         extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])
_TPL = {}
_CURRENT = {"w": None}


def shim_float(v, default=MISSING):
    try:
        return float(v)
    except (TypeError, ValueError):
        if default is MISSING:
            raise ValueError("float() got a non-numeric value without a default: %r" % (v,))
        return default


def shim_int(v, default=MISSING):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        if default is MISSING:
            raise ValueError("int() got a non-numeric value without a default: %r" % (v,))
        return default


def _is_number(v):
    try:
        return shim_float(v) == shim_float(v) and abs(shim_float(v)) != float("inf")
    except (TypeError, ValueError):
        return False


def _as_timestamp(v, default=MISSING):
    try:
        if isinstance(v, datetime):
            return v.timestamp()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        return datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        if default is MISSING:
            raise ValueError("as_timestamp() with no default: %r" % (v,))
        return default


def _as_datetime(v, default=MISSING):
    try:
        if isinstance(v, datetime):
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        return datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        if default is MISSING:
            raise ValueError("as_datetime() with no default: %r" % (v,))
        return default


def _iif(condition, a=True, b=False, n=None):
    if condition is None:
        return n
    return a if condition else b


ENV.filters["float"] = shim_float
ENV.filters["int"] = shim_int
ENV.tests["is_number"] = _is_number
ENV.filters["is_number"] = _is_number
ENV.filters["states"] = lambda eid: _stats_get(eid)
ENV.filters["as_timestamp"] = _as_timestamp
ENV.filters["as_datetime"] = _as_datetime
ENV.filters["tojson"] = json.dumps


def _stats_get(eid):
    w = _CURRENT["w"]
    return w._get(eid) if w is not None else None


class St:
    """A minimal HA state object."""

    def __init__(self, state, attributes=None, last_changed=None, last_updated=None):
        self.state = state
        self.attributes = attributes if attributes is not None else {}
        self.last_changed = last_changed if last_changed is not None else OLD
        self.last_updated = last_updated if last_updated is not None else self.last_changed
        self.entity_id = None

    @property
    def domain(self):
        return self.entity_id.split(".", 1)[0] if self.entity_id else None

    @property
    def object_id(self):
        return self.entity_id.split(".", 1)[1] if self.entity_id else None

    @property
    def name(self):
        return self.attributes.get("friendly_name")


class _Dom:
    def __init__(self, w, dom):
        self.w, self.dom = w, dom

    def __getattr__(self, obj):
        return self.w._get(self.dom + "." + obj)


class _StatesObj:
    """``states(eid)`` -> state string; ``states.sensor.x`` / ``states['sensor.x']`` -> St|None."""

    def __init__(self, w):
        self.w = w

    def __call__(self, eid):
        o = self.w._get(eid)
        return o.state if o is not None else "unknown"

    def __getitem__(self, eid):
        return self.w._get(eid)

    def __getattr__(self, dom):
        return _Dom(self.w, dom)


class World:
    def __init__(self):
        self.s = {}

    def set(self, eid, state, attrs=None, lc=None, lu=None):
        st = St(str(state), attrs, lc, lu)
        st.entity_id = eid
        self.s[eid] = st
        return st

    def _get(self, eid):
        return self.s.get(eid)

    def states_fn(self, eid):
        o = self.s.get(eid)
        return o.state if o is not None else "unknown"

    def is_state(self, eid, v):
        o = self.s.get(eid)
        if o is None:
            return False
        return o.state in ([v] if isinstance(v, str) else list(v))

    def state_attr(self, eid, a):
        o = self.s.get(eid)
        return o.attributes.get(a) if o is not None else None

    def is_state_attr(self, eid, a, v):
        o = self.s.get(eid)
        if o is None or a not in o.attributes:
            return False
        return o.attributes.get(a) in ([v] if isinstance(v, str) else list(v))

    def has_value(self, eid):
        o = self.s.get(eid)
        return o is not None and o.state not in ("unknown", "unavailable")

    def render(self, text, **ctx):
        t = _TPL.get(text)
        if t is None:
            t = _TPL[text] = ENV.from_string(text)
        _CURRENT["w"] = self
        base = {
            "states": _StatesObj(self),
            "is_state": self.is_state,
            "state_attr": self.state_attr,
            "is_state_attr": self.is_state_attr,
            "has_value": self.has_value,
            "now": lambda: NOW,
            "utcnow": lambda: NOW,
            "float": shim_float,
            "int": shim_int,
            "is_number": _is_number,
            "iif": _iif,
            "as_timestamp": _as_timestamp,
            "as_datetime": _as_datetime,
            "tojson": json.dumps,
        }
        base.update(ctx)
        return t.render(**base).strip()


def _truthy(text):
    return str(text).strip().lower() in ("true", "on", "1", "yes")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def load_yaml_text(text):
    return yaml.safe_load(text)


def load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


CAND = load_cfg(os.environ["RP_CONFIG"])
_MASTER = {"cfg": None, "err": None}


def master_cfg():
    if _MASTER["cfg"] is not None:
        return _MASTER["cfg"]
    if _MASTER["err"] is not None:
        raise _MASTER["err"]
    try:
        r = subprocess.run(["git", "-C", REPO, "show", MASTER_REF + ":" + MASTER_PATH],
                           capture_output=True, check=True)
        _MASTER["cfg"] = load_yaml_text(r.stdout.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - surfaced to the test as a clear error
        _MASTER["err"] = RuntimeError(
            "MASTER baseline unavailable (git -C %s show %s:%s): %s"
            % (REPO, MASTER_REF, MASTER_PATH, exc))
        raise _MASTER["err"]
    return _MASTER["cfg"]


def template_entities(cfg):
    out = {}
    for blk in cfg.get("template", []) or []:
        for kind in ("sensor", "binary_sensor"):
            for e in blk.get(kind, []) or []:
                uid = e.get("unique_id")
                if uid is None:
                    continue
                dflt = e.get("default_entity_id")
                if dflt:
                    oid = str(dflt).split(".", 1)[-1]
                    eid = "%s.%s" % (kind, oid)
                else:
                    eid = "%s.%s" % (kind, _slug(e.get("name", uid)))
                out[uid] = {"ent": e, "domain": kind, "eid": eid}
    return out


CAND_ENTS = template_entities(CAND)
_NEW_UIDS = {"v": None}


def new_uids():
    """Candidate uids not present in MASTER (the derived entities to render in step 2)."""
    if _NEW_UIDS["v"] is not None:
        return _NEW_UIDS["v"]
    try:
        mus = set(template_entities(master_cfg()))
    except RuntimeError:
        mus = set()
    _NEW_UIDS["v"] = [u for u in CAND_ENTS if u not in mus]
    return _NEW_UIDS["v"]


def helpers(cfg):
    out = {}
    for domain in ("input_boolean", "input_number", "input_select", "input_text"):
        for key, spec in (cfg.get(domain, {}) or {}).items():
            if isinstance(spec, dict) and "initial" in spec:
                ini = spec["initial"]
                if isinstance(ini, bool):
                    ini = "on" if ini else "off"
                out["%s.%s" % (domain, key)] = str(ini)
            else:
                out["%s.%s" % (domain, key)] = "unknown"
    return out


def _put(w, eid, val):
    if val is None:
        return
    state = val[0]
    attrs = val[1] if len(val) > 1 else {}
    lc = val[2] if len(val) > 2 else OLD
    lu = val[3] if len(val) > 3 else None
    w.set(eid, state, attrs, lc, lu)


# ---------------------------------------------------------------------------
# Render sequence (build exactly the plan's run(); fx wins over helpers)
# ---------------------------------------------------------------------------
def build_world(cfg, fx):
    w = World()
    for eid, state in helpers(cfg).items():
        if eid in fx:
            continue
        w.set(eid, state)
    for eid, val in fx.items():
        _put(w, eid, val)
    te = os.environ.get("RP_CSI_T_ENTITY")
    if te:
        w.set(te, str(T))
    return w


def render_entity(cfg, uid, fx, this):
    w = build_world(cfg, fx)
    return w.render(template_entities(cfg)[uid]["ent"]["state"], this=this)


def run(cfg, fx, this_zone, new=()):
    ents = template_entities(cfg)
    w = build_world(cfg, fx)
    # step 2: new derived entities (two passes), fixture values win. A derived entity
    # this fixture does not provide may need inputs the shim has no business knowing
    # (statistics, trackers); skip it rather than fail the cascade render.
    for _ in range(2):
        for uid in new:
            info = ents.get(uid)
            if info is None or info["eid"] in fx:
                continue
            try:
                raw = w.render(info["ent"]["state"], this=St("unknown", {}, OLD, OLD))
            except jinja2.UndefinedError:
                continue
            st = raw.lower()
            if info["domain"] == "binary_sensor":
                st = "on" if st in ("true", "on", "1", "yes") else "off"
            w.set(info["eid"], st, lc=NOW, lu=NOW)
    # step 3: yui_room_zone
    zent = ents["yui_room_zone"]
    zone = w.render(zent["ent"]["state"], this=this_zone)
    w.set("sensor.yui_room_zone", zone)
    if zent["ent"].get("availability"):
        if not _truthy(w.render(zent["ent"]["availability"])):
            raise AssertionError("yui_room_zone availability false")
    # step 4: rp_zone_reason
    rent = ents["rp_zone_reason"]
    reason = w.render(rent["ent"]["state"], this=St("unknown", {}, OLD, OLD))
    w.set("sensor.rp_zone_reason", reason)
    if rent["ent"].get("availability"):
        if not _truthy(w.render(rent["ent"]["availability"])):
            raise AssertionError("rp_zone_reason availability false")
    return zone, reason


def run_master(fx, this_zone):
    return run(master_cfg(), fx, this_zone, new=())


_CASE_CACHE = {}


def case(which, key, fx, this_zone):
    """Cached (zone, reason) for (candidate|master, key). key None = no caching."""
    if key is not None:
        hit = _CASE_CACHE.get((which, key))
        if hit is not None:
            return hit
    res = run(CAND, fx, this_zone, new=new_uids()) if which == "cand" else run_master(fx, this_zone)
    if key is not None:
        _CASE_CACHE[(which, key)] = res
    return res


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
FP = {
    "nodes": ["pcdesk", "workdesk", "cyd", "kitchen"],
    "feat": {},
    "zones": {
        "kitchen": {"pcdesk": -85, "workdesk": -80, "cyd": -82, "kitchen": -55},
        "work_desk": {"pcdesk": -66, "workdesk": -58, "cyd": -75, "kitchen": -82},
        "pc_desk": {"pcdesk": -52, "workdesk": -70, "cyd": -55, "kitchen": -88},
        "bed": {"pcdesk": -78, "workdesk": -75, "cyd": -70, "kitchen": -84},
    },
}


def base():
    return {
        "input_boolean.rp_csi_lead": ("on", {}),
        "binary_sensor.rp_pc_online": ("off", {}),
        "binary_sensor.rp_pc_locked": ("on", {}),
        "sensor.rp_pc_idle": ("9999", {}),
        "binary_sensor.yui_home": ("on", {}),
        "binary_sensor.rp_night_bed": ("off", {}),
        "binary_sensor.rp_kitchen_radar_hint": ("off", {}),
        "binary_sensor.rp_kitchen_radar_recent": ("off", {}),
        "binary_sensor.rp_kitchen_evidence": ("off", {}),
        "binary_sensor.kitchenradar_presence": ("off", {}),
        "sensor.kitchenradar_detection_distance": ("9999", {}),
        "sensor.rp_power_max120": ("0", {}),
        "input_datetime.rp_kitchen_light_op": ("2026-10-30 08:00:00", {"timestamp": EPOCH - 7200}),
        "sensor.rp_pcdesk_signal": ("-60", {}),
        "sensor.rp_workdesk_signal": ("-64", {}),
        "sensor.rp_nlpi_signal": ("-127", {}),
        "sensor.rp_kitchen_signal": ("-80", {}),
        "sensor.rp_cyd_signal": ("-70", {}),
        "input_number.rp_hysteresis_db": ("6", {}),
        "input_number.rp_min_rssi": ("-90", {}),
        "input_number.rp_fp_hysteresis": ("2.5", {}),
        "input_number.rp_feat_weight_db": ("0", {}),
        "input_number.rp_kitchen_max_cm": ("250", {}),
        "binary_sensor.rp_pcdesk_ble_stale": ("off", {}),
        "binary_sensor.rp_workdesk_ble_stale": ("off", {}),
        "binary_sensor.rp_cyd_ble_stale": ("off", {}),
        "binary_sensor.rp_kitchen_ble_stale": ("off", {}),
        "binary_sensor.rp_nlpi_ble_stale": ("off", {}),
        "sensor.rp_fp_zone": ("work_desk", {}),
        "sensor.rp_fingerprints": ("4", FP),
    }


def merge(*dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


def csi_ok(state="pc_desk", confidence=T_HI, ts=None):
    if ts is None:
        ts = EPOCH - 5
    return {"sensor.rp_csi_zone": (state, {"confidence": confidence, "ts": ts}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5))}


def this_zone(state, age_s):
    lc = NOW - timedelta(seconds=age_s)
    return St(state, {}, lc, lc)


# BAD variants: name -> fx override for sensor.rp_csi_zone (None = entity absent)
def bad_variants():
    out = {
        "unavailable": ("unavailable", {"confidence": T_HI, "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "unknown_zone": ("unknown", {"confidence": T_HI, "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "absent": None,
        "stale_30_001": ("pc_desk", {"confidence": T_HI, "ts": EPOCH - 30.001}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "stale_31": ("pc_desk", {"confidence": T_HI, "ts": EPOCH - 31}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "stale_600": ("pc_desk", {"confidence": T_HI, "ts": EPOCH - 600}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_missing": ("pc_desk", {"ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_unknown": ("pc_desk", {"confidence": "unknown", "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_text": ("pc_desk", {"confidence": "abc", "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_none": ("pc_desk", {"confidence": None, "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_empty": ("pc_desk", {"confidence": "", "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_nan": ("pc_desk", {"confidence": "nan", "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_below": ("pc_desk", {"confidence": T_LO, "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "conf_negative": ("pc_desk", {"confidence": -1, "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
        "home_state": ("home", {"confidence": T_HI, "ts": EPOCH - 5}, NOW - timedelta(seconds=120), NOW - timedelta(seconds=5)),
    }
    return {"sensor.rp_csi_zone": out}


# ---------------------------------------------------------------------------
# MATRIX (4 x 2 x 3 x 2 x 2 x 5 x 5 x 2 = 4800 fixtures)
# ---------------------------------------------------------------------------
PC = {
    "active": {"binary_sensor.rp_pc_online": ("on", {}), "binary_sensor.rp_pc_locked": ("off", {}), "sensor.rp_pc_idle": ("10", {})},
    "hold": {"binary_sensor.rp_pc_online": ("on", {}), "binary_sensor.rp_pc_locked": ("off", {}), "sensor.rp_pc_idle": ("120", {})},
    "locked": {"binary_sensor.rp_pc_online": ("on", {}), "binary_sensor.rp_pc_locked": ("on", {}), "sensor.rp_pc_idle": ("9999", {})},
    "off": {},
}
HOME = {"on": {"binary_sensor.yui_home": ("on", {})}, "off": {"binary_sensor.yui_home": ("off", {})}}
RADAR = {
    "none": {},
    "hint": {"binary_sensor.rp_kitchen_radar_hint": ("on", {}), "binary_sensor.rp_kitchen_radar_recent": ("on", {}),
             "binary_sensor.rp_kitchen_evidence": ("on", {}), "binary_sensor.kitchenradar_presence": ("on", {}),
             "sensor.kitchenradar_detection_distance": ("120", {})},
    "recent_power": {"binary_sensor.rp_kitchen_radar_recent": ("on", {}), "binary_sensor.rp_kitchen_evidence": ("on", {}),
                     "sensor.rp_power_max120": ("1500", {})},
}
SIG = {
    "normal": {},
    "deaf": {"sensor.rp_pcdesk_signal": ("-127", {}), "sensor.rp_workdesk_signal": ("-127", {}),
             "sensor.rp_nlpi_signal": ("-127", {}), "sensor.rp_kitchen_signal": ("-127", {}),
             "sensor.rp_cyd_signal": ("-127", {})},
}
NIGHT = {"off": {}, "on": {"binary_sensor.rp_night_bed": ("on", {})}}
FPZ = {z: {"sensor.rp_fp_zone": (z, {})} for z in ("work_desk", "pc_desk", "kitchen", "bed", "none")}
PREV = ("work_desk", "pc_desk", "kitchen", "home", "away")
AGES = (10, 600)


def matrix():
    out = []
    for pc, home, radar, sig, night, fp, prev, age in itertools.product(
            list(PC), list(HOME), list(RADAR), list(SIG), list(NIGHT), list(FPZ), PREV, AGES):
        fx = merge(base(), PC[pc], HOME[home], RADAR[radar], SIG[sig], NIGHT[night], FPZ[fp])
        out.append(((pc, home, radar, sig, night, fp, prev, age), fx))
    return out


MATRIX = matrix()
MATRIX_COUNT = 4800


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestConstants(unittest.TestCase):
    def test_constants_sane(self):
        self.assertGreaterEqual(CSI_SLOT, 2)   # away is always before CSI (A4)
        self.assertLessEqual(CSI_SLOT, 6)
        self.assertEqual(len(RULES), CSI_SLOT)


class TestA1(unittest.TestCase):
    def test_lead_boolean_declared_restored(self):
        # A1.1 (amended 2026-10-30, owner ask_mvv239n77ac1cd64f59d): no `initial`, so HA restores the
        # last state and an OFF kill switch survives a restart; turned on once at deploy.
        self.assertIn("rp_csi_lead", CAND["input_boolean"])
        for dom, key in (("input_boolean", "rp_csi_lead"), ("input_number", "rp_csi_min_conf"),
                         ("input_number", "rp_csi_stale_s")):
            self.assertNotIn("initial", CAND[dom][key] or {}, key)

    def test_lead_off_matches_master_matrix(self):
        # A1.2 / guard
        mismatches = []
        n = 0
        for key, fx in MATRIX:
            for variant in ("a", "b"):
                f = dict(fx)
                f["input_boolean.rp_csi_lead"] = ("off", {})
                if variant == "a":
                    f["sensor.rp_csi_zone"] = csi_ok(state="kitchen")["sensor.rp_csi_zone"]
                else:
                    f["sensor.rp_csi_zone"] = None
                this = this_zone(key[6], key[7])
                cz, _ = case("cand", ("12a", variant) + key, f, this)
                mz, _ = case("master", ("12a", variant) + key, f, this)
                n += 1
                if cz != mz and len(mismatches) < 20:
                    mismatches.append((variant, key, cz, mz))
        self.assertEqual(n, MATRIX_COUNT * 2)
        self.assertEqual(mismatches, [], "first mismatches (variant, fixture, cand, master): %r" % (mismatches,))

    def test_lead_off_reason_matches_master_rule(self):
        # A1.3
        bad = []
        n = 0
        for key, fx in MATRIX:
            for variant in ("a", "b"):
                f = dict(fx)
                f["input_boolean.rp_csi_lead"] = ("off", {})
                if variant == "a":
                    f["sensor.rp_csi_zone"] = csi_ok(state="kitchen")["sensor.rp_csi_zone"]
                else:
                    f["sensor.rp_csi_zone"] = None
                this = this_zone(key[6], key[7])
                cz, cr = case("cand", ("12a", variant) + key, f, this)
                mz, mr = case("master", ("12a", variant) + key, f, this)
                n += 1
                mkey = MASTER_KEY(mr)
                self.assertIsNotNone(mkey, "unmapped master reason %r (fixture %r)" % (mr, key))
                exp = EXPECT_REASON(mkey)
                ok = cr == exp and cr != "csi"
                dwell = (mz == key[6] and mz != cz and key[7] == 10)
                if not ok and not (dwell and cr in (exp, DWELL_NAME)):
                    if len(bad) < 20:
                        bad.append((variant, key, cr, exp, mr))
        self.assertEqual(n, MATRIX_COUNT * 2)
        self.assertEqual(bad, [], "(variant, fixture, cand_reason, expected, master_text): %r" % (bad,))

    def test_rp_fp_zone_unchanged(self):
        # A1.4 / guard
        sets = [(-52, -70, -55, -88), (-66, -58, -75, -82), (-85, -80, -82, -55),
                (-78, -75, -70, -84), (-127, -127, -127, -127)]
        nodes = ("pcdesk", "workdesk", "cyd", "kitchen")
        n = 0
        for sig in sets:
            for tstate in ("work_desk", "pc_desk", "none"):
                for lead in ("on", "off"):
                    fx = merge(base(), {
                        "sensor.rp_%s_signal" % nd: (str(v), {}) for nd, v in zip(nodes, sig)})
                    fx["input_boolean.rp_csi_lead"] = (lead, {})
                    this = St(tstate, {}, OLD, OLD)
                    cz = render_entity(CAND, "rp_fp_zone", fx, this)
                    mz = render_entity(master_cfg(), "rp_fp_zone", fx, this)
                    n += 1
                    self.assertEqual(cz, mz, "rp_fp_zone differs for %r %r %r" % (sig, tstate, lead))
        self.assertEqual(n, 30)

    def test_lead_missing_or_unknown_no_error(self):
        # A1.5
        for variant, val in (("absent", None), ("unknown", ("unknown", {})), ("unavailable", ("unavailable", {}))):
            with self.subTest(variant=variant):
                fx = merge(base(), csi_ok(), {"input_boolean.rp_csi_lead": val})
                zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
                self.assertIn(zone, {"kitchen", "work_desk", "pc_desk", "bed", "home", "away"})
                self.assertIn(reason, ALLOWED_REASONS)


class TestA2(unittest.TestCase):
    def test_interface_referenced(self):
        # A2.0
        text = CAND_ENTS["yui_room_zone"]["ent"]["state"]
        for uid in new_uids():
            text += "\n" + CAND_ENTS[uid]["ent"]["state"]
        for needle in ("sensor.rp_csi_zone", "confidence", "input_boolean.rp_csi_lead"):
            if needle not in text:
                self.fail("interface mismatch: plan assumes "
                          "state_attr('sensor.rp_csi_zone','confidence')")

    def test_csi_leads_each_zone(self):
        # A2.1 (O1: valid CSI zones only)
        for state in ("pc_desk", "work_desk", "bed"):
            with self.subTest(state=state):
                extra = {"sensor.rp_fp_zone": ("bed", {})} if state == "work_desk" else {}
                fx = merge(base(), csi_ok(state=state), extra)
                zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
                self.assertEqual(zone, state)
                self.assertEqual(reason, "csi")

    def test_age_exactly_30s_leads(self):
        # A2.2 (O2: age from attr ts)
        fx = merge(base(), csi_ok(state="pc_desk", ts=EPOCH - 30))
        zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
        self.assertEqual(zone, "pc_desk")
        self.assertEqual(reason, "csi")

    def test_confidence_equal_T_leads(self):
        # A2.3
        for conf in (T, str(T_HI)):
            with self.subTest(confidence=conf):
                fx = merge(base(), csi_ok(state="pc_desk", confidence=conf))
                zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
                self.assertEqual(zone, "pc_desk")
                self.assertEqual(reason, "csi")

    def test_last_updated_counts_not_last_changed(self):
        # A2.4 (O2: age from attr ts, not last_updated)
        fx = merge(base(), csi_ok(state="pc_desk"))
        fx["sensor.rp_csi_zone"] = ("pc_desk", {"confidence": T_HI, "ts": EPOCH - 5},
                                    NOW - timedelta(seconds=3600), NOW - timedelta(seconds=3600))
        zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
        self.assertEqual(zone, "pc_desk")
        self.assertEqual(reason, "csi")

    def test_csi_kitchen_evidence_flag(self):
        # A2.5
        fx = merge(base(), csi_ok(state="kitchen"))
        zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
        if CSI_KITCHEN_NEEDS_EVIDENCE is False:
            self.assertEqual(zone, "kitchen")
            self.assertEqual(reason, "csi")
        else:
            mz, _ = run_master(fx, this_zone("work_desk", 600))
            self.assertEqual(zone, mz)
            self.assertEqual(reason, "ble_fallback")


class TestA3(unittest.TestCase):
    def test_bad_csi_falls_back_base(self):
        # A3.1 (14 plan variants + O1 home_state)
        for name, csi_val in bad_variants()["sensor.rp_csi_zone"].items():
            with self.subTest(variant=name):
                fx = merge(base(), {"sensor.rp_csi_zone": csi_val})
                zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
                mz, _ = run_master(fx, this_zone("work_desk", 600))
                self.assertEqual(zone, mz, "zone must equal master for %s" % name)
                self.assertEqual(zone, "work_desk")
                self.assertEqual(reason, "ble_fallback")

    def test_bad_csi_falls_back_matrix(self):
        # A3.2: 15 BAD variants x reduced matrix (pc off, home on, radar none, sig normal,
        #        night off, fp 5, attrs present/absent, prev 5 x age 2) = 15 x 100
        bad = bad_variants()["sensor.rp_csi_zone"]
        variants = list(bad)
        n = 0
        errs = []
        for name in variants:
            for fp in FPZ:
                for attrs_present in (True, False):
                    for prev in PREV:
                        for age in AGES:
                            fx = merge(base(), FPZ[fp])
                            if not attrs_present:
                                fx["sensor.rp_fingerprints"] = ("unknown", {})
                            fx["sensor.rp_csi_zone"] = bad[name]
                            this = this_zone(prev, age)
                            cz, cr = run(CAND, fx, this, new=new_uids())
                            mz, mr = run_master(fx, this)
                            n += 1
                            mkey = MASTER_KEY(mr)
                            self.assertIsNotNone(mkey, "unmapped master reason %r" % (mr,))
                            exp = EXPECT_REASON(mkey)
                            dwell = (mz == prev and mz != cz and age == 10)
                            if cz != mz or not (cr == exp or (dwell and cr in (exp, DWELL_NAME))):
                                if len(errs) < 20:
                                    errs.append((name, fp, attrs_present, prev, age, cz, mz, cr, exp))
        self.assertEqual(n, len(variants) * 5 * 2 * 5 * 2)
        self.assertEqual(errs, [], "(variant, fp, attrs, prev, age, cz, mz, cr, exp): %r" % (errs,))

    def test_future_timestamp_no_error(self):
        # A3.3 (O2: ts = NOW + 5; valid by the constants, csi or ble_fallback accepted)
        fx = merge(base(), csi_ok(state="pc_desk", ts=EPOCH + 5))
        zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
        self.assertIn(zone, {"pc_desk", "work_desk"})
        self.assertIn(reason, {"csi", "ble_fallback"})


class TestA4(unittest.TestCase):
    def test_away_beats_fresh_csi(self):
        # A4.1
        for state in ("pc_desk", "kitchen", "bed", "home"):
            with self.subTest(state=state):
                fx = merge(base(), csi_ok(state=state), {"binary_sensor.yui_home": ("off", {})})
                zone, reason = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
                self.assertEqual(zone, "away")
                self.assertEqual(reason, RULE_NAME["away"])

    def test_home_away_templates_unchanged(self):
        # A4.2 / guard
        uids = ["yui_home", "rp_away_condition", "rp_phone_seen", "rp_watch_seen",
                "rp_nodes_alive", "rp_phone_irk_loaded"]
        ments = template_entities(master_cfg())
        for uid in uids:
            with self.subTest(uid=uid):
                ce = CAND_ENTS[uid]["ent"]
                me = ments[uid]["ent"]
                self.assertEqual(" ".join(ce["state"].split()), " ".join(me["state"].split()))
                for d in ("delay_on", "delay_off"):
                    self.assertEqual(ce.get(d), me.get(d))
                self.assertNotIn("csi", ce["state"].lower())

    def test_matrix_away_never_overridden(self):
        # A4.3 / guard: home off and pc != active -> away, lead on, CSI kitchen
        n = 0
        bad = []
        for key, fx in MATRIX:
            pc, home = key[0], key[1]
            if home != "off" or pc == "active":
                continue
            f = merge(fx, csi_ok(state="kitchen"))
            cz, _ = case("cand", ("43",) + key, f, this_zone(key[6], key[7]))
            mz, _ = case("master", ("43",) + key, f, this_zone(key[6], key[7]))
            n += 1
            if cz != mz and len(bad) < 20:
                bad.append((key, cz, mz))
        self.assertEqual(n, 1800)
        self.assertEqual(bad, [], "(fixture, cand, master): %r" % (bad,))


class TestA5(unittest.TestCase):
    def test_rule_vs_csi_precedence(self):
        # A5.1: the four table rows + the night_bed special cases (O3)
        rows = [
            ("pc_active", merge(base(), csi_ok(state="kitchen"), PC["active"]), "work_desk", "pc_desk"),
            ("pc_hold", merge(base(), csi_ok(state="work_desk"), PC["hold"]), "pc_desk", "pc_desk"),
            ("kitchen_radar", merge(base(), csi_ok(state="pc_desk"), RADAR["hint"]), "work_desk", "kitchen"),
            ("phone_unheard", merge(base(), csi_ok(state="pc_desk"), SIG["deaf"]), "work_desk", "home"),
        ]
        for rule, fx, prev, zone in rows:
            with self.subTest(rule=rule):
                self.assertLess(RULES.index(rule), CSI_SLOT)
                z, r = run(CAND, fx, this_zone(prev, 600), new=new_uids())
                self.assertEqual(z, zone)
                self.assertEqual(r, RULE_NAME[rule])
        # A5.1a: night_bed on + CSI pc_desk / work_desk -> bed / night_bed
        for state in ("pc_desk", "work_desk"):
            with self.subTest(night="a", csi=state):
                fx = merge(base(), csi_ok(state=state), NIGHT["on"])
                z, r = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
                self.assertEqual(z, "bed")
                self.assertEqual(r, "night_bed")
        # A5.1b: night_bed on + CSI kitchen / bathroom / bed -> CSI zone / csi
        for state in ("kitchen", "bathroom", "bed"):
            with self.subTest(night="b", csi=state):
                fx = merge(base(), csi_ok(state=state), NIGHT["on"])
                z, r = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
                self.assertEqual(z, state)
                self.assertEqual(r, "csi")
        # A5.1c (2026-11-14): phone unheard + CSI bed >= 0.7 -> bed / csi; below 0.7 or another CSI zone -> home / phone_unheard
        for state, conf, zone, rule in (("bed", 0.7, "bed", "csi"), ("bed", 1.0, "bed", "csi"),
                                        ("bed", 0.69, "home", "phone_unheard"), ("work_desk", 1.0, "home", "phone_unheard"),
                                        ("bathroom", 1.0, "home", "phone_unheard")):
            for night in ("off", "on"):
                with self.subTest(deaf=state, conf=conf, night=night):
                    fx = merge(base(), csi_ok(state=state, confidence=conf), SIG["deaf"], NIGHT[night])
                    z, r = run(CAND, fx, this_zone("bed", 600), new=new_uids())
                    self.assertEqual(z, zone)
                    self.assertEqual(r, rule)

    def test_kitchen_fp_needs_evidence_on_fallback(self):
        # A5.2
        stale = bad_variants()["sensor.rp_csi_zone"]["stale_600"]
        fx = merge(base(), {"sensor.rp_csi_zone": stale, "sensor.rp_fp_zone": ("kitchen", {})})
        z, r = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
        self.assertEqual(z, "home")
        self.assertEqual(r, "ble_fallback")
        fx2 = merge(fx, {"binary_sensor.rp_kitchen_radar_recent": ("on", {}),
                         "binary_sensor.rp_kitchen_evidence": ("on", {}),
                         "sensor.rp_power_max120": ("1500", {})})
        z2, r2 = run(CAND, fx2, this_zone("work_desk", 600), new=new_uids())
        self.assertEqual(z2, "kitchen")
        self.assertEqual(r2, "ble_fallback")

    def test_min_dwell_fp_path(self):
        # A5.3 / guard
        for age, expect in ((10, "work_desk"), (44, "work_desk"), (45, "pc_desk"), (600, "pc_desk")):
            with self.subTest(age=age):
                fx = merge(base(), {"input_boolean.rp_csi_lead": ("off", {}),
                                    "sensor.rp_fp_zone": ("pc_desk", {})})
                z, _ = run(CAND, fx, this_zone("work_desk", age), new=new_uids())
                mz, _ = run_master(fx, this_zone("work_desk", age))
                self.assertEqual(z, expect)
                self.assertEqual(z, mz)

    def test_min_dwell_csi_path(self):
        # A5.4
        for age, expect in ((10, "work_desk"), (44, "work_desk"), (45, "pc_desk")):
            with self.subTest(age=age):
                fx = merge(base(), csi_ok(state="pc_desk"))
                z, r = run(CAND, fx, this_zone("work_desk", age), new=new_uids())
                if CSI_SOFT:
                    self.assertEqual(z, expect)
                    self.assertIn(r, ("csi", DWELL_NAME))
                    if age == 45:
                        self.assertEqual(r, "csi")
                else:
                    self.assertEqual(z, "pc_desk")
                    self.assertEqual(r, "csi")

    def test_bathroom_radar_vs_csi(self):
        # A5.5 (O5/O9: not skipped - fixture the hint binary_sensor directly)
        if BATHROOM_RADAR_ENTITY is None:
            self.skipTest("no bathroom radar entity known: master has none, interface list has none")
        fx = merge(base(), csi_ok(state="pc_desk"), {BATHROOM_RADAR_ENTITY: ("on", {})})
        z, r = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
        self.assertEqual(z, BATHROOM_ZONE)
        self.assertEqual(r, BATHROOM_RULE)


class TestA6(unittest.TestCase):
    def test_reason_closed_set_everywhere(self):
        # A6.1
        checks = []
        checks.append((merge(base(), csi_ok(state="kitchen"), {"input_boolean.rp_csi_lead": ("off", {})}), "work_desk", 600))
        for state in ("pc_desk", "work_desk", "bed"):
            checks.append((merge(base(), csi_ok(state=state)), "work_desk", 600))
        for name, val in bad_variants()["sensor.rp_csi_zone"].items():
            checks.append((merge(base(), {"sensor.rp_csi_zone": val}), "work_desk", 600))
        checks.append((merge(base(), csi_ok(state="kitchen"), {"binary_sensor.yui_home": ("off", {})}), "work_desk", 600))
        checks.append((merge(base(), csi_ok(state="kitchen"), PC["active"]), "work_desk", 600))
        for key, fx in MATRIX[:50]:
            checks.append((fx, key[6], key[7]))
        for fx, prev, age in checks:
            _, r = run(CAND, fx, this_zone(prev, age), new=new_uids())
            self.assertIn(r, ALLOWED_REASONS)
            self.assertRegex(r, r"^[a-z_]+$")

    def test_csi_reason_iff_csi_zone(self):
        # A6.2
        cases = []
        for state in ("pc_desk", "work_desk", "bed"):
            cases.append((merge(base(), csi_ok(state=state)), state))
        for name, val in bad_variants()["sensor.rp_csi_zone"].items():
            cases.append((merge(base(), {"sensor.rp_csi_zone": val}), None))
        cases.append((merge(base(), csi_ok(state="pc_desk"), {"input_boolean.rp_csi_lead": ("off", {})}), None))
        for fx, csi_state in cases:
            z, r = run(CAND, fx, this_zone("work_desk", 600), new=new_uids())
            if r == "csi":
                self.assertIsNotNone(csi_state)
                self.assertEqual(z, csi_state)
            if csi_state is None:
                self.assertNotEqual(r, "csi")


class TestA9(unittest.TestCase):
    def test_templates_compile(self):
        # A9.1 / guard
        n = 0
        for s in itertools.chain(_tpl_strings(CAND.get("template", [])), _tpl_strings(CAND.get("automation", []))):
            if "{{" in s or "{%" in s:
                n += 1
                try:
                    ENV.parse(s)
                except jinja2.TemplateSyntaxError as exc:
                    self.fail("TemplateSyntaxError: %s\n%s" % (exc, s))
        self.assertGreater(n, 0)

    def test_unique_ids_unique(self):
        # A9.2 / guard
        seen = []
        _collect_unique_ids(CAND, seen)
        dupes = {u for u in seen if seen.count(u) > 1}
        self.assertEqual(dupes, set())


class TestA11(unittest.TestCase):
    def test_automations_switch_stays_off(self):
        # A11.1 / guard
        self.assertIs(CAND["input_boolean"]["room_presence_automations"]["initial"], False)
        with open(os.environ["RP_CONFIG"], encoding="utf-8") as f:
            lines = f.read().splitlines()
        hits = []
        for i, line in enumerate(lines):
            if "room_presence_automations" in line:
                window = "\n".join(lines[max(0, i - 3):i + 1])
                if "turn_on" in window:
                    hits.append(i + 1)
        self.assertEqual(hits, [])


def _tpl_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _tpl_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _tpl_strings(v)


def _collect_unique_ids(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "unique_id" and isinstance(v, str):
                out.append(v)
            else:
                _collect_unique_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_unique_ids(v, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
