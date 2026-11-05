"""Local silhouette interpolation for detached facial features."""
from functools import lru_cache
import cv2
import numpy as np
from .windows_alpha import AlphaFrame

@lru_cache(maxsize=12)
def _field(frame):
    if frame.width*frame.height>256*256:raise ValueError('feature region exceeds 256x256 budget')
    pixels=np.frombuffer(frame.bgra,np.uint8).reshape(frame.height,frame.width,4)
    inside=(pixels[:,:,3]>=128).astype(np.uint8)
    if not inside.any():return None
    cols=np.flatnonzero(inside.any(axis=0));top=[];bottom=[]
    for x in cols:
        ys=np.flatnonzero(inside[:,x]);top.append(float(ys[0])-.5);bottom.append(float(ys[-1])+.5)
    return pixels,cols,np.array(top),np.array(bottom)

def morph(source,target,progress):
    if progress<=0:return source
    if progress>=1:return target
    if source==target:return source
    if (source.width,source.height)!=(target.width,target.height):raise ValueError('feature dimensions differ')
    a=_field(source);b=_field(target)
    if a is None or b is None:
        from .pose_transition import blend
        return blend(source,target,progress)
    t=progress*progress*(3-2*progress)
    yy,xx=np.indices((source.height,source.width),dtype=np.float32)
    low=a[1][0]*(1-t)+b[1][0]*t;high=a[1][-1]*(1-t)+b[1][-1]*t
    phase=(xx-low)/max(1,high-low)
    ax=a[1][0]+phase*(a[1][-1]-a[1][0]);bx=b[1][0]+phase*(b[1][-1]-b[1][0])
    at=np.interp(ax,a[1],a[2]);ab=np.interp(ax,a[1],a[3])
    bt=np.interp(bx,b[1],b[2]);bb=np.interp(bx,b[1],b[3])
    top=at*(1-t)+bt*t;bottom=ab*(1-t)+bb*t
    vertical=(yy-top)/np.maximum(1,bottom-top)
    left=cv2.remap(a[0],ax.astype(np.float32),(at+vertical*(ab-at)).astype(np.float32),cv2.INTER_LINEAR)
    right=cv2.remap(b[0],bx.astype(np.float32),(bt+vertical*(bb-bt)).astype(np.float32),cv2.INTER_LINEAR)
    pixels=cv2.addWeighted(left,1-t,right,t,0)
    pixels[(xx<low-1)|(xx>high+1)]=0
    return AlphaFrame(source.width,source.height,pixels.tobytes())
