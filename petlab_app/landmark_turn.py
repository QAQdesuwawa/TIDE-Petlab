"""Explicit landmark warp for authored view transitions; no dense optical flow."""
from functools import lru_cache
import numpy as np
import cv2
from .windows_alpha import AlphaFrame

@lru_cache(maxsize=8)
def triangles(points):
    p=np.asarray(points,np.float32)
    sub=cv2.Subdiv2D((0,0,int(p[:,0].max())+2,int(p[:,1].max())+2))
    for x,y in p:sub.insert((float(x),float(y)))
    result=[]
    for triangle in sub.getTriangleList().reshape(-1,3,2):
        ids=[int(np.argmin(np.sum((p-v)**2,axis=1))) for v in triangle]
        if all(np.linalg.norm(p[j]-v)<.01 for j,v in zip(ids,triangle)):result.append(tuple(ids))
    return tuple(result)

def warp(frame,source,target,topology):
    h,w=frame.height,frame.width
    mx=np.zeros((h,w),np.float32);my=np.zeros_like(mx)
    for tri in topology:
        a=np.array([source[i] for i in tri],np.float32);b=np.array([target[i] for i in tri],np.float32)
        det=lambda p:float(np.cross(p[1]-p[0],p[2]-p[0]))
        if det(a)*det(b)<=0:raise ValueError('turn mesh triangle inverted')
        x0=max(0,int(np.floor(b[:,0].min())));x1=min(w,int(np.ceil(b[:,0].max()))+1)
        y0=max(0,int(np.floor(b[:,1].min())));y1=min(h,int(np.ceil(b[:,1].max()))+1)
        if x1<=x0 or y1<=y0:continue
        mask=np.zeros((y1-y0,x1-x0),np.uint8)
        cv2.fillConvexPoly(mask,np.round(b-[x0,y0]).astype(np.int32),1)
        y,x=np.mgrid[y0:y1,x0:x1].astype(np.float32)
        transform=cv2.getAffineTransform(b,a)
        xx=transform[0,0]*x+transform[0,1]*y+transform[0,2]
        yy=transform[1,0]*x+transform[1,1]*y+transform[1,2]
        select=mask.astype(bool);mx[y0:y1,x0:x1][select]=xx[select];my[y0:y1,x0:x1][select]=yy[select]
    pixels=np.frombuffer(frame.bgra,np.uint8).reshape(h,w,4)
    return cv2.remap(pixels,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)

def interpolate(start,end,start_points,end_points,progress):
    if progress<=0:return start
    if progress>=1:return end
    if (start.width,start.height)!=(end.width,end.height):raise ValueError('turn canvas differs')
    a=np.asarray(start_points,np.float32);b=np.asarray(end_points,np.float32)
    t=progress*progress*(3-2*progress);middle=a*(1-t)+b*t
    topology=triangles(tuple(map(tuple,(a+b)/2)))
    left=warp(start,a,middle,topology);right=warp(end,b,middle,topology)
    value=cv2.addWeighted(left,1-t,right,t,0)
    value[:,:,:3]=np.minimum(value[:,:,:3],value[:,:,3:4])
    return AlphaFrame(start.width,start.height,value.tobytes())
