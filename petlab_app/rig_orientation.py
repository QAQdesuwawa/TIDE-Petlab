"""Display-only orientation continuity; never schedules an actor or movement."""
from .rig_view_transition import turn


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

    def present(self,frame,target,now):
        if target not in (-1,0,1):raise ValueError('invalid display orientation')
        root=frame.rig.get('_view_root',frame.rig)
        # Registered artwork needs enough display samples to show its in-betweens.
        # This is a visual duration, not a wait before action or speech dispatch.
        self.duration=.28 if root.get('_view_registration') else .16
        if target!=self.target:
            self.source=self.value(now);self.target=float(target);self.started=now
        value=self.value(now)
        result=turn(frame,0,1,value) if value>=0 else turn(frame,0,-1,-value)
        active=self.source!=self.target and now<self.started+self.duration
        return result,active
