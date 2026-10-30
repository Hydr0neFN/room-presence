"""Read-only smoke run: live MQTT in, Engine out to stdout. Publishes nothing. Usage: CSI_DIR=/tmp/x python3 smoke_live.py [seconds]"""
import json, os, sys, time
import paho.mqtt.client as mqtt
import csi_svc as svc

t0 = time.time()
eng = svc.Engine()
print("engine init %.1fs notes=%s seed=%d" % (time.time() - t0, eng.notes, len(eng.seed)), flush=True)
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
c.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
c.on_connect = lambda cl, u, f, rc: [cl.subscribe(t) for t in ("presence/+/csi", "presence/zone/state")]

def on_message(cl, u, m):
    now = time.time()
    try:
        p = json.loads(m.payload)
    except ValueError:
        return
    if m.topic == "presence/zone/state":
        eng.on_ctx(p, now)
    elif m.topic.endswith("/csi"):
        eng.on_csi(m.topic.split("/")[1], p, now)

c.on_message = on_message
c.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), 1883, 60)
c.loop_start()
end = time.time() + int(sys.argv[1] if len(sys.argv) > 1 else 90)
while time.time() < end:
    time.sleep(10 - time.time() % 10)
    st, ag, row = eng.tick(time.time())
    print(json.dumps({k: st[k] for k in ("zone", "confidence", "scores", "used_nodes", "live_zone", "fp_zone", "weak_label", "baseline_nodes", "baseline_span_h", "baseline_quiet_age_min", "training")}), flush=True)
print(json.dumps(ag), flush=True)
eng.save()
print("saved; state size", os.path.getsize(eng._path()))
c.loop_stop()
