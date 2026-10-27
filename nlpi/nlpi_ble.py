"""BLE presence scanner for nl-pi (onboard Bluetooth), same MQTT schema as the ESP32 firmware.

Publishes presence/nlpi/apple every 3 s:
  {"scan":1,"apple_best":-61,"apple_avg":-66,"apple_n":9,"irk_n":1,"irk":[{"best":-58,"avg":-60,"n":4}],"heap":0,"up":55}
Subscribes presence/config/irk (hex, any separators, <= 4 keys; both byte orders tried)
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

NODE = os.environ.get("BLE_NODE", "nlpi")
PUBLISH_S = 3
MAX_IRK = 4
APPLE_TYPES = (0x10, 0x0C, 0x0F)  # Nearby Info, Handoff, Nearby Action

irks = []          # list of (key_as_given, key_reversed) bytes
cache = {}         # address -> irk index or -1
enabled = True
apple = []
irk_samples = [[] for _ in range(MAX_IRK)]


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
    for i, (k1, k2) in enumerate(irks):
        if resolves(addr, k1) or resolves(addr, k2):
            found = i
            break
    if len(cache) > 500:
        cache.clear()
    cache[addr] = found
    return found


def parse_irks(text):
    nibbles = re.sub(r"0[xX]", "", text)
    nibbles = re.sub(r"[^0-9a-fA-F]", "", nibbles)
    keys = []
    for i in range(0, len(nibbles) - 31, 32):
        if len(keys) >= MAX_IRK:
            break
        k = bytes.fromhex(nibbles[i:i + 32])
        keys.append((k, k[::-1]))
    return keys


def on_adv(device, adv):
    if not enabled:
        return
    rssi = adv.rssi
    if irks:
        idx = resolve_addr(device.address)
        if idx >= 0:
            irk_samples[idx].append(rssi)
    md = adv.manufacturer_data.get(0x004C)
    if md and len(md) > 0 and md[0] in APPLE_TYPES:
        apple.append(rssi)


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
            irk_now = [summary(s) for s in irk_samples[:len(irks)]]
            for s in irk_samples:
                s.clear()
            payload = {
                "scan": 1 if enabled else 0,
                "apple_best": max(a) if a else -127,
                "apple_avg": round(sum(a) / len(a)) if a else -127,
                "apple_n": len(a),
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
