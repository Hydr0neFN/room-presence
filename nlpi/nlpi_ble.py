"""BLE presence scanner for nl-pi (onboard Bluetooth), same MQTT schema as the ESP32 firmware.

Publishes presence/nlpi/apple every 3 s:
  {"scan":1,"apple_best":-61,"apple_avg":-66,"apple_n":9,"mov_best":-70,"mov_avg":-72,"mov_n":2,
   "static_n":3,"irk_n":2,"irk":[{"id":"phone","best":-58,"avg":-60,"n":4},{"id":"watch","best":-127,"avg":-127,"n":0}],"heap":0,"up":55}
apple_* = every Apple advert, mov_* = only adverts NOT judged stationary (stationary.py; laptops/tablet).
Subscribes presence/config/irk: JSON {"phone":"<hex>","watch":"<hex>"} (labelled) or plain hex
(first key = phone; hex with any separators, <= 4 keys; both byte orders tried)
and presence/nlpi/cmd (on | off). Env: MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS.
"""
import asyncio
import json
import os
import re
import time

import paho.mqtt.client as mqtt
from bleak import BleakScanner
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from stationary import StationaryFilter

NODE = os.environ.get("BLE_NODE", "nlpi")
PUBLISH_S = 3
MAX_IRK = 4
APPLE_TYPES = (0x10, 0x0C, 0x0F)  # Nearby Info, Handoff, Nearby Action

irks = []          # list of (id, key_as_given, key_reversed)
cache = {}         # address -> irk index or -1
enabled = True
apple = []         # every Apple advert
mov = []           # Apple adverts of devices not judged stationary
irk_samples = [[] for _ in range(MAX_IRK)]
stat_filter = StationaryFilter()


def ah(key, prand):
    """Bluetooth ah(): AES-128(k, 0^104 || prand), low 24 bits."""
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    out = enc.update(b"\x00" * 13 + prand) + enc.finalize()
    return out[13:16]


def resolves(addr, key):
    b = bytes.fromhex(addr.replace(":", ""))
    if len(b) != 6 or (b[0] & 0xC0) != 0x40:
        return False
    return ah(key, b[0:3]) == b[3:6]


def resolve_addr(addr):
    if addr in cache:
        return cache[addr]
    found = -1
    for i, (_id, k1, k2) in enumerate(irks):
        if resolves(addr, k1) or resolves(addr, k2):
            found = i
            break
    if len(cache) > 500:
        cache.clear()
    cache[addr] = found
    return found


def _hex_keys(text):
    nibbles = re.sub(r"0[xX]", "", text)
    nibbles = re.sub(r"[^0-9a-fA-F]", "", nibbles)
    return [bytes.fromhex(nibbles[i:i + 32]) for i in range(0, len(nibbles) - 31, 32)]


def parse_irks(text):
    """Returns [(id, key, key_reversed)]. JSON object = labelled; anything else = plain hex."""
    out = []
    if text.lstrip().startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = {}
        for label, val in obj.items():
            keys = _hex_keys(str(val))
            if keys:
                out.append((str(label)[:12], keys[0], keys[0][::-1]))
    else:
        for n, k in enumerate(_hex_keys(text)):
            out.append(("phone" if n == 0 else f"irk{n + 1}", k, k[::-1]))
    return out[:MAX_IRK]


def on_adv(device, adv):
    if not enabled:
        return
    rssi = adv.rssi
    resolved = False
    if irks:
        idx = resolve_addr(device.address)
        if idx >= 0:
            irk_samples[idx].append(rssi)
            resolved = True
    md = adv.manufacturer_data.get(0x004C)
    if md and len(md) > 0 and md[0] in APPLE_TYPES:
        apple.append(rssi)
        # IRK-resolved devices are the owner's: never filtered, never part of the fallback.
        if not resolved and not stat_filter.observe(device.address, rssi, time.time()):
            mov.append(rssi)


def summary(samples):
    if not samples:
        return {"best": -127, "avg": -127, "n": 0}
    return {"best": max(samples), "avg": round(sum(samples) / len(samples)), "n": len(samples)}


async def main():
    global enabled, irks
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="nlpi_ble")
    client.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    client.will_set(f"presence/{NODE}/status", "offline", qos=1, retain=True)

    def on_connect(c, u, f, rc, props=None):
        c.subscribe("presence/config/irk")
        c.subscribe(f"presence/{NODE}/cmd")
        c.publish(f"presence/{NODE}/status", "online", qos=1, retain=True)

    def on_message(c, u, msg):
        global enabled, irks
        text = msg.payload.decode("utf-8", "ignore")
        if msg.topic == "presence/config/irk":
            irks = parse_irks(text)
            print("IRKs loaded:", [i[0] for i in irks], flush=True)
            cache.clear()
            for s in irk_samples:
                s.clear()
        elif msg.topic == f"presence/{NODE}/cmd":
            enabled = text.strip().lower() in ("on", "scan_on")

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(2, 60)
    client.connect_async(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    client.loop_start()

    scanner = BleakScanner(on_adv, bluez={"filters": {"DuplicateData": True}})
    await scanner.start()
    t0 = time.time()
    try:
        while True:
            await asyncio.sleep(PUBLISH_S)
            a, apple[:] = list(apple), []
            m, mov[:] = list(mov), []
            irk_now = [dict(id=irks[i][0], **summary(s)) for i, s in enumerate(irk_samples[:len(irks)])]
            for s in irk_samples:
                s.clear()
            payload = {
                "scan": 1 if enabled else 0,
                "apple_best": max(a) if a else -127,
                "apple_avg": round(sum(a) / len(a)) if a else -127,
                "apple_n": len(a),
                "mov_best": max(m) if m else -127,
                "mov_avg": round(sum(m) / len(m)) if m else -127,
                "mov_n": len(m),
                "static_n": stat_filter.static_count(time.time()),
                "irk_n": len(irks),
                "irk": irk_now,
                "heap": 0,
                "up": int(time.time() - t0),
            }
            client.publish(f"presence/{NODE}/apple", json.dumps(payload), qos=0, retain=False)
    finally:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())
