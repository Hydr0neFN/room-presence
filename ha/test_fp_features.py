"""Check the 'RP fp zone' template of ha/room_presence.yaml (BLE + WiFi-feature distance) against a
pure-Python reference. Renders the real template text with jinja2 (stubbed states/state_attr).
  py -3.13 ha/test_fp_features.py
"""
import math
import os
import sys

import jinja2
import jinja2.sandbox
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


class L(yaml.SafeLoader):
    pass


def load_template():
    d = yaml.load(open(os.path.join(HERE, "room_presence.yaml"), encoding="utf-8"), L)
    for blk in d["template"]:
        for s in blk.get("sensor", []):
            if s["name"] == "RP fp zone":
                return s["state"]
    raise SystemExit("template not found")


TEMPLATE = load_template()
DEBUG_TAIL = "{{ prev if prev in zones and ns.pd - ns.bd < h else ns.best }}"
assert DEBUG_TAIL in TEMPLATE
DEBUG = TEMPLATE.replace(DEBUG_TAIL, "{{ ns.best }}|{{ ns.bd }}|{{ ns.pd }}")
ENV = jinja2.sandbox.ImmutableSandboxedEnvironment()


class This:
    def __init__(self, state):
        self.state = state


def render(tpl, states, attrs, prev=None):
    def st(e):
        return states.get(e, "unknown")

    ctx = {"states": st, "state_attr": lambda e, a: attrs.get(a)}
    if prev is not None:
        ctx["this"] = This(prev)
    return ENV.from_string(tpl).render(**ctx).strip()


def fnum(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


FEAT_ENTITY = lambda fn: (  # noqa: E731
    f"sensor.rp_{fn.split('_')[1]}_csi_still" if fn.split("_")[0] == "csi" and fn.split("_")[2] == "still"
    else f"sensor.rp_link_{fn.split('_')[1]}_motion")


def reference(states, attrs, prev):
    nodes, zones = attrs["nodes"], attrs["zones"]
    feat = attrs.get("feat") or {}
    w = fnum(states.get("input_number.rp_feat_weight_db"), 6.0)
    h = fnum(states.get("input_number.rp_fp_hysteresis"), 2.5)
    cur = {}
    if w > 0:
        for fn in feat.get("names", []):
            x = fnum(states.get(FEAT_ENTITY(fn)))
            if x is not None:
                cur[fn] = x
    best, bd, pd = "none", 999, 999
    for z, f in zones.items():
        d, k = 0.0, 0
        for n in nodes:
            sg = max(fnum(states.get(f"sensor.rp_{n}_signal"), -127.0), -100)
            d += (sg - fnum(f.get(n), -100.0)) ** 2
        fv = feat.get("zones", {}).get(z, {})
        for fn, x in cur.items():
            mu = fv.get(fn)
            sd = feat.get("scale", {}).get(fn, 0)
            if mu is not None and sd > 0:
                d += (w * (x - mu) / sd) ** 2
                k += 1
        r = math.sqrt(d / max(len(nodes) + k, 1))
        if r < bd:
            bd, best = r, z
        if z == prev:
            pd = r
    zone = prev if prev in zones and pd - bd < h else best
    return zone, best, bd, pd


NODES = ["nlpi", "pcdesk", "workdesk"]
ZONES = {
    "pc_desk": {"nlpi": -100, "pcdesk": -50, "workdesk": -75},
    "work_desk": {"nlpi": -100, "pcdesk": -72, "workdesk": -52},
    "bed": {"nlpi": -60, "pcdesk": -90, "workdesk": -88},
}
FEAT = {
    "names": ["csi_pcdesk_motion", "csi_pcdesk_still", "csi_workdesk_motion", "link_kitchenradar", "link_neopixel"],
    "zones": {
        "pc_desk": {"csi_pcdesk_motion": 40.0, "csi_pcdesk_still": 10.0, "csi_workdesk_motion": 5.0,
                    "link_kitchenradar": 0.8, "link_neopixel": 1.2},
        "work_desk": {"csi_pcdesk_motion": 6.0, "csi_pcdesk_still": 9.0, "csi_workdesk_motion": 35.0,
                      "link_kitchenradar": 0.9},               # lacks link_neopixel
        "bed": {"csi_pcdesk_motion": 5.0, "csi_pcdesk_still": 8.0, "csi_workdesk_motion": 4.0,
                "link_kitchenradar": 0.7, "link_neopixel": 2.4},
    },
    "scale": {"csi_pcdesk_motion": 15.0, "csi_pcdesk_still": 3.0, "csi_workdesk_motion": 14.0,
              "link_kitchenradar": 0.5, "link_neopixel": 0.6},
}


def base_states():
    return {"sensor.rp_nlpi_signal": "-127", "sensor.rp_pcdesk_signal": "-60", "sensor.rp_workdesk_signal": "-64",
            "input_number.rp_feat_weight_db": "6", "input_number.rp_fp_hysteresis": "2.5",
            "sensor.rp_link_pcdesk_motion": "38.5", "sensor.rp_pcdesk_csi_still": "11",
            "sensor.rp_link_workdesk_motion": "6.1", "sensor.rp_link_kitchenradar_motion": "0.85",
            "sensor.rp_link_neopixel_motion": "1.1"}


def case(name, mutate, prev=None, feat=FEAT):
    s = base_states()
    attrs = {"nodes": NODES, "zones": ZONES, "feat": feat}
    mutate(s, attrs)
    zone, best, bd, pd = reference(s, attrs, prev)
    got_zone = render(TEMPLATE, s, attrs, prev)
    got = render(DEBUG, s, attrs, prev).split("|")
    ok = got_zone == zone and got[0] == best and math.isclose(float(got[1]), bd, rel_tol=1e-9) and \
        (pd == 999 and float(got[2]) == 999 or math.isclose(float(got[2]), pd, rel_tol=1e-9))
    print(("PASS " if ok else "FAIL ") + name, "->", got_zone, f"(ref {zone}, d={bd:.4f}, tpl d={float(got[1]):.4f})")
    return ok


def main():
    res = [
        case("features on, all numeric", lambda s, a: None),
        case("feature weight 0 disables features", lambda s, a: s.update({"input_number.rp_feat_weight_db": "0"})),
        case("a live feature is unavailable (skipped)", lambda s, a: s.update({"sensor.rp_link_workdesk_motion": "unavailable",
                                                                            "sensor.rp_link_neopixel_motion": "unknown"})),
        case("no feat attribute (old behaviour)", lambda s, a: a.pop("feat")),
        case("features flip BLE tie", lambda s, a: s.update({"sensor.rp_pcdesk_signal": "-61", "sensor.rp_workdesk_signal": "-61",
                                                          "sensor.rp_link_pcdesk_motion": "3", "sensor.rp_link_workdesk_motion": "36"})),
        case("hysteresis keeps previous zone", lambda s, a: s.update({"input_number.rp_fp_hysteresis": "10"}), prev="work_desk"),
        case("hysteresis released", lambda s, a: s.update({"input_number.rp_fp_hysteresis": "0.5"}), prev="work_desk"),
        case("pcdesk signal unavailable (-127 -> floor -100)", lambda s, a: s.update({"sensor.rp_pcdesk_signal": "unavailable"})),
    ]
    print(f"{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
