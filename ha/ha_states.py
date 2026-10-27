"""Read latest states from the HA recorder DB (read-only). Usage (on nl-pi):
  ssh nl-pi-lan "docker exec -i homeassistant python - rp_ yui_ room_zone" < ha/ha_states.py
Arguments are substrings of entity ids (SQL LIKE %arg%). The recorder commits every ~5 s."""
import sqlite3
import sys

pats = sys.argv[1:] or ["rp_"]
c = sqlite3.connect("file:/config/home-assistant_v2.db?mode=ro", uri=True)
where = " or ".join(["m.entity_id like ?"] * len(pats))
q = ("select m.entity_id, s.state from states_meta m join states s on s.metadata_id=m.metadata_id "
     "where s.state_id=(select max(state_id) from states where metadata_id=m.metadata_id) and (" + where + ") order by 1")
for r in c.execute(q, ["%" + p + "%" for p in pats]):
    print(*r)
