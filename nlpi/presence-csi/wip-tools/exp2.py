import sys; sys.path.insert(0,'/tmp/csi-work')
from exp1 import *
import random
def smoothz(data,k):
    if k<=1: return [d[2] for d in data]
    out=[];buf=collections.deque(maxlen=k)
    for d in data:
        buf.append(d[2]); acc={}
        for zv in buf:
            for n,v in zv.items(): acc.setdefault(n,[]).append(v)
        out.append({n:sum(v)/len(v) for n,v in acc.items()})
    return out
class GNB:
    def fit(self,samples,vfloor=0.25):
        by=collections.defaultdict(list)
        for z,zv in samples: by[z].append(zv)
        self.p={}
        for z,vs in by.items():
            m={};v={}
            for n in {n for zv in vs for n in zv}:
                col=[zv[n] for zv in vs if n in zv]
                mu=sum(col)/len(col); var=sum((x-mu)**2 for x in col)/len(col)
                m[n]=mu; v[n]=max(var,vfloor)
            self.p[z]=(m,v)
        return self
    def dist(self,zv):
        out={}
        for z,(m,v) in self.p.items():
            s=0;k=0
            for n,x in zv.items():
                if n in m: s+=(x-m[n])**2/v[n]+math.log(v[n]); k+=1
            if k: out[z]=s/k
        return out,[]
class KNN:
    def __init__(self,k=25): self.k=k
    def fit(self,samples,cap=1500):
        by=collections.defaultdict(list)
        for z,zv in samples: by[z].append(zv)
        rnd=random.Random(1); self.s=[]
        for z,vs in by.items():
            if len(vs)>cap: vs=rnd.sample(vs,cap)
            self.s+= [(z,v) for v in vs]
        return self
    def dist(self,zv):
        ds=[]
        for z,v in self.s:
            a=0;k=0
            for n,x in zv.items():
                if n in v: a+=(x-v[n])**2;k+=1
            if k: ds.append((a/k,z))
        ds.sort(); top=ds[:self.k]; c=collections.Counter(z for _,z in top)
        return {z:1-c.get(z,0)/self.k for z in cf.ZONES if z in {s[0] for s in self.s}},[]
def lodo(zs,labels,day,cls,tests=(28,29,30),name='',sub=None):
    tot=collections.Counter()
    for hold in tests:
        tr=[(labels[i],zs[i]) for i in range(len(zs)) if day[i]!=hold and labels[i] in cf.ZONES and zs[i]]
        m=cls().fit(tr)
        for i in range(len(zs)):
            if day[i]==hold and labels[i] in cf.ZONES and zs[i] and (sub is None or sub(i)):
                dd,_=m.dist(zs[i]); p,_=cf.decide(dd); tot[(labels[i],p)]+=1
    return report(tot,name)
if __name__=='__main__':
    labels=[cf.weak_from_row(r, r['t'].hour) for r in rows]
    day=[r['t'].day for r in rows]
    for bname,kw in [('w24q.2',{}),('w6q.2',{'window_s':21600}),('w12q.2',{'window_s':43200}),('w24q.1',{'loc_q':.1}),('w12q.1',{'window_s':43200,'loc_q':.1})]:
        data=build(rows,**kw)
        for k in (1,6):
            zs=smoothz(data,k)
            for cname,cls in [('nc',lambda:cf.ZoneModel()),('nc.5',lambda:cf.ZoneModel(sd_floor=.5)),('gnb',GNB)]:
                lodo(zs,labels,day,cls,name=f'{bname} sm{k} {cname}')
