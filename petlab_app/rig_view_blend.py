"""Registered, display-only material handoff between authored rig views.

Registration lives in the asset, in part-local coordinates. Both endpoints keep
sampling the same action/expression clock; no neutral frame is inserted.
"""
import cv2
import numpy as np
from .rig_activity import RigFrame
from .cutout_binding import matrices, render
from .windows_alpha import AlphaFrame


def validate(data,views):
    known={p['id'] for p in views[0]['parts']}
    if set(data)!={'-1','1'}:raise ValueError('incomplete turn registration')
    for direction,rows in data.items():
        if set(rows)!=known:raise ValueError('incomplete turn parts')
        for row in rows.values():
            if set(row)!={'target','source','destination','triangles','handoff'} or row['target'] not in known:
                raise ValueError('invalid turn registration part')
            a=np.asarray(row['source']);b=np.asarray(row['destination']);tri=np.asarray(row['triangles'])
            if a.shape!=b.shape or a.ndim!=2 or a.shape[1]!=2 or not 3<=len(a)<=64 or not np.isfinite(a).all() or not np.isfinite(b).all():
                raise ValueError('invalid turn landmarks')
            if tri.ndim!=2 or tri.shape[1]!=3 or not np.issubdtype(tri.dtype,np.integer) or tri.min()<0 or tri.max()>=len(a):raise ValueError('invalid turn triangles')
            if not 0<row['handoff']<1:raise ValueError('invalid turn material handoff')


def _world(points, matrix):
    p=np.asarray(points,dtype=np.float32)
    return (matrix @ np.column_stack((p,np.ones(len(p)))).T).T[:,:2].astype(np.float32)


def _maps(src,dst,triangles,size):
    w,h=size;mx=np.full((h,w),-1,np.float32);my=mx.copy()
    for ids in triangles:
        p=dst[ids];q=src[ids]
        x0,y0=np.maximum(0,np.floor(p.min(0))).astype(int)
        x1,y1=np.minimum([w,h],np.ceil(p.max(0))+1).astype(int)
        if x0>=x1 or y0>=y1:continue
        transform=cv2.getAffineTransform(p,q)
        xx,yy=np.meshgrid(np.arange(x0,x1),np.arange(y0,y1))
        mask=np.zeros(xx.shape,np.uint8)
        cv2.fillConvexPoly(mask,np.round((p-[x0,y0])*256).astype(np.int32),1,shift=8)
        inside=mask.astype(bool)
        mx[y0:y1,x0:x1][inside]=(transform[0,0]*xx+transform[0,1]*yy+transform[0,2])[inside]
        my[y0:y1,x0:x1][inside]=(transform[1,0]*xx+transform[1,1]*yy+transform[1,2])[inside]
    return mx,my


class ViewBlendFrame(RigFrame):
    def __init__(self,a,b,phase):
        dominant=a if phase<.5 else b
        super().__init__(dominant.rig,dominant.pose,a.code,(a.width,a.height))
        self.endpoints=(a,b);self.phase=phase
        self.direction=b.rig['_view_direction']*phase
        self.geometry=None

    def layer_selection(self):
        a,b=self.endpoints;data=a.rig['_view_registration'][str(b.rig['_view_direction'])]
        return ({name for name,row in data.items() if self.phase<row['handoff']+.14},
                {row['target'] for row in data.values() if self.phase>row['handoff']-.14})

    @property
    def bgra(self):
        from .rig_expression_pose import compose,endpoint
        layers=[];poses=[]
        for f,selected in zip(self.endpoints,self.layer_selection()):
            pose=compose(f.pose,endpoint(f.rig,f.code));d={}
            render(f.rig['parts'],f.rig['textures'],pose,size=(f.width,f.height),expression=f.code,layers=d,selected_parts=selected)
            layers.append(d);poses.append(pose)
        return self.composite(layers,poses).bgra

    def composite(self,layers,poses):
        a,b=self.endpoints;t=self.phase;w,h=self.width,self.height
        data=a.rig['_view_registration'][str(b.rig['_view_direction'])]
        ma=matrices(a.rig['parts'],poses[0]);mb=matrices(b.rig['parts'],poses[1])
        out=np.zeros((h,w,4),np.float32);geometry=[]
        for part in sorted(a.rig['parts'],key=lambda p:p['order']):
            name=part['id'];row=data[name];other=row['target']
            x=layers[0].get(name);y=layers[1].get(other)
            if x is None and y is None:continue
            pa=_world(row['source'],ma[name]);pb=_world(row['destination'],mb[other]);p=pa*(1-t)+pb*t
            # A short, staggered handoff avoids both a whole-character cut and
            # a long translucent double silhouette. Geometry is already aligned.
            center=row['handoff'];u=np.clip((t-center+.14)/.28,0,1);u=u*u*(3-2*u)
            mapped=[]
            for index,(pixels,points) in enumerate(((x,pa),(y,pb))):
                if pixels is None or (index==0 and u==1) or (index==1 and u==0):
                    mapped.append(np.zeros_like(out));continue
                if len(points)==9:
                    # The rectangular limb/prop registration is affine. Avoid
                    # constructing eight full raster maps for that same affine.
                    matrix=cv2.getAffineTransform(points[[0,2,6]],p[[0,2,6]])
                    warped=cv2.warpAffine(pixels,matrix,(w,h),flags=cv2.INTER_LINEAR)
                else:
                    m=_maps(points,p,row['triangles'],(w,h))
                    warped=cv2.remap(pixels,*m,cv2.INTER_LINEAR)
                mapped.append(warped.astype(np.float32))
            layer=mapped[0]*(1-u)+mapped[1]*u
            out=layer+out*(1-layer[:,:,3:4]/255)
            geometry.append((name,other,p,pa,pb,row['triangles'],float(u),layer[:,:,3]))
        self.geometry=geometry
        return AlphaFrame(w,h,np.clip(np.round(out),0,255).astype(np.uint8).tobytes())

    def hit(self,x,y):
        from .rig_hit import hit
        if self.geometry is None:self.bgra
        ix,iy=int(x),int(y)
        if not 0<=ix<self.width or not 0<=iy<self.height:return None
        for name,other,p,pa,pb,triangles,u,alpha in reversed(self.geometry):
            if alpha[iy,ix]<16:continue
            k=0 if u<.5 else 1;src=pa if k==0 else pb
            for ids in triangles:
                triangle=p[ids]
                if cv2.pointPolygonTest(triangle,(float(x),float(y)),False)<0:continue
                matrix=cv2.getAffineTransform(triangle,src[ids]);sx,sy=matrix@np.array([x,y,1.])
                return hit(self.endpoints[k],sx,sy,only_part=name if k==0 else other)
            return None
        return None
