import sys
root = sys.argv[1]
p = root + '/platformio.ini'
s = open(p).read()
a = """    knolleary/PubSubClient@^2.8
build_flags =
"""
assert a in s
s = s.replace(a, """    knolleary/PubSubClient@^2.8
    h2zero/NimBLE-Arduino@^2.3.0
build_flags =
    ; BLE presence scanner (src/ble_presence.cpp, room-presence project): node "cyd",
    ; publisher-fn API, NimBLE trimmed to a passive observer.
    -D BLE_PUBLISH_FN_API
    -D BLE_NODE_NAME=\\"cyd\\"
    -D CONFIG_BT_NIMBLE_ROLE_CENTRAL_DISABLED
    -D CONFIG_BT_NIMBLE_ROLE_PERIPHERAL_DISABLED
    -D CONFIG_BT_NIMBLE_ROLE_BROADCASTER_DISABLED
    -D CONFIG_BT_NIMBLE_MAX_CONNECTIONS=1
    -D CONFIG_BT_NIMBLE_LOG_LEVEL=5
    -D CONFIG_NIMBLE_CPP_LOG_LEVEL=0
""", 1)
open(p, 'w').write(s)

n = open(root + '/src/net.cpp').read()


def rep(a, b):
    global n
    assert a in n, a
    n = n.replace(a, b, 1)


rep('#include <WiFi.h>\n', '#include <WiFi.h>\n\n#include "ble_presence.h"\n')
rep('static void on_message(char *topic, uint8_t *payload, unsigned int len) {\n',
    'static void on_message(char *topic, uint8_t *payload, unsigned int len) {\n'
    '  if (blepresence::onMessage(topic, payload, len)) return;  // presence/... (IRK config, cmd)\n')
rep('  s_mqtt.subscribe(TOPIC_PANEL "bench", 0);\n  return true;',
    '  s_mqtt.subscribe(TOPIC_PANEL "bench", 0);\n'
    '  blepresence::subscribe([](const char *t) { s_mqtt.subscribe(t); });\n  return true;')
rep('    disableLoopWDT();\n    portENTER_CRITICAL(&s_mux);\n    s_model.ota_active = true;',
    '    disableLoopWDT();\n    ble_scan_pause(true);  // radio + heap are needed for the upload\n'
    '    portENTER_CRITICAL(&s_mux);\n    s_model.ota_active = true;')
rep('    enableLoopWDT();\n  });\n  ArduinoOTA.begin();',
    '    enableLoopWDT();\n    ble_scan_pause(false);\n  });\n  ArduinoOTA.begin();')
rep('static void setup_ota() {',
    '// Stop/resume the BLE scanner through its own command path ("off"/"on" on presence/cyd/cmd).\n'
    'static void ble_scan_pause(bool pause) {\n'
    '  const char *cmd = pause ? "off" : "on";\n'
    '  blepresence::onMessage("presence/" BLE_NODE_NAME "/cmd", (const uint8_t *)cmd, strlen(cmd));\n}\n\n'
    'static void setup_ota() {')
rep('  WiFi.setSleep(false);  // modem sleep adds 100+ ms to every command',
    '  // WiFi/BT coexistence aborts with modem sleep fully off; MIN_MODEM costs a few ms per command.\n'
    '  WiFi.setSleep(WIFI_PS_MIN_MODEM);')
rep('  s_mqtt.setKeepAlive(30);', '  s_mqtt.setKeepAlive(60);  // BLE coexistence can stall WiFi for seconds')
rep('        s_mqtt.loop();\n        Cmd c;',
    '        s_mqtt.loop();\n'
    '        // BLE presence scanner (node "cyd"): start once WiFi + MQTT have been up for a while.\n'
    '        static bool ble_started = false;\n'
    '        if (!ble_started && now > 30000) {\n'
    '          ble_started = true;\n'
    '          blepresence::setup([](const char *t, const char *p, bool r) {\n'
    '            return s_mqtt.connected() && s_mqtt.publish(t, p, r);\n'
    '          });\n'
    '        }\n'
    '        blepresence::loop();\n'
    '        Cmd c;')
open(root + '/src/net.cpp', 'w').write(n)
