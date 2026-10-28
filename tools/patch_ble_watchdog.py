import sys
p = sys.argv[1]
raw = open(p, 'rb').read().decode()
s = raw.replace('\r\n', '\n')


def rep(old, new, cnt=1):
    global s
    assert s.count(old) == cnt, (s.count(old), old[:60])
    s = s.replace(old, new)


# 1. globals
rep('''#define LOCK() do { if (g_mux) xSemaphoreTake(g_mux, portMAX_DELAY); } while (0)
#define UNLOCK() do { if (g_mux) xSemaphoreGive(g_mux); } while (0)
bool g_enabled = true;
bool g_started = false;
uint32_t g_lastPublish = 0;
''', '''#define LOCK() do { if (g_mux) xSemaphoreTake(g_mux, portMAX_DELAY); } while (0)
#define UNLOCK() do { if (g_mux) xSemaphoreGive(g_mux); } while (0)
// loop() never blocks forever: a take that times out skips that publish cycle.
#define TRYLOCK(ms) (!g_mux || xSemaphoreTake(g_mux, pdMS_TO_TICKS(ms)) == pdTRUE)
bool g_enabled = true;
bool g_started = false;
uint32_t g_lastPublish = 0;

// Health / watchdog. 2026-10-27 the pcdesk node reset at 17:13 and the old guard kept the
// scanner off for the rest of that boot (14 h, heap 136 -> 182 kB, CSI kept running): after a
// crash-type reset the scanner is now started later (back-off) instead of never.
#ifndef BLE_STALL_MS
#define BLE_STALL_MS 60000UL // no advert of any kind for this long: restart the scan
#endif
#ifndef BLE_CRASH_DEFER_MS
#define BLE_CRASH_DEFER_MS 300000UL // first start after a crash-type reset (doubles per consecutive crash, max 16x)
#endif
#ifndef BLE_INIT_RETRY_MS
#define BLE_INIT_RETRY_MS 300000UL // retry after a failed init / low heap
#endif
#ifndef BLE_STATUS_MS
#define BLE_STATUS_MS 30000UL
#endif
enum BleState { ST_OFF = 0, ST_RUN = 1, ST_DEFER = 2, ST_INITFAIL = 3 };
constexpr uint32_t RTC_MAGIC = 0x424C4501;
RTC_NOINIT_ATTR uint32_t g_rtcMagic;
RTC_NOINIT_ATTR uint32_t g_rtcWd;    // watchdog-initiated chip restarts in a row, cleared like g_rtcCrash
RTC_NOINIT_ATTR uint32_t g_rtcCrash; // consecutive crash-type boots, cleared by power-on or 30 min of uptime
volatile uint32_t g_lastAdv = 0;     // millis() of the last advert, written by the host task
uint32_t g_ref = 0;                  // scan start or last watchdog kick
uint32_t g_startAt = 0;              // deferred start time (ST_DEFER / ST_INITFAIL)
uint32_t g_lastStatus = 0;
uint16_t g_scanRestarts = 0;
uint32_t g_runSince = 0;             // last successful bleStart()
uint8_t g_noScan = 0;                // loop ticks with the scan down although it should run
uint8_t g_stall = 0;                 // consecutive watchdog kicks without an advert in between
uint16_t g_lockTimeouts = 0;
uint8_t g_state = ST_OFF;
const char *g_rr = "UNKNOWN";

const char *resetName(esp_reset_reason_t r)
{
    switch (r)
    {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXT";
    case ESP_RST_SW: return "SW";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    default: return "OTHER";
    }
}
''')

# 2. advert timestamp
rep('''        const bool apple = md.size() >= 3 && (uint8_t)md[0] == 0x4C && (uint8_t)md[1] == 0x00;
        LOCK();''', '''        const bool apple = md.size() >= 3 && (uint8_t)md[0] == 0x4C && (uint8_t)md[1] == 0x00;
        g_lastAdv = millis();
        LOCK();''')

# 3. startScan stamps the start
rep('''    scan->start(0, false);   // 0 = until stopped
}''', '''    g_ref = millis();
    scan->start(0, false);   // 0 = until stopped
}''')

