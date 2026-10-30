import sys; sys.path.insert(0,'/tmp/csi-work')
import sqlite3,collections,math
import csi_features as cf
c=sqlite3.connect('/tmp/csi-work/labels.db')
L=list(c.execute("select id,zone,ts from labels order by id"))
S=collections.defaultdict(dict)
for lid,node,ph,csim,csis in c.execute("select label_id,node,phone_avg,csi_motion,csi_still from samples"):
    S[lid][node]=(ph,csim,csis)
prev=None;g=0;grp={}
for i,z,t in L:
    if prev is None or z!=prev[0] or t-prev[1]>40: g+=1
    grp[i]=g; prev=(z,t)
zone={i:z for i,z,_ in L}
nodes=['cyd','kitchen','nlpi','pcdesk','workdesk']
def feats(kind):
    out={}
    for lid in zone:
        v={}
        for n in nodes:
            ph,m,s=S[lid].get(n,(None,None,None))
            if kind in('ble','both'): v['ble_'+n]=(ph if ph is not None else -100)/10.0
            if kind in('csi','both','csistill') and n!='nlpi':
                if kind!='csistill' and m is not None: v['m_'+n]=cf.g(m)
                if s is not None and s>=0: v['s_'+n]=cf.g(s)
        out[lid]=v
    return out
def norm(F):
    names={n for v in F.values() for n in v}
    st={}
    for n in names:
        vals=sorted(v[n] for v in F.values() if n in v); med=cf.quantile(vals,.5); mad=cf.quantile(sorted(abs(x-med) for x in vals),.5)*1.4826
        st[n]=(med,max(mad,.15))
    return {i:{n:(x-st[n][0])/st[n][1] for n,x in v.items()} for i,v in F.items()}
def cv(F,name,w=None):
    Z=norm(F); ok=0;tot=0;cm=collections.Counter()
    for lid in Z:
        tr=[(zone[j],Z[j]) for j in Z if grp[j]!=grp[lid]]
        m=cf.ZoneModel(weights=w).fit(tr); d,_=m.dist(Z[lid]); p,_=cf.decide(d)
        ok+=p==zone[lid]; tot+=1; cm[(zone[lid],p)]+=1
    print(name,'burst-LOO acc',round(ok/tot,3),'n',tot)
    return cm
for k in ('ble','csi','csistill','both'): cv(feats(k),k)
# random LOO (as likely in earlier analysis) for reference
def loo(F,name):
    Z=norm(F);ok=0
    for lid in Z:
        tr=[(zone[j],Z[j]) for j in Z if j!=lid]
        m=cf.ZoneModel().fit(tr); d,_=m.dist(Z[lid]); p,_=cf.decide(d); ok+=p==zone[lid]
    print(name,'plain LOO',round(ok/len(Z),3))
for k in ('ble','csi','csistill','both'): loo(feats(k),k)
