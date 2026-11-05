"""Reuse walk frame indices with bound geometry and live expressions."""
from copy import deepcopy
from .rig_activity import RigFrame,_pose

def samples(source,count):
    rig=source.rig
    # Carry poses retain their already-authored prop/arm parameters.
    working={**rig,'base':deepcopy(source.pose)}
    return tuple(RigFrame(rig,_pose(working,rig['gait'],i/count),source.code,(source.width,source.height)) for i in range(count))*2

def build(frames,config,source_indices=None):
    start=max(map(len,frames.values()));count=config['frame_count'];result={};clips={}
    for code,old in frames.items():
        source=old[0 if source_indices is None else source_indices[0]]
        poses=samples(source,count)
        result[code]=old+(old[0],)*(start-len(old))+poses
        clips[code]={'left':list(range(start,start+count)),'right':list(range(start+count,start+count*2))}
    return result,clips,{i:0 for i in range(start,start+2*count)}
