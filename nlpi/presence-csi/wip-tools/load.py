import csv,glob,statistics,collections,datetime
NODES=['cyd','kitchen','pcdesk','workdesk']
FEATS=[f'csi_{n}_{k}' for n in NODES for k in ('motion','still')]
def num(x):
    try: return float(x)
    except: return None
def load():
    rows=[]
    for f in sorted(glob.glob('/tmp/csi-work/zone-v3-*.csv')):
        for r in csv.DictReader(open(f)):
            r['t']=datetime.datetime.fromisoformat(r['ts']); rows.append(r)
    return rows
def weak(r):
    """weak label or None"""
    if r['home']=='off': return 'away'
    idle=num(r['pc_idle']); rad=r['radar']=='on'; rcm=num(r['radar_cm'])
    wdage=num(r['wd_light_age_s']); h=r['t'].hour
    if r['pc_online']=='on' and r['pc_locked']=='off' and idle is not None and idle<=60: return 'pc_desk'
    pcquiet = (idle is None) or idle>=300
    if rad and (rcm is None or rcm<=150) and pcquiet: return 'kitchen'
    if r['wd_light']=='on' and wdage is not None and wdage<=900 and not rad and pcquiet: return 'work_desk'
    if (h>=23 or h<7) and (idle is None or idle>=1800) and not rad and r['wd_light']!='on' and r['kitchen_light']!='on': return 'bed'
    return None
