"""Adapt existing input playback indices, without creating new input events."""
from .rig_activity import RigFrame,_pose


def samples(source,kind,count):
    rig=source.rig
    return tuple(RigFrame(rig,_pose(rig,rig['input_feedback'][kind],i/(count-1)),
                          source.code,(source.width,source.height)) for i in range(count))


def replace_drag(frames,clips,*,lead_in=None):
    result=dict(frames)
    for code,clip in clips.items():
        source=frames[code][0]
        if not isinstance(source,RigFrame) or 'input_feedback' not in source.rig:continue
        rows=list(frames[code])
        if lead_in is not None:
            # The pre-loop slots otherwise retain the legacy whole-character
            # lift artwork. Begin in this rig's rest pose, then the existing
            # loop owns the movement; no second character/face is introduced.
            for index in lead_in['frames']:rows[index]=source
        for index,frame in zip(clip['frames'],samples(source,'drag',len(clip['frames']))):rows[index]=frame
        result[code]=tuple(rows)
    return result
