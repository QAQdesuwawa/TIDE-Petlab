"""Compile authored material joins once, beneath an unchanged facial region."""
import math
import cv2
import numpy as np
from .cutout_binding import matrices
from .windows_alpha import AlphaFrame


def prepare(parts,textures,base):
    if not any('overlap_blend' in p for p in parts):return
    by_id={p['id']:p for p in parts}
    transforms=matrices(parts,base)
    for part in parts:
        spec=part.pop('overlap_blend',None)
        if spec is None:continue
        if not isinstance(spec,dict) or set(spec)!={'backing','edges','width','protect'}:
            raise ValueError('invalid overlap blend')
        backing=by_id.get(spec['backing'])
        if (backing is None or backing.get('parent')!=part['id'] or
                backing['order']>=part['order'] or backing.get('expressions') or backing.get('deformers')):
            raise ValueError('overlap backing must be a fixed child behind material')
        polygon=np.asarray(part.get('clip_polygon',()),dtype=float)
        edges=spec['edges'];width=spec['width']
        if (polygon.ndim!=2 or polygon.shape[1]!=2 or len(polygon)<3 or
                not np.isfinite(polygon).all() or not isinstance(edges,list) or not edges or
                any(type(i) is not int or not 0<=i<len(polygon) for i in edges) or
                type(width) not in (int,float) or not math.isfinite(width) or not 0<width<=32):
            raise ValueError('invalid overlap edge band')
        source=textures[part['texture']];h,w=source.height,source.width
        yy,xx=np.mgrid[:h,:w].astype(np.float32)
        distance=np.full((h,w),np.inf)
        for i in edges:
            a=polygon[i];v=polygon[(i+1)%len(polygon)]-a
            if v@v==0:raise ValueError('empty overlap edge')
            t=np.clip(((xx-a[0])*v[0]+(yy-a[1])*v[1])/(v@v),0,1)
            distance=np.minimum(distance,np.hypot(xx-a[0]-t*v[0],yy-a[1]-t*v[1]))
        # Backing and foreground share the head transform; coverage therefore
        # stays valid through every head pose and is independent of expression.
        matrix=np.linalg.solve(transforms[backing['id']],transforms[part['id']])
        x=xx-part['anchor'][0];y=yy-part['anchor'][1]
        mx=(matrix[0,0]*x+matrix[0,1]*y+matrix[0,2]+backing['anchor'][0]).astype(np.float32)
        my=(matrix[1,0]*x+matrix[1,1]*y+matrix[1,2]+backing['anchor'][1]).astype(np.float32)
        back=textures[backing['texture']]
        alpha=np.frombuffer(back.bgra,np.uint8).reshape(back.height,back.width,4)[:,:,3]
        coverage=cv2.remap(alpha,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)/255
        amount=np.clip(distance/width,0,1);amount=amount*amount*(3-2*amount)
        amount=1-(1-amount)*coverage
        if not isinstance(spec['protect'],list):raise ValueError('invalid protected regions')
        for box in spec['protect']:
            if (len(box)!=4 or any(type(n) not in (int,float) or not math.isfinite(n) for n in box)
                    or not 0<=box[0]<box[2]<=w or not 0<=box[1]<box[3]<=h):
                raise ValueError('invalid protected region')
            amount[(xx>=box[0])&(xx<=box[2])&(yy>=box[1])&(yy<=box[3])]=1
        replacements={}
        for name in {part['texture'],*part.get('expressions',{}).values()}:
            frame=textures[name]
            if (frame.width,frame.height)!=(w,h):raise ValueError('overlap expression size mismatch')
            pixels=np.frombuffer(frame.bgra,np.uint8).reshape(h,w,4)
            key='__overlap__:'+part['id']+':'+name
            textures[key]=AlphaFrame(w,h,np.rint(pixels*amount[:,:,None]).astype(np.uint8).tobytes())
            replacements[name]=key
        part['texture']=replacements[part['texture']]
        if 'expressions' in part:
            part['expressions']={code:replacements[name] for code,name in part['expressions'].items()}
