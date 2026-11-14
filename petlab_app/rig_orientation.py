"""Display-only orientation continuity; never schedules an actor or movement."""
import math

from .rig_view_transition import turn


def along_route(vector):
    """Front/side blend and eye direction for the current screen-space segment."""
    if vector is None:
        return None, None
    dx, dy = vector
    distance = math.hypot(dx, dy)
    if not distance:
        return None, None
    # Bridge to the rear artwork through the side profile. An upper diagonal
    # must not rotate toward the front just before the rear view takes over.
    side = math.atan2(dx, max(0, dy)) / (math.pi / 2)
    gaze = {'horizontal': dx / distance, 'vertical': dy / distance,
            'elevation_degrees': math.degrees(math.atan2(-dy, abs(dx)))}
    return side, gaze


class Orientation:
    def __init__(self):
        self.source=self.target=0.
        self.started=0.
        self.duration=.16

    def value(self,now):
        t=max(0.,min(1.,(now-self.started)/self.duration))
        t=t*t*(3-2*t)
        return self.source+(self.target-self.source)*t

    def sync(self,value,now):
        self.source=self.target=float(value);self.started=now

    def present(self,frame,target,now,*,striding=False):
        if not -1 <= target <= 1:raise ValueError('invalid display orientation')
        root=frame.rig.get('_view_root',frame.rig)
        if target!=self.target:
            self.source=self.value(now);self.target=float(target);self.started=now
            # A bend blends while the feet keep moving. Initial target-facing
            # and other registered turns retain their usual visual lead.
            self.duration=.12 if striding else (.28 if root.get('_view_registration') else .16)
        value=self.value(now)
        result=turn(frame,0,1,value) if value>=0 else turn(frame,0,-1,-value)
        active=self.source!=self.target and now<self.started+self.duration
        return result,active
