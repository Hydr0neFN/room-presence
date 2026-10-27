"""Weak zone labels from passive context (pure function, stdlib; used by zone_log.py, tested by test_weak_label.py).

classify(c) -> (label, fired)   label is "" when unsure; fired = list of rule names that matched.
Conservative by design: a rule needs its own evidence, two different zones firing = conflict = "".

Input dict c (None / "" = unknown; unknown evidence never fires a rule):
  home          "on"/"off"        binary_sensor.yui_home ("off" -> no label: away)
  minute        0..1439           local minute of day
  pc_online     "on"/"off"        binary_sensor.rp_pc_online
  pc_locked     "on"/"off"        binary_sensor.rp_pc_locked
  pc_idle       seconds           sensor.rp_pc_idle
  radar         "on"/"off"        kitchen radar presence
  radar_cm      cm                kitchen radar detection distance
  power_max120  W                 max powermon watts over the last 120 s
  wd_light      "on"/"off"        light.channel_b (work desk)
  wd_light_age  seconds           time since light.channel_b last changed
  wd_phone      dBm               workdesk node phone RSSI, last 10 s mean
  wd_phone_med  dBm               rolling (6 h) median of wd_phone, needs >= MIN_MED_N samples (else None)
  phone_sd_10m  dB                largest stdev over the last 10 min of the phone RSSI of the nodes that heard it in
                                  >= 2/3 of the windows; None if no node qualifies
Rules (zone: condition):
  pc_desk   PC online and unlocked and idle < 60 s                               (same as yui_room_zone's PC rule)
  kitchen   radar on and < 250 cm, OR powermon >= 1000 W within the last 2 min   (the 1000 W branch needs the board on the meter)
  work_desk light.channel_b on AND the PC is inactive AND the workdesk-node phone corroborates:
              A  idle >= 300 s (or locked) and wd_phone > rolling median
              B  light switched <= 10 min ago, idle >= 60 s (or locked) and wd_phone >= median - 3 dB
            A light that is on alone never fires (the owner may leave it on).
  bed       00:30-07:30, PC locked, radar off, home, phone RSSI stable (stdev <= 3 dB over 10 min)
"""
PC_ACTIVE_IDLE_S = 60
WD_IDLE_A_S = 300
WD_LIGHT_RECENT_S = 600
KITCHEN_MAX_CM = 250
KITCHEN_POWER_W = 1000
BED_FROM_MIN = 30           # 00:30
BED_TO_MIN = 7 * 60 + 30    # 07:30
BED_STABLE_SD_DB = 3.0
WD_B_MARGIN_DB = 3.0
MIN_MED_N = 30


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pc_active(c):
    idle = _num(c.get("pc_idle"))
    return c.get("pc_online") == "on" and c.get("pc_locked") == "off" and idle is not None and idle < PC_ACTIVE_IDLE_S


def pc_inactive(c, idle_s):
    """PC online with a known state and locked, or idle for at least idle_s."""
    if c.get("pc_online") != "on" or c.get("pc_locked") not in ("on", "off"):
        return False
    idle = _num(c.get("pc_idle"))
    return c.get("pc_locked") == "on" or (idle is not None and idle >= idle_s)


def classify(c):
    if c.get("home") == "off":
        return "", []
    fired = []
    if pc_active(c):
        fired.append("pc_desk")
    cm, w = _num(c.get("radar_cm")), _num(c.get("power_max120"))
    if (c.get("radar") == "on" and cm is not None and cm < KITCHEN_MAX_CM) or (w is not None and w >= KITCHEN_POWER_W):
        fired.append("kitchen")
    ph, med = _num(c.get("wd_phone")), _num(c.get("wd_phone_med"))
    age = _num(c.get("wd_light_age"))
    if c.get("wd_light") == "on" and ph is not None and med is not None:
        a = pc_inactive(c, WD_IDLE_A_S) and ph > med
        b = age is not None and age <= WD_LIGHT_RECENT_S and pc_inactive(c, PC_ACTIVE_IDLE_S) and ph >= med - WD_B_MARGIN_DB
        if a or b:
            fired.append("work_desk")
    m, sd = _num(c.get("minute")), _num(c.get("phone_sd_10m"))
    if (m is not None and BED_FROM_MIN <= m < BED_TO_MIN and c.get("pc_online") == "on" and c.get("pc_locked") == "on"
            and c.get("radar") == "off" and c.get("home") == "on" and sd is not None and sd <= BED_STABLE_SD_DB):
        fired.append("bed")
    return (fired[0] if len(fired) == 1 else ""), fired
