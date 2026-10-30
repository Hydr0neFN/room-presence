"""Position labelling site (runs on nl-pi, LAN only): press the zone you are standing in; the last WINDOW_S seconds of
phone / watch / BLE / CSI signals are stored with that label in SQLite, for later analysis (accuracy of the zone logic,
re-fitting fingerprints).

  http://10.0.0.20:8097/            page (phone friendly)
  POST /api/label {"zone": ...}      store one labelled snapshot
  POST /api/undo                     delete the newest label
  GET  /api/state                    counts per zone + newest labels
  GET  /api/export.csv               one row per label x node
Tables (DB_FILE): labels(id, ts, zone, window_s), samples(label_id, node, phone_avg, phone_n, watch_avg, watch_n,
mov_best, msgs, csi_motion, csi_still, csi_n). Env: MQTT_* (presence-ble.env), DB_FILE, BIND (default 10.0.0.20), PORT.
Not heard = NULL. Also publishes presence/label/event (not retained).
"""
import csv
import io
import json
import os
import sqlite3
import statistics
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

ZONES = [("kitchen", "廚房"), ("work_desk", "工作桌"), ("pc_desk", "電腦桌"), ("bed", "床"), ("bathroom", "浴室/門口")]
ZONE_IDS = [z for z, _ in ZONES]
WINDOW_S = 15
KEEP_S = 60
DB_FILE = os.environ.get("DB_FILE", "/var/lib/presence-labels/labels.db")
buf = defaultdict(lambda: {"apple": deque(), "csi": deque()})
buf_lock = threading.Lock()
db_lock = threading.Lock()
mq = None

