"""Independent cosmetic clocks, with stable cadence within each cycle."""
import math,random,time
class IdleTiming:
 def __init__(self,rng=None,*,now=None):
  self.rng=rng or random.Random();now=time.monotonic() if now is None else now
  self.phase=self.rng.randrange(100000);self.speed=self.rng.uniform(.94,1.06)
  self.next_blink=now+self.rng.uniform(2.8,6.5);self.blink_until=0
  self.next_micro=now+self.rng.uniform(6,12);self._micro=None;self._micro_suspended=False
 def delay(self,ms):return max(16,round(ms*self.speed))
 def blink(self,now):
  if now>=self.next_blink:
   self.blink_until=now+.12;self.next_blink=now+self.rng.uniform(2.8,6.5)
  return now<self.blink_until

 def micro_pose(self,now,mood,*,enabled=True):
  """Finite cosmetic pose, never an action selection or psychological event."""
  if not enabled:
   self._micro=None;self._micro_suspended=True
   return {}
  if self._micro_suspended:
   self._micro_suspended=False;self.next_micro=now+self.rng.uniform(6,12)
  if self._micro is None:
   if now<self.next_micro:return {}
   self._micro=(now,self.rng.uniform(1.3,1.9),self.rng.choice((-1,1)))
  start,duration,side=self._micro
  phase=(now-start)/duration
  if phase>=1:
   self._micro=None;self.next_micro=now+self.rng.uniform(6,12)
   return {}
  strength=math.sin(math.pi*max(0,phase))**2
  # Only cosmetic amplitudes vary. The selected face and formal state stay put.
  lively=mood=='happy';subdued=mood in ('sad','aggrieved')
  return {'head':{'angle':side*(3.5 if lively else 2.5)*strength,
                  'y':(1.8 if subdued else -.8)*strength},
          'left_arm':{'angle':(4 if lively else 2)*strength},
          'right_arm':{'angle':-(4 if lively else 2)*strength},
          'tail':{'tail':side*(.30 if lively else .12 if subdued else .20)*strength}}


def micro_frame(frame,offsets):
 """Offset existing bound parts without replacing their artwork or expression."""
 if not offsets:return frame
 from .rig_activity import RigFrame
 if not isinstance(frame,RigFrame):return frame
 pose={part:dict(values) for part,values in frame.pose.items()}
 known={part['id'] for part in frame.rig['parts']}
 for part,values in offsets.items():
  if part not in known:continue
  target=pose.setdefault(part,{})
  for key,value in values.items():
   if key in ('x','y') and part in frame.rig['roots']:value*=frame.rig['scale']
   target[key]=target.get(key,0)+value
 return RigFrame(frame.rig,pose,frame.code,(frame.width,frame.height))
