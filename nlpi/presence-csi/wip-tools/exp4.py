import sys; sys.path.insert(0,'/tmp/csi-work')
from exp2 import *
labels=[cf.weak_from_row(r, r['t'].hour) for r in rows]
day=[r['t'].day for r in rows]
data=build(rows,window_s=8*3600,loc_q=.1)
zz=smoothz(data,6)
def aug(zv):
    o=dict(zv)
    st=[v for k,v in zv.items() if k.endswith('still')]; mo=[v for k,v in zv.items() if k.endswith('motion')]
    if st: o['agg_still_max']=max(st); o['agg_still_mean']=sum(st)/len(st)
    if mo: o['agg_motion_mean']=sum(mo)/len(mo)
    return o
za=[aug(z) for z in zz]
lodo(zz,labels,day,lambda:cf.ZoneModel(),name='w8 q.1 sm6 nc')
lodo(za,labels,day,lambda:cf.ZoneModel(),name='  +aggregates')
# only still / only motion
lodo([{k:v for k,v in z.items() if k.endswith('still')} for z in zz],labels,day,lambda:cf.ZoneModel(),name='  still only')
lodo([{k:v for k,v in z.items() if k.endswith('motion')} for z in zz],labels,day,lambda:cf.ZoneModel(),name='  motion only')
# sm 3, 12
for k in (3,12,18):
    lodo(smoothz(data,k),labels,day,lambda:cf.ZoneModel(),name=f'  sm{k}')
# night / day split for sm6
night=lambda i: rows[i]['t'].hour>=23 or rows[i]['t'].hour<7
lodo(zz,labels,day,lambda:cf.ZoneModel(),name='  night only',sub=night)
lodo(zz,labels,day,lambda:cf.ZoneModel(),name='  day only',sub=lambda i:not night(i))
# per-day
for d in (28,29,30):
    lodo(zz,labels,day,lambda:cf.ZoneModel(),tests=(d,),name=f'  test day {d}')
