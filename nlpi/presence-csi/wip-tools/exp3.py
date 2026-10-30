import sys; sys.path.insert(0,'/tmp/csi-work')
from exp2 import *
labels=[cf.weak_from_row(r, r['t'].hour) for r in rows]
day=[r['t'].day for r in rows]
# BLE fp / live vs weak
for col in ('fp_zone','zone'):
    conf=collections.Counter((labels[i],r[col]) for i,r in enumerate(rows) if labels[i] in cf.ZONES and r[col] in cf.ZONES and day[i]!=27)
    report(conf,'BLE '+col+' vs weak (d28-30)')
# static raw normalisation
stat={}
for n in cf.NAMES:
    v=sorted(cf.g(num(r['csi_'+n])) for r in rows if num(r['csi_'+n]) is not None)
    med=cf.quantile(v,.5); mad=cf.quantile(sorted(abs(x-med) for x in v),.5)*1.4826
    stat[n]=(med,max(mad,.15))
zs=[]
for r in rows:
    zv={}
    for n in cf.NAMES:
        x=num(r['csi_'+n])
        if x is not None: zv[n]=(cf.g(x)-stat[n][0])/stat[n][1]
    zs.append(zv)
lodo(zs,labels,day,lambda:cf.ZoneModel(),name='STATIC-global-norm nc')
res=[]
for wh in (2,3,4,6,8):
    for q in (.1,.2,.3):
        data=build(rows,window_s=wh*3600,loc_q=q,min_n=min(360,wh*3600//10//2))
        zz=smoothz(data,6)
        a,b=lodo(zz,labels,day,lambda:cf.ZoneModel(),name=f'w{wh}h q{q} sm6 nc')