PAGE = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>定位標記</title>
<style>body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#111;color:#eee}
h1{font-size:18px;margin:0 0 12px}button{width:100%;font-size:22px;padding:22px;margin:8px 0;border:0;border-radius:14px;
background:#2b6cb0;color:#fff}button:active{background:#1a4a80}button.u{background:#444;font-size:16px;padding:12px}
#msg{min-height:3em;padding:8px;background:#222;border-radius:8px;margin:8px 0;white-space:pre-wrap;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:3px 4px;text-align:left;border-bottom:1px solid #333}</style></head>
<body><h1>我現在在哪裡？按一下，記錄這個位置的手機 / 手錶 / 藍牙 / CSI 訊號</h1>
<div id="btns"></div><div id="msg"></div>
<button class="u" onclick="undo()">復原最後一筆</button><p><a style="color:#8cf" href="/api/export.csv">匯出 CSV</a></p>
<div id="st"></div>
<script>
const Z=%ZONES%;
const b=document.getElementById('btns');
Z.forEach(([id,n])=>{const e=document.createElement('button');e.textContent=n;e.onclick=()=>lab(id,e);b.appendChild(e)});
async function lab(z,el){el.disabled=true;msg('記錄中…');
 try{const r=await fetch('/api/label',{method:'POST',body:JSON.stringify({zone:z})});const j=await r.json();
 msg(j.ok?('已存 #'+j.id+'（'+j.zone+'）\n手機：'+j.phone_nodes+' 個節點聽到　手錶：'+j.watch_nodes+' 個節點聽到\n'+j.detail):('失敗：'+j.error))}
 catch(e){msg('失敗：'+e)}el.disabled=false;st()}
async function undo(){const r=await fetch('/api/undo',{method:'POST'});const j=await r.json();msg(j.ok?'已刪除 #'+j.id:'沒有可復原的');st()}
function msg(t){document.getElementById('msg').textContent=t}
async function st(){const j=await (await fetch('/api/state')).json();
 let h='<table><tr><th>區域</th><th>筆數</th></tr>';for(const [id,n] of Z)h+='<tr><td>'+n+'</td><td>'+(j.counts[id]||0)+'</td></tr>';
 h+='</table><p>最近：</p><table>';for(const l of j.last)h+='<tr><td>#'+l.id+'</td><td>'+l.zone+'</td><td>'+l.time+'</td></tr>';
 document.getElementById('st').innerHTML=h+'</table>'}
st();
</script></body></html>"""


def conn():
    c = sqlite3.connect(DB_FILE)
    c.execute("CREATE TABLE IF NOT EXISTS labels(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, zone TEXT, window_s INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS samples(label_id INTEGER, node TEXT, phone_avg REAL, phone_n INTEGER, watch_avg REAL,"
              " watch_n INTEGER, mov_best REAL, msgs INTEGER, csi_motion REAL, csi_still REAL, csi_n INTEGER)")
    return c


def on_message(c, u, m):
    try:
        _, node, kind = m.topic.split("/")
        p = json.loads(m.payload)
    except (ValueError, TypeError):
        return
    now = time.time()
    with buf_lock:
        q = buf[node][kind]
        q.append((now, p))
        while q and q[0][0] < now - KEEP_S:
            q.popleft()


def labelled(p, ident):
    for e in p.get("irk") or []:
        if e.get("id") == ident:
            return e.get("avg") if e.get("n", 0) > 0 else None
    return None


def snapshot():
    """Per node summary of the last WINDOW_S seconds."""
    t0 = time.time() - WINDOW_S
    out = {}
    with buf_lock:
        for node, kinds in buf.items():
            apple = [p for t, p in kinds["apple"] if t >= t0]
            csi = [p for t, p in kinds["csi"] if t >= t0 and int(p.get("ok", 0)) == 1]
            ph = [v for v in (labelled(p, "phone") for p in apple) if v is not None]
            wa = [v for v in (labelled(p, "watch") for p in apple) if v is not None]
            mb = [p.get("mov_best") for p in apple if p.get("mov_n", 0) > 0 and p.get("mov_best") is not None]
            mo = [float(p["motion"]) for p in csi if p.get("motion") is not None]
            st = [float(p["still"]) for p in csi if p.get("still") is not None]
            if not (apple or csi):
                continue
            out[node] = {"phone_avg": round(statistics.mean(ph), 1) if ph else None, "phone_n": len(ph),
                         "watch_avg": round(statistics.mean(wa), 1) if wa else None, "watch_n": len(wa),
                         "mov_best": max(mb) if mb else None, "msgs": len(apple),
                         "csi_motion": round(statistics.mean(mo), 2) if mo else None,
                         "csi_still": round(statistics.mean(st), 2) if st else None, "csi_n": len(csi)}
    return out


def add_label(zone):
    snap = snapshot()
    if not snap:
        return {"ok": False, "error": "no signals received in the last %d s" % WINDOW_S}
    with db_lock:
        c = conn()
        cur = c.execute("INSERT INTO labels(ts, zone, window_s) VALUES(?,?,?)", (time.time(), zone, WINDOW_S))
        lid = cur.lastrowid
        for node, s in snap.items():
            c.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (lid, node, s["phone_avg"], s["phone_n"], s["watch_avg"], s["watch_n"], s["mov_best"], s["msgs"],
                       s["csi_motion"], s["csi_still"], s["csi_n"]))
        c.commit()
        c.close()
    detail = "\n".join("%s: 手機 %s 手錶 %s" % (n, s["phone_avg"], s["watch_avg"]) for n, s in sorted(snap.items()))
    mq.publish("presence/label/event", json.dumps({"id": lid, "zone": zone, "ts": int(time.time())}))
    return {"ok": True, "id": lid, "zone": zone, "detail": detail,
            "phone_nodes": sum(1 for s in snap.values() if s["phone_n"]),
            "watch_nodes": sum(1 for s in snap.values() if s["watch_n"])}


def state():
    with db_lock:
        c = conn()
        counts = dict(c.execute("SELECT zone, COUNT(*) FROM labels GROUP BY zone"))
        last = [{"id": i, "zone": z, "time": time.strftime("%m-%d %H:%M", time.localtime(t))}
                for i, t, z in c.execute("SELECT id, ts, zone FROM labels ORDER BY id DESC LIMIT 8")]
        c.close()
    return {"counts": counts, "last": last}


def undo():
    with db_lock:
        c = conn()
        row = c.execute("SELECT id FROM labels ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            c.execute("DELETE FROM samples WHERE label_id=?", (row[0],))
            c.execute("DELETE FROM labels WHERE id=?", (row[0],))
            c.commit()
        c.close()
    return {"ok": bool(row), "id": row[0] if row else None}


def export_csv():
    with db_lock:
        c = conn()
        rows = c.execute("SELECT l.id, datetime(l.ts,'unixepoch','localtime'), l.zone, s.node, s.phone_avg, s.phone_n, s.watch_avg,"
                         " s.watch_n, s.mov_best, s.msgs, s.csi_motion, s.csi_still, s.csi_n FROM labels l JOIN samples s ON"
                         " s.label_id=l.id ORDER BY l.id, s.node").fetchall()
        c.close()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["label_id", "time", "zone", "node", "phone_avg", "phone_n", "watch_avg", "watch_n", "mov_best", "msgs",
                "csi_motion", "csi_still", "csi_n"])
    w.writerows(rows)
    return out.getvalue().encode("utf-8")


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.replace("%ZONES%", json.dumps(ZONES, ensure_ascii=False)), "text/html")
        elif self.path == "/api/state":
            self.send(200, state())
        elif self.path == "/api/export.csv":
            self.send(200, export_csv(), "text/csv")
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send(400, {"ok": False, "error": "bad json"})
        if self.path == "/api/label":
            zone = body.get("zone")
            self.send(200, add_label(zone) if zone in ZONE_IDS else {"ok": False, "error": "unknown zone"})
        elif self.path == "/api/undo":
            self.send(200, undo())
        else:
            self.send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


def main():
    global mq
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn().close()
    mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="label_site") if hasattr(mqtt, "CallbackAPIVersion") \
        else mqtt.Client(client_id="label_site")
    mq.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASS"])
    mq.on_connect = lambda c, u, f, rc: [c.subscribe("presence/+/apple"), c.subscribe("presence/+/csi")]
    mq.on_message = on_message
    mq.connect(os.environ.get("MQTT_HOST", "127.0.0.1"), int(os.environ.get("MQTT_PORT", "1883")), 60)
    mq.loop_start()
    ThreadingHTTPServer((os.environ.get("BIND", "10.0.0.20"), int(os.environ.get("PORT", "8097"))), H).serve_forever()


if __name__ == "__main__":
    main()
