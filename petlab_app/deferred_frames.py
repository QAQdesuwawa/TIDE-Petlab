"""Retain indexed legacy fallbacks without rendering unused animation pixels."""
from threading import RLock


def defer(source,count,produce):
    lock=RLock();result=None
    def pixels(index):
        nonlocal result
        with lock:
            if result is None:result=produce()
            return result[index].bgra
    return tuple(DeferredFrame(source.width,source.height,i,pixels) for i in range(count))


class DeferredFrame:
    def __init__(self,width,height,index,pixels):
        self.width=width;self.height=height
        self._index=index;self._pixels=pixels

    @property
    def bgra(self):return self._pixels(self._index)
