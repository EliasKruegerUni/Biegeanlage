"""Erzeugt ../biegbert_geometry.js aus ORIG_Biegbert_Gesamt.STEP.
pip install cadquery-ocp trimesh fast-simplification numpy
python prep_vis.py      (STEP-Pfad optional per Umgebungsvariable BIEGBERT_STEP)
"""
import os
import re, json, base64, numpy as np, trimesh, fast_simplification
from lib import *
st=load(); parts=collect(st)

EXCL=re.compile(r"screw|DIN 912|DIN-912|DIN 7991|ISO 14579|F-M[23]x|hex nut|parallel|DIN EN ISO 8735|DIN 6885|ZBH-12|VFOE|OL-1_4|QS-G|QSM-M5|B-M5-B|VAME-T|ASLR|Ringschraube|Intercontec|Schraube DIN912|Wuerfelverbinder|Winkel 20x20", re.I)

PIV_A=[0,0,-415.6098]; TIP_Z2=[0,0,-395.6098]; STOP_Z1=[0,0,-1695.1098]
def classify(p):
    pn=p['ppath'][-1]; anc=p['ppath'][1:-1]; top=anc[0] if anc else None; ip="/".join(p['ipath'])
    if EXCL.search(pn) or any(EXCL.search(a) for a in anc): return None
    has=lambda s: s in pn or any(s in a for a in anc)
    if top=="ORIG_Eisenschwein":
        if pn=="ORIG_Eisenschwein_Einsatz": return ("Z2","Fuehrungshuelse (Bronze)",1,"bronze")
        if pn in("ORIG_Eisenschwein","ORIG_Eisenschwein_Flansch1","ORIG_Eisenschwein_Platte","ORIG_Eisenschwein_Positionierkeil"): return ("Z2",pn,2,"steel")
        if pn in("ORIG_Eisenschwein_Gewindemutter","ORIG_Eisenschwein_Gewindemutter_Halterung") or has("CGH30") or has("Laserdistanz"): return ("Z2",pn,3,"mech")
        return ("fix",pn,3,"drive" if (has("Motor_F2") or has("Winkelgetriebe")) else "mech")
    if top=="ORIG_Vorschub":
        if pn=="Röhm_Duro80.stp": return ("W","Futter Röhm Duro-M 80",1,"chuck")
        if pn=="ORIG_4BF_Adapterwelle": return ("W","Adapterwelle",1,"contact_steel")
        if pn=="ORIG_W_Wellenmutter_skf_nut_kmk_5": return ("W",pn,3,"mech")
        if pn in("ORIG_Platte_0","ORIG_Vorschub_4BF_Motorhalterung","ORIG_Vorschub_Positionierkeil","ORIG_W_Lagerdeckel"): return ("Z1",pn,2,"steel")
        if has("Z1_HIWIN_Wagen") or has("ORIG_Beckhoff_Motor_4BF") or has("4BF_Beckhoff_Getriebe") or pn in("ORIG_Vorschub_Laserblech","ORIG_W_Schrägkugellager_3306-bd-xl-2hrs-tvh","GG_Trapezmutter711-044-6800.stp"):
            return ("Z1",pn,3,"drive" if (has("Motor_4BF") or has("Getriebe")) else "mech")
        return ("fix",pn,3,"drive" if (has("Z-Motor") or has("SYNCHRON")) else "mech")
    if top=="ILIAS_Biegekopf_neu":
        if pn=="Matrizeneinsatz": return ("A","Matrize (AMPCO)",1,"bronze")
        if pn=="Matrizenhalterung": return ("A",pn,2,"steel")
        if pn=="Gestell_Biegekopf_Platte": return ("fix",pn,4,"frame")
        if pn in("X-Platte","Matrizenaufnahme_Oben1","Matrizenlagerdeckel_oben","Matrizenaufnahme_Unten","Matrizenlagerdeckel_unten"): return ("X",pn,2,"steel")
        if "AACGH20" in pn and "AAAACGR20" not in pn: return ("X",pn,3,"mech")
        if has("biegekopf unten") or pn in("X-Spindelmutterhalter","X-Achse_Spindelmutter_711-040-6800.stp"):
            return ("X",pn,3,"drive" if (has("B-Achse")) else "mech")
        return ("fix",pn,3,"drive" if (has("X-Achse_Motor") or has("Planetengetriebe")) else "mech")
    if top=="Niederhalter":
        k={"NAUO8":1,"NAUO9":2,"NAUO10":3}[p['ipath'][1]]
        side="L" if ("NAUO45" in ip or "NAUO47" in ip) else "R"
        node=f"NH{k}_{side}"
        if pn=="Niederhalter_Platte": return (node,f"Niederhalter {k}{side} Platte (POM)",1,"pom")
        if pn.endswith("(K)"): return (node,"DFM-50 Kolben/Joch",2,"alu")
        if pn.endswith("(Z)"): return ("fix","DFM-50 Gehäuse",2,"alu")
        return None
    if top=="stuetzelement assembly": return ("fix",pn,3,"alu")
    if top and ("Ventilinsel" in top): return ("fix",pn,4,"pneu")
    if pn=="Ilias-UpperTable" and p['ipath'][1]=="NAUO11": return None   # Duplikat
    if pn in("Ilias-UpperTable","Ilias-TableSkelet","platte links","platte rechts"): return ("fix",pn,4,"frame")
    if pn=="ILIAS_CGR15R_Schiene": return ("fix",pn,3,"mech")
    if pn=="Spindellagerhalterung_Vorne": return ("fix",pn,3,"mech")
    print("UNCLASSIFIED",pn,anc); return None

