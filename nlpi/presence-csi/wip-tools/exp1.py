import sys; sys.path.insert(0,'/tmp/csi-work')
from load import load, num, NODES
import csi_features as cf, collections, statistics, datetime, time, math
rows=load()
def ts(r): return r['t'].timestamp()
def build(rows, **kw):
    bl=cf.Baseline(**kw); out=[]
    for r in rows:
        t=ts(r); raw={}
        for n in cf.NODES:
            for k in cf.KINDS:
                v=num(r[f'csi_{n}_{k}']); raw[f'{n}_{k}']=v
        bl.tick(t)
        z=bl.zvec(raw)
        for nm,v in raw.items(): bl.add(t,nm,v)
        out.append((r,raw,z))
    return out
def evalcls(data, labels, trainmask, testmask, use='z', **mk):
    tr=[(labels[i], d[2] if use=='z' else d[1]) for i,d in enumerate(data) if trainmask(i) and labels[i] in cf.ZONES and (d[2] if use=='z' else d[1])]
    m=cf.ZoneModel(**mk).fit(tr)
    conf=collections.Counter()
    for i,d in enumerate(data):
        if testmask(i) and labels[i] in cf.ZONES:
            zv=d[2] if use=='z' else d[1]
            zv={k:v for k,v in zv.items() if v is not None}
            if not zv: continue
            dd,_=m.dist(zv); p,_=cf.decide(dd)
            conf[(labels[i],p)]+=1
    return conf
def report(conf, name):
    zs=sorted({a for a,_ in conf}); tot=sum(conf.values())
    acc=sum(v for (a,b),v in conf.items() if a==b)/max(tot,1)
    rec={z: sum(v for (a,b),v in conf.items() if a==z and b==z)/max(sum(v for (a,b),v in conf.items() if a==z),1) for z in zs}
    bal=sum(rec.values())/len(rec)
    print(f'{name:28s} n={tot:5d} acc={acc:.3f} bal={bal:.3f} '+' '.join(f'{z[:4]}={r:.2f}' for z,r in rec.items()))
    return acc,bal
if __name__=='__main__':
    labels=[cf.weak_from_row(r, r['t'].hour) for r in rows]
    day=[r['t'].day for r in rows]
    for name,kw in [('base w24 q.2',{}),('w12',{'window_s':43200}),('w6',{'window_s':21600}),('q.5',{'loc_q':.5}),('q.1',{'loc_q':.1}),('w48',{'window_s':172800})]:
        data=build(rows,**kw)
        for hold in (28,29,30):
            report(evalcls(data,labels,lambda i:day[i]<hold and True,lambda i:day[i]==hold),f'{name} train<{hold} test={hold}')
    # raw baseline-free features
    data=build(rows)
    # raw: use raw feature values
    for hold in (28,29,30):
        report(evalcls(data,labels,lambda i:day[i]<hold,lambda i:day[i]==hold,use='raw',sd_floor=3.0),f'RAW train<{hold} test={hold}')