# 4. setup: replace guard + init
a = s.index('    // Never let the BLE controller put the board in a boot loop')
b = s.index('void subscribe(')
new_setup = '''    // Never let the BLE controller put the board in a boot loop: after a crash-type reset the
    // scanner is not started at once but after BLE_CRASH_DEFER_MS (doubling per consecutive
    // crash, max 16x), so a crash caused by BLE cannot loop faster than that, and a one-off
    // brownout does not cost the scanner for the rest of the boot.
    const esp_reset_reason_t why = esp_reset_reason();
    g_rr = resetName(why);
    if (g_rtcMagic != RTC_MAGIC)
    {
        g_rtcMagic = RTC_MAGIC;
        g_rtcCrash = 0;
        g_rtcWd = 0;
    }
    const bool crash = why == ESP_RST_PANIC || why == ESP_RST_BROWNOUT || why == ESP_RST_TASK_WDT || why == ESP_RST_INT_WDT || why == ESP_RST_WDT;
    if (why == ESP_RST_POWERON)
        g_rtcCrash = g_rtcWd = 0;
    else if (crash && g_rtcCrash < 1000)
        g_rtcCrash++;
    if (crash)
    {
        uint32_t shift = g_rtcCrash > 0 ? g_rtcCrash - 1 : 0;
        if (shift > 4)
            shift = 4;
        g_startAt = millis() + (BLE_CRASH_DEFER_MS << shift);
        g_state = ST_DEFER;
        Serial.printf("[BLE] previous reset was a crash (%s, %u in a row), scanner starts in %lu s\\n", g_rr, (unsigned)g_rtcCrash,
                      (unsigned long)((BLE_CRASH_DEFER_MS << shift) / 1000));
        return;
    }
    bleStart();
}

'''
s = s[:a] + new_setup + s[b:]

# 5. helpers before setup
rep('''} // namespace

#ifdef BLE_PUBLISH_FN_API
void setup(BlePublishFn publish)''', '''
bool pub(const String &topic, const char *payload)
{
#ifdef BLE_PUBLISH_FN_API
    return g_publish(topic.c_str(), payload, false);
#else
    return g_mqtt->publish(topic.c_str(), payload, false);
#endif
}

// Init NimBLE and start the scan. On failure the stack is released again and the start is
// retried every BLE_INIT_RETRY_MS (from loop()).
bool bleStart()
{
    if (ESP.getFreeHeap() < BLE_MIN_FREE_HEAP + 30000)
    {
        Serial.printf("[BLE] not enough heap (%u), retry later\\n", ESP.getFreeHeap());
        g_state = ST_INITFAIL;
        g_startAt = millis() + BLE_INIT_RETRY_MS;
        return false;
    }
    if (!NimBLEDevice::init(""))
    {
        Serial.printf("[BLE] init failed (free heap %u), retry later\\n", ESP.getFreeHeap());
        NimBLEDevice::deinit(true);
        g_state = ST_INITFAIL;
        g_startAt = millis() + BLE_INIT_RETRY_MS;
        return false;
    }
    g_started = true;
    g_state = ST_RUN;
    g_ref = millis();
    g_runSince = g_ref;
    if (g_enabled)
        startScan();
    Serial.printf("[BLE] scanner up, free heap %u\\n", ESP.getFreeHeap());
    return true;
}

// Time of the last sign of life of the scan: a real advert after the last (re)start, else the (re)start.
uint32_t lastSeen() { return (int32_t)(g_lastAdv - g_ref) > 0 ? g_lastAdv : g_ref; }

// Health record for HA: also published while the scanner is not running.
void publishStatus(uint32_t now)
{
    if (now - g_lastStatus < BLE_STATUS_MS)
        return;
    g_lastStatus = now;
    char b[200];
    snprintf(b, sizeof(b), "{\\"state\\":%d,\\"rr\\":\\"%s\\",\\"crash_n\\":%u,\\"restarts\\":%u,\\"stall\\":%u,\\"lock_to\\":%u,\\"adv_age\\":%ld,\\"heap\\":%u,\\"up\\":%lu}",
             (int)g_state, g_rr, (unsigned)g_rtcCrash, (unsigned)g_scanRestarts, (unsigned)g_stall, (unsigned)g_lockTimeouts,
             g_started ? (long)(((int32_t)(now - g_lastAdv) > 0 ? now - g_lastAdv : 0) / 1000) : -1L, ESP.getFreeHeap(), (unsigned long)(now / 1000));
    pub(topicBase() + "/ble", b);
}

// No advert for BLE_STALL_MS although the scan should be running: restart the scan, then the whole
// NimBLE stack, and as a last resort the chip (never earlier than 15 min after boot).
void watchdog(uint32_t now)
{
    g_scanRestarts++;
    g_stall++;
    Serial.printf("[BLE] watchdog: no advert for %lu s, kick %u\\n", (unsigned long)((now - g_ref) / 1000), (unsigned)g_stall);
    NimBLEScan *scan = NimBLEDevice::getScan();
    if (g_stall <= 3)
    {
        scan->stop();
        startScan();
    }
    else if (g_stall <= 9 || now < 15UL * 60 * 1000)
    {
        scan->stop();
        NimBLEDevice::deinit(true);
        g_started = false;
        g_state = ST_OFF;
        bleStart();
    }
    else if (g_rtcWd < 3)
    {
        g_rtcWd++;
        Serial.println("[BLE] watchdog: restarting the chip");
        delay(100);
        ESP.restart();
    }
    else
    {
        scan->stop();
        NimBLEDevice::deinit(true);
        g_started = false;
        g_state = ST_OFF;
        bleStart();
    }
    g_ref = millis(); // next check one full interval later
}
} // namespace

#ifdef BLE_PUBLISH_FN_API
void setup(BlePublishFn publish)''')

