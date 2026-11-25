import sys, math
import numpy as np
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TDF import TDF_Label
from OCP.collections import Sequence_TDF_Label as Seq
from OCP.TDataStd import TDataStd_Name
from OCP.TopLoc import TopLoc_Location
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS
import os
STEP=os.environ.get("BIEGBERT_STEP", os.path.join(os.path.dirname(__file__), "..", "..", "00_CAD Modell", "ORIG_Biegbert_Gesamt.STEP"))
_KEEP=[]
def load():
    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    r = STEPCAFControl_Reader(); r.SetNameMode(True); r.SetColorMode(True)
    r.ReadFile(STEP); r.Transfer(doc); _KEEP.append((doc,r))
    return XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
def name(l):
    n = TDataStd_Name()
    return n.Get().ToExtString() if l.FindAttribute(TDataStd_Name.GetID_s(), n) else "?"
def collect(st):
    """returns list of dict(path=[inst names], prod=[product names], shape_local, loc)"""
    out=[]
    def walk(lab, loc, ipath, ppath):
        if st.IsReference_s(lab):
            ref=TDF_Label(); st.GetReferredShape_s(lab, ref)
            loc=loc.Multiplied(st.GetLocation_s(lab)); inst=name(lab); lab=ref
        else: inst=name(lab)
        prod=name(lab); ip=ipath+[inst]; pp=ppath+[prod]
        if st.IsAssembly_s(lab):
            s=Seq(); st.GetComponents_s(lab,s)
            for i in range(1,s.Length()+1): walk(s.Value(i),loc,ip,pp)
        else:
            out.append(dict(ipath=ip,ppath=pp,shape=st.GetShape_s(lab),loc=loc))
    roots=Seq(); st.GetFreeShapes(roots)
    for i in range(1,roots.Length()+1): walk(roots.Value(i),TopLoc_Location(),[],[])
    return out
def trsf_matrix(loc):
    t=loc.Transformation(); M=np.eye(4)
    for i in range(3):
        for j in range(4): M[i,j]=t.Value(i+1,j+1)
    return M
def mesh(shape, lin=0.05, ang=0.3):
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    V=[];F=[];off=0
    ex=TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        f=TopoDS.Face(ex.Current()); l=TopLoc_Location()
        tri=BRep_Tool.Triangulation_s(f,l)
        if tri is not None:
            tr=l.Transformation()
            for i in range(1,tri.NbNodes()+1):
                p=tri.Node(i).Transformed(tr); V.append((p.X(),p.Y(),p.Z()))
            rev = f.Orientation()==TopAbs_REVERSED
            for i in range(1,tri.NbTriangles()+1):
                a,b,c=tri.Triangle(i).Get()
                F.append((off+a-1,off+c-1,off+b-1) if rev else (off+a-1,off+b-1,off+c-1))
            off+=tri.NbNodes()
        ex.Next()
    return np.array(V),np.array(F,dtype=np.int64)
