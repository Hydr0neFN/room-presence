import sys; sys.path.insert(0,'/tmp/csi-work')
from exp2 import *
labels=[cf.weak_from_row(r, r['t'].hour) for r in rows]
day=[r['t'].day for r in rows]
for wh,q in ((8,.1),(8,.15),(6,.15),(10,.1)):
    data=build(rows,window_s=wh*3600,loc_q=q)
    zz=smoothz(data,6)
    for w in (0,.15,.25,.5,1):
        wt={f"{n}_motion":w for n in cf.NODES}
        lodo(zz,labels,day,lambda:cf.ZoneModel(weights=wt),name=f'w{wh} q{q} motion weight {w}')
