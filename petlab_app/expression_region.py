"""Local material composition for a bound head, never screen-space overlays."""
from functools import lru_cache
from collections import OrderedDict
from threading import RLock
import numpy as np
from .windows_alpha import AlphaFrame
from .pose_transition import morph

_pairs=OrderedDict()
_pair_bytes=0
_pair_limit=16*1024*1024
_pair_lock=RLock()


def _prepared_pair(source,target):
    from .pose_transition import _motion_pair
    global _pair_bytes
    key=(source,target)
    with _pair_lock:
        if key in _pairs:
            _pairs.move_to_end(key);return _pairs[key][0]
        reverse=(target,source)
        if reverse in _pairs:
            _pairs.move_to_end(reverse)
            a,b,x,y,forward,backward=_pairs[reverse][0]
            return b,a,x,y,backward,forward
    pair=_motion_pair(source,target)
    cost=sum(a.nbytes for a in pair)+len(source.bgra)+len(target.bgra)
    if cost<=_pair_limit:
        with _pair_lock:
            if key not in _pairs:
                while _pairs and _pair_bytes+cost>_pair_limit:
                    _,old=_pairs.popitem(last=False);_pair_bytes-=old[1]
                _pairs[key]=(pair,cost);_pair_bytes+=cost
    return pair


class ExpressionRegion:
    def __init__(self, base, rect, *, feather=4, interpolate=morph):
        x,y,w,h=rect
        if any(type(v) is not int for v in rect) or min(x,y)<0 or min(w,h)<1 or x+w>base.width or y+h>base.height:
            raise ValueError('invalid expression region')
        if type(feather) is not int or not 0<=feather<=min(w,h)//2:
            raise ValueError('invalid feather')
        self.base=base;self.rect=rect;self.interpolate=interpolate
        yy,xx=np.indices((h,w))
        edge=np.minimum.reduce([xx,yy,w-1-xx,h-1-yy])
        self.weight=np.ones((h,w,1)) if not feather else np.minimum(1,edge/feather)[:,:,None]
        # At most two whole source frames retained per region for crop reuse.
        self.crop=lru_cache(maxsize=2)(self._crop)

    def _pixels(self, frame):
        if (frame.width,frame.height)!=(self.base.width,self.base.height):
            raise ValueError('expression dimensions differ')
        return np.frombuffer(frame.bgra,np.uint8).reshape(frame.height,frame.width,4)

    def _crop(self, frame):
        x,y,w,h=self.rect
        return AlphaFrame(w,h,self._pixels(frame)[y:y+h,x:x+w].tobytes())

    def prepare(self, frame):
        """Canonical expression: unchanged hair/silhouette, authored face region."""
        x,y,w,h=self.rect;out=self._pixels(self.base).copy()
        source=self._pixels(frame)[y:y+h,x:x+w]
        out[y:y+h,x:x+w]=np.round(source*self.weight+out[y:y+h,x:x+w]*(1-self.weight)).astype(np.uint8)
        return AlphaFrame(self.base.width,self.base.height,out.tobytes())

    def prepare_pair(self, source, target):
        """Prepare initial local correspondences during asset loading, not UI."""
        if source==target or min(self.rect[2:])<12:return
        _prepared_pair(self.crop(source),self.crop(target))

    def __call__(self, source, target, phase):
        if phase<=0:return source
        if phase>=1:return target
        x,y,w,h=self.rect
        interpolate=self.interpolate
        if interpolate is morph and min(w,h)<12:
            from .pose_transition import blend
            interpolate=blend
        if interpolate is morph:
            from .pose_transition import _interpolate_pair
            local=_interpolate_pair(_prepared_pair(self.crop(source),self.crop(target)),phase)
        else:local=interpolate(self.crop(source),self.crop(target),phase)
        out=self._pixels(self.base).copy()
        # Prepared endpoints already feather the material boundary. Do not
        # feather again on interruption, which would progressively erase it.
        out[y:y+h,x:x+w]=np.frombuffer(local.bgra,np.uint8).reshape(h,w,4)
        return AlphaFrame(self.base.width,self.base.height,out.tobytes())
