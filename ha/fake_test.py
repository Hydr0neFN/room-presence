"""Publish fake presence/<node>/apple messages (every 3 s) for one scenario, then stop.
  py -3.13 ha/fake_test.py <scenario> <seconds>
Scenarios: phone_work   phone at workdesk -55, pcdesk -70, watch -40 and a MacBook (apple_best -30) at pcdesk
           nophone_mac  no IRK anywhere: MacBook -30 (stationary, mov_n 0) + a walking phone -62 at pcdesk
           none         nodes alive, nothing heard (phone + watch absent)
Fake messages are not retained and expire in HA after 30 s; nothing to clear."""
import json
import os
import sys
import time

import paho.mqtt.client as mqtt

HERE = os.path.dirname(os.path.abspath(__file__))


def irk(phone=None, watch=None):
    def e(i, v):
        return {"id": i, "best": v if v else -127, "avg": v if v else -127, "n": 3 if v else 0}
    out = []
    if phone is not None:
        out.append(e("phone", phone))
    if watch is not None:
        out.append(e("watch", watch))
    return out


def msg(apple_best=-127, apple_n=0, mov_best=-127, mov_n=0, irks=()):
    return {"scan": 1, "apple_best": apple_best, "apple_avg": apple_best, "apple_n": apple_n,
            "mov_best": mov_best, "mov_avg": mov_best, "mov_n": mov_n, "static_n": 1,
            "irk_n": len(irks), "irk": list(irks), "heap": 90000, "up": 1}


SCEN = {
    "phone_work": {
        "pcdesk": msg(-30, 6, -127, 0, irk(-70, -40)),
        "workdesk": msg(-60, 2, -127, 0, irk(-55, -75)),
        "nlpi": msg(-127, 0, -127, 0, irk(0, 0)),
    },
    "nophone_mac": {
        "pcdesk": msg(-30, 9, -62, 2),
        "workdesk": msg(-127, 0),
        "nlpi": msg(-127, 0),
    },
    "none": {
        "pcdesk": msg(irks=irk(0, 0)),
        "workdesk": msg(irks=irk(0, 0)),
        "nlpi": msg(irks=irk(0, 0)),
    },
}

with open(os.path.join(HERE, "..", "pc-idle", "mqtt-pass.local"), encoding="utf-8") as f:
    pw = f.read().strip()
c = mqtt.Client(client_id="fake_presence_test")
c.username_pw_set("pcpresence", pw)
c.connect("10.0.0.20", 1883, 60)
c.loop_start()
t_end = time.time() + int(sys.argv[2])
while time.time() < t_end:
    for node, m in SCEN[sys.argv[1]].items():
        c.publish(f"presence/{node}/apple", json.dumps(m), qos=0, retain=False)
    time.sleep(3)
c.loop_stop()
