Root cause: both markdown cards used `content: >-` (folded scalar), which folds newlines between table rows into spaces. Fixed with `|-` (literal).
files_changed: nl-pi:/opt/homeassistant/config/dashboards/house.yaml (backup house.yaml.bak-20261030-debugview). Dashboard only, no template sensor changed, no zone logic changed. File was actually LF (0 CR), not CRLF; line endings preserved.
Also: RSSI <= -127 (or non-numeric) shows —; CSI unknown/unavailable/none/empty shows —. Applies on browser refresh.
commands_run:
- docker exec homeassistant python -c "yaml.safe_load(house.yaml)" | 0 | yaml ok
- docker exec homeassistant python -m homeassistant --script check_config -c /config | 0 (rc of tail) | "Testing configuration at /config", no errors
- jinja render of the two cards (offline, states from recorder values: pcdesk/workdesk -127, nlpi csi unknown) | 0 | rows on separate lines: "| pcdesk | — | 是 | 25 |", "| nlpi | -72.0 | 否 | — |", separator line on its own line; 證據旗標 table also multi-line
tests_modified: false
not_verified: live browser render (needs owner refresh); rendering used jinja2 with stubbed states()/is_state() not the HA template engine (recorder DB queries too slow, >60s)
assumptions: patch appended to /Users/ruanyouyi/rpi4/room-presence-debugview-20261030.patch (paths a/homeassistant/config/dashboards/house.yaml). I removed my own temp scripts /tmp/{dbgfix,render,render2}.py on nl-pi.
