"""Short whole-frame presentation transition; no action/state authority."""
def blend(source,target,progress):
    if progress<=0:return source
    if progress>=1:return target
    if (source.width,source.height)!=(target.width,target.height):return target
    from .windows_alpha import AlphaFrame
    # AlphaFrame pixels are premultiplied BGRA. Interpolate all four channels
    # together, preserving transparent edges instead of fading via black.
    t=round(progress*progress*(3-2*progress)*256)
    left=tuple(i*(256-t) for i in range(256))
    right=tuple(i*t+128 for i in range(256))
    data=bytes((left[a]+right[b])>>8 for a,b in zip(source.bgra,target.bgra))
    return AlphaFrame(target.width,target.height,data)


# Bounded display-only motion correspondence. Never stored in event history.
from functools import lru_cache

@lru_cache(maxsize=12)
def _motion_pair(source,target):
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    a=np.frombuffer(source.bgra,dtype=np.uint8).reshape(source.height,source.width,4)
    b=np.frombuffer(target.bgra,dtype=np.uint8).reshape(target.height,target.width,4)
    def gray(v):
        # Composite for feature matching only; rendered pixels retain alpha.
        rgb=np.minimum(255,v[:,:,:3].astype(np.uint16)+(255-v[:,:,3:4])).astype(np.uint8)
        return cv2.cvtColor(rgb,cv2.COLOR_BGR2GRAY)
    ga,gb=gray(a),gray(b)
    flow=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    flow.setFinestScale(0)
    flow.setVariationalRefinementIterations(10)
    y,x=np.mgrid[:source.height,:source.width].astype(np.float32)
    def center(v):
        alpha=v[:,:,3].astype(np.float32);mass=max(1,float(alpha.sum()))
        return np.array([(x*alpha).sum()/mass,(y*alpha).sum()/mass],dtype=np.float32)
    delta=center(b)-center(a)
    def aligned(gray,offset):
        return cv2.warpAffine(gray,np.array([[1,0,offset[0]],[0,1,offset[1]]],dtype=np.float32),
            (source.width,source.height),borderMode=cv2.BORDER_CONSTANT,borderValue=255)
    forward=flow.calc(ga,aligned(gb,-delta),None)+delta
    backward=flow.calc(gb,aligned(ga,delta),None)-delta
    return a,b,x,y,forward,backward


def morph(source,target,progress):
    """Motion-compensated whole-pose interpolation, not an unaligned dissolve.

    Cached optical correspondence moves features into intermediate positions
    before blending premultiplied pixels. No independent face or body layers.
    """
    if progress<=0:return source
    if progress>=1:return target
    if source is target or source==target:return source
    if (source.width,source.height)!=(target.width,target.height):return target
    import cv2
    import numpy as np
    from .windows_alpha import AlphaFrame
    return _interpolate_pair(_motion_pair(source,target),progress)


def _interpolate_pair(pair,progress):
    import cv2
    import numpy as np
    from .windows_alpha import AlphaFrame
    a,b,x,y,forward,backward=pair
    t=progress*progress*(3-2*progress)
    def warp(image,field,amount):
        mx=x-field[:,:,0]*amount;my=y-field[:,:,1]*amount
        for _ in range(3):
            displacement=cv2.remap(field,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
            mx=x-displacement[:,:,0]*amount;my=y-displacement[:,:,1]*amount
        return cv2.remap(image,mx,my,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    left=warp(a,forward,t)
    right=warp(b,backward,1-t)
    # Occluded/new features cannot be reliably blended (closed/open eyes,
    # hair tips). Use the nearer key pose for high-contrast differences, so
    # these remain one opaque feature rather than two translucent features.
    data=left.copy() if t<.5 else right.copy()
    compatible=np.max(np.abs(left.astype(np.int16)-right.astype(np.int16)),axis=2)<=16
    mixed=cv2.addWeighted(left,1-t,right,t,0)
    data[compatible]=mixed[compatible]
    # Interpolation/rounding must retain premultiplied alpha at silhouettes.
    data[:,:,:3]=np.minimum(data[:,:,:3],data[:,:,3:4])
    return AlphaFrame(a.shape[1],a.shape[0],data.tobytes())
