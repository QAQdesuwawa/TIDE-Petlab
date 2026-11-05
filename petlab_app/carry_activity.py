"""Play the same tail action on the whole held-prop pose; no behavior selection."""
def build(frames,activities):
    if not all(k in activities for k in ('play_tail','eat_rice','offer_bowl')):return frames,{},{}
    from .rig_activity import RigFrame
    if isinstance(frames['normal'][activities['eat_rice']['frames'][0]],RigFrame):
        return _bound(frames,activities)
    from .whole_frame_renderer import animate_tail
    result=dict(frames);clips={};mapping={}
    base=activities['play_tail'];count=48
    for pose,key,index in [('hold_bowl','eat_rice',0),('offer_bowl','offer_bowl',-1)]:
        source=frames['normal'][activities[key]['frames'][index]]
        config={'frame_count':count,'region':[.025,.64,.30,.93],'amplitude_px':16.15,
                'tail_origin':'right','implementation':'carry-tail-v1'}
        samples=animate_tail(source,config)+(source,)
        offset=max(len(v) for v in result.values())
        for code,old in result.items():result[code]=old+(source,)*(offset-len(old))+samples
        clips[pose]={'frames':[offset+i%count for i in range(len(base['frames'])-1)]+[offset+count],
                     'durations_ms':list(base['durations_ms'])}
        mapping.update({i:0 for i in range(offset,offset+count+1)})
    return result,clips,mapping


def _bound(frames,activities):
    """Apply the selected tail geometry to each expression's held-prop pose."""
    from copy import deepcopy
    from .rig_activity import RigFrame
    result=dict(frames);clips={};mapping={};tail=activities['play_tail']
    for name,key,index in [('hold_bowl','eat_rice',0),('offer_bowl','offer_bowl',-1)]:
        start=max(map(len,result.values()))
        for code,old in result.items():
            source=frames[code][activities[key]['frames'][index]];samples=[]
            for frame_index in tail['frames']:
                motion=frames[code][frame_index]
                if not isinstance(motion,RigFrame):raise ValueError('bound carry requires bound tail motion')
                pose=deepcopy(source.pose)
                for part,values in motion.pose.items():
                    for parameter,value in values.items():
                        neutral=1 if parameter in ('opacity','scale_x','scale_y') else 0
                        base=motion.rig['base'].get(part,{}).get(parameter,neutral)
                        if value!=base:
                            target=pose.setdefault(part,{})
                            target[parameter]=target.get(parameter,neutral)+(value-base)
                samples.append(RigFrame(source.rig,pose,code,(source.width,source.height)))
            result[code]=old+(old[0],)*(start-len(old))+tuple(samples)
        indices=list(range(start,start+len(tail['frames'])))
        clips[name]={'frames':indices,'durations_ms':list(tail['durations_ms'])}
        mapping.update({i:0 for i in indices})
    return result,clips,mapping