# 6. loop
rep('''void loop()
{
    if (!g_started)
        return;
    const uint32_t now = millis();
    if (now - g_lastPublish < BLE_PUBLISH_MS)
        return;
    g_lastPublish = now;
''', '''void loop()
{
    const uint32_t now = millis();
    if (!g_started)
    {
        if ((g_state == ST_DEFER || g_state == ST_INITFAIL) && (int32_t)(now - g_startAt) >= 0)
            bleStart();
        if (g_state != ST_OFF)
            publishStatus(now);
        return;
    }
    publishStatus(now);
    if ((g_rtcCrash || g_rtcWd) && now - g_runSince > 30UL * 60 * 1000)
        g_rtcCrash = g_rtcWd = 0;
    if (now - g_lastPublish < BLE_PUBLISH_MS)
        return;
    g_lastPublish = now;
''')

rep('''    else if (g_enabled && !scan->isScanning() && freeHeap > BLE_MIN_FREE_HEAP + 10000)
        startScan();
''', '''    else if (g_enabled && !scan->isScanning() && freeHeap > BLE_MIN_FREE_HEAP + 10000)
    {
        startScan();
        if (!scan->isScanning() && ++g_noScan >= 20) // start keeps failing: same escalation as a silent scan
        {
            g_noScan = 0;
            watchdog(now);
            return;
        }
    }
    else if (g_enabled && scan->isScanning() && (int32_t)(now - lastSeen()) > (int32_t)BLE_STALL_MS)
    {
        watchdog(now);
        return;
    }
    if (scan->isScanning())
        g_noScan = 0;
    if (g_stall && (int32_t)(g_lastAdv - g_ref) > 0)
        g_stall = 0; // adverts are flowing again
''')

rep('''    int irkCount, staticN;
    LOCK();
    apple = g_apple;''', '''    int irkCount, staticN;
    if (!TRYLOCK(200))
    {
        g_lockTimeouts++;
        return;
    }
    apple = g_apple;''')

rep('''"],\\"heap\\":%u,\\"up\\":%lu}", ESP.getFreeHeap(), (unsigned long)(now / 1000));
#ifdef BLE_PUBLISH_FN_API
    g_publish((topicBase() + "/apple").c_str(), buf, false);
#else
    g_mqtt->publish((topicBase() + "/apple").c_str(), buf, false);
#endif
}''', '''"],\\"heap\\":%u,\\"up\\":%lu,\\"rst\\":%u}", ESP.getFreeHeap(), (unsigned long)(now / 1000), (unsigned)g_scanRestarts);
    pub(topicBase() + "/apple", buf);
}''')

open(p, 'wb').write(s.replace('\n', '\r\n').encode())
print('patched', p)
