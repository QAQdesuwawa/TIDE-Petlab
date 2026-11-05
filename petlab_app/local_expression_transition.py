"""Display-only transition of a bound part, before its parent transforms.

The host owns time and expression selection. This object neither advances an
action nor emits events. Retargeting retains the currently visible local pose.
"""
from .pose_transition import morph


class LocalExpressionTransition:
    def __init__(self, frame, *, interpolate=morph):
        self.source=self.target=frame
        self.started_ms=0.0
        self.duration_ms=0.0
        self.interpolate=interpolate

    def sample(self, now_ms):
        if self.source is self.target:return self.target
        phase=max(0.0,min(1.0,(now_ms-self.started_ms)/self.duration_ms))
        return self.interpolate(self.source,self.target,phase)

    def retarget(self, frame, now_ms, *, duration_ms=120):
        if not 0<duration_ms<=1000:raise ValueError('invalid display transition duration')
        if (frame.width,frame.height)!=(self.target.width,self.target.height):
            raise ValueError('expression parts must share local dimensions')
        if frame is self.target:return
        self.source=self.sample(now_ms)
        self.target=frame
        self.started_ms=now_ms
        self.duration_ms=duration_ms
