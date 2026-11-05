"""Apply authored facing offsets before the existing expression presenter."""
from copy import deepcopy
from .rig_activity import RigFrame

def facing_frame(frame,direction):
    return turn_frame(frame,direction,direction,1)


def turn_frame(frame,origin,target,phase):
    """Interpolate authored local geometry on the existing action phase."""
    if frame.rig.get('views'):
        from .rig_view_transition import turn
        return turn(frame,origin,target,phase)
    controls=frame.rig.get('facing')
    if not controls:return frame
    if origin not in (-1,0,1) or target not in (-1,0,1) or not 0<=phase<=1:
        raise ValueError('invalid bound turn phase')
    start=controls['right' if origin>0 else 'left'] if origin else {}
    end=controls['right' if target>0 else 'left'] if target else {}
    t=phase*phase*(3-2*phase)
    pose=deepcopy(frame.pose)
    for part in start.keys()|end.keys():
        values=pose.setdefault(part,{})
        a=start.get(part,{});b=end.get(part,{})
        for name in a.keys()|b.keys():
            neutral=1 if name.startswith('scale') else 0
            value=a.get(name,neutral)*(1-t)+b.get(name,neutral)*t
            values[name]=values.get(name,1)*value if name.startswith('scale') else values.get(name,0)+value
    return RigFrame(frame.rig,pose,frame.code,(frame.width,frame.height))