# node origins (CAD mm, global)
NODE_ORIGIN={"fix":[0,0,0],"Z1":STOP_Z1,"W":STOP_Z1,"Z2":TIP_Z2,"X":PIV_A,"A":PIV_A}
TARGET={1:3500,2:900,3:500,4:900}
SPECIAL={'Alu-Profil':120,'Edelstahlplatte':60,'Ilias-TableSkelet':2500}
geoms={}; insts=[]; stats={}
def b64(a): return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
for p in parts:
    c=classify(p)
    if not c: continue
    node,label,lvl,mat=c
    M=trsf_matrix(p['loc'])
    prod=p['ppath'][-1]
    # node origin for NH: plate centre computed later -> use fixed origin per NH from plate
    gkey=prod
    if gkey not in geoms:
        s=p['shape']
        from OCP.Bnd import Bnd_Box; from OCP.BRepBndLib import BRepBndLib
        b=Bnd_Box(); BRepBndLib.Add_s(s,b); d=b.CornerMin().Distance(b.CornerMax())
        if lvl==1: V,F=mesh(s, max(0.03,d*0.0015), 0.30)
        else:      V,F=mesh(s, max(0.2,d*0.006), 0.55)
        tgt=TARGET[lvl]
        for k,v in SPECIAL.items():
            if prod.startswith(k): tgt=v
        if lvl==1 and len(F)>tgt:
            mm=trimesh.Trimesh(V,F,process=True)
            V,F=fast_simplification.simplify(mm.vertices.astype(np.float32),mm.faces.astype(np.int32),target_reduction=1-tgt/len(mm.faces))
            print("  L1 decim:",prod,len(mm.faces),"->",len(F))
        if len(F)>4*tgt and lvl>=3:
            mm=trimesh.Trimesh(V,F,process=True)
            if len(F)>8*tgt:
                h=mm.convex_hull; V2,F2=h.vertices,h.faces; print("  hull:",prod)
            else: V2,F2=V,F
            V,F=V2,F2
        m=trimesh.Trimesh(V,F,process=True)
        m=trimesh.graph.smooth_shade(m,angle=np.radians(35))
        geoms[gkey]=dict(v=b64(m.vertices.astype(np.float32)),n=b64(np.clip(np.round(m.vertex_normals*127),-127,127).astype(np.int8)),
                         i=b64(m.faces.astype(np.uint32 if len(m.vertices)>65535 else np.uint16)),i32=len(m.vertices)>65535,
                         nv=len(m.vertices),nt=len(m.faces))
    insts.append(dict(g=gkey,node=node,label=label,lvl=lvl,mat=mat,M=M,det=float(np.linalg.det(M[:3,:3]))))
# NH origins = plate bbox centre (x,0,z) from instance translation (plate geometry origin)
for k in (1,2,3):
    for side in "LR":
        pl=[i for i in insts if i['node']==f"NH{k}_{side}" and i['lvl']==1][0]
        NODE_ORIGIN[f"NH{k}_{side}"]=[float(pl['M'][0,3]),0.0,float(pl['M'][2,3])]
def gorig(n):
    if n=="W": return np.array(STOP_Z1)
    if n=="A": return np.array(PIV_A)
    return np.array(NODE_ORIGIN[n])
out_inst=[]
for i in insts:
    M=i['M'].copy(); M[:3,3]-=gorig(i['node'])
    out_inst.append(dict(g=i['g'],node=i['node'],label=i['label'],lvl=i['lvl'],mat=i['mat'],
        m=[round(float(x),5) for x in M.T.flatten()], mirror=i['det']<0))
data=dict(source="ORIG_Biegbert_Gesamt.STEP (SolidWorks 2026, Export 03.11.2026)",units="mm",
          nodeOrigin={k:[round(float(x),4) for x in v] for k,v in NODE_ORIGIN.items()},geoms=geoms,instances=out_inst)
js="/* Automatisch erzeugt aus ORIG_Biegbert_Gesamt.STEP – nicht von Hand bearbeiten */\nwindow.BIEGBERT_GEOMETRY="+json.dumps(data,separators=(",",":"),ensure_ascii=False)+";\n"
open(os.path.join(os.path.dirname(__file__),"..","biegbert_geometry.js"),"w",encoding="utf-8").write(js)
import collections
cnt=collections.Counter(); tri=collections.Counter()
for i in out_inst: cnt[i['lvl']]+=1; tri[i['lvl']]+=geoms[i['g']]['nt']
print("instances per level",dict(cnt)); print("tris per level",dict(tri)); print("geoms",len(geoms),"size MB",len(js)/1e6)
print("mirrored",sum(i['mirror'] for i in out_inst))
for n in sorted(set(i['node'] for i in out_inst)): print(n, sorted(set(i['label'] for i in out_inst if i['node']==n))[:12])
