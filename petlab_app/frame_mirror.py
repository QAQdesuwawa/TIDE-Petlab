"""Bounded display-only mirroring; retain source identity with cached pixels."""
from collections import OrderedDict
import numpy as np
from .windows_alpha import AlphaFrame


class MirrorCache:
    def __init__(self,limit=8*1024*1024):
        self.limit=limit;self.bytes=0;self.rows=OrderedDict()

    def frame(self,source):
        key=id(source)
        cached=self.rows.get(key)
        if cached is not None and cached[0] is source:
            self.rows.move_to_end(key);return cached[1]
        pixels=source.bgra
        result=AlphaFrame(source.width,source.height,np.frombuffer(pixels,np.uint8).reshape(source.height,source.width,4)[:,::-1].tobytes())
        cost=len(pixels)+len(result.bgra)
        if cost<=self.limit:
            while self.rows and self.bytes+cost>self.limit:
                _,old=self.rows.popitem(last=False);self.bytes-=old[2]
            self.rows[key]=(source,result,cost);self.bytes+=cost
        return result
