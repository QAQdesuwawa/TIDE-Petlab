"""Expression-independent contact poses on the existing registered rig."""
from .rig_activity import append
from model_packages.desktop_pet_v1.social_contact import KEYS

def declarations():
    hold=[[0,0],[.25,1],[.7,1],[1,0]]
    kiss_lean=[[0,0],[.14,.08],[.34,.68],[.46,1],[.66,1],[.87,.25],[1,0]]
    def channel(part,parameter,amount,curve=hold):
        return dict(part=part,parameter=parameter,amplitude=amount,curve=curve)
    result={}
    for key in KEYS:
        accept=key.startswith('accept_');decline=key.startswith('decline_')
        channels=([channel('head','angle',-8),channel('right_arm','angle',-18)] if decline else
            [channel('left_arm','angle',-55 if accept else -25),channel('right_arm','angle',-55 if accept else 25)])
        duration=1800 if accept else 1000
        if key=='accept_kiss':
            # The mirrored side views lean in together, pause once, then withdraw.
            channels=[channel('body','x',94,kiss_lean),channel('body','angle',8,kiss_lean),
                      channel('head','x',48,kiss_lean),channel('head','angle',-12,kiss_lean),
                      channel('left_arm','angle',-12,kiss_lean),channel('right_arm','angle',-17,kiss_lean)]
            duration=1560
        elif accept:
            channels.extend([channel('body','x',50),channel('body','angle',6),
                             channel('head','angle',8 if key.endswith('cheek_kiss') else -4)])
        result[key]=dict(source='bound_rig',duration_ms=duration,channels=channels)
    return result

def kiss_eyes_closed(phase):
    return .38 <= phase <= .71


def install(frames,root,binding):
    from .rig_activity import RigFrame

    frames,clips,mapping=append(frames,root,binding,declarations())
    kiss_frames=clips['accept_kiss']['frames']
    for code,samples in frames.items():
        updated=list(samples)
        for phase,index in enumerate(kiss_frames):
            pose=samples[index]
            idle=pose.rig.get('idle')
            if idle and kiss_eyes_closed(phase/(len(kiss_frames)-1)):
                updated[index]=RigFrame(pose.rig,pose.pose,idle['blink_code'],(pose.width,pose.height))
        frames[code]=tuple(updated)
    return frames,clips,mapping
