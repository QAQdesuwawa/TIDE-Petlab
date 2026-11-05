"""Material-view changes retain the selected action's local pose offsets."""
from copy import deepcopy
from .rig_activity import RigFrame


def rebind(frame,view):
    if hasattr(frame,'endpoints'):frame=frame.endpoints[0]
    if frame.rig is view:return frame
    original=frame.rig['base'];target=view['base'];pose=deepcopy(target)
    if {p['id'] for p in frame.rig['parts']}!={p['id'] for p in view['parts']}:
        raise ValueError('view part identities differ')
    source_basis=frame.rig.get('pose_basis',{'reflection':1,'parts':{}})
    target_basis=view.get('pose_basis',{'reflection':1,'parts':{}})
    reflection=source_basis['reflection']*target_basis['reflection']
    for part,row in frame.pose.items():
        canonical=source_basis['parts'].get(part,part)
        mapped=target_basis['parts'].get(canonical,canonical)
        result=pose.setdefault(mapped,{})
        for name,value in row.items():
            neutral=1 if name in ('scale_x','scale_y','opacity') else 0
            before=original.get(part,{}).get(name,neutral)
            after=target.get(mapped,{}).get(name,neutral)
            if name in ('scale_x','scale_y'):result[name]=after*value/before
            elif name=='opacity':result[name]=value
            else:result[name]=after+(value-before)*(reflection if name in ('x','angle') else 1)
    return RigFrame(view,pose,frame.code,(frame.width,frame.height))


def turn(frame,origin,target,phase):
    root=frame.rig.get('_view_root',frame.rig)
    views=root['views']
    if origin not in views or target not in views or not 0<=phase<=1:raise ValueError('invalid view turn')
    if origin==target:return rebind(frame,views[target])
    if origin*target==-1:
        return turn(frame,origin,0,phase*2) if phase<.5 else turn(frame,0,target,(phase-.5)*2)
    result=rebind(frame,views[origin if phase<.5 else target])
    if phase in (0,1):return result
    if root.get('_view_registration') is not None:
        from .rig_view_blend import ViewBlendFrame
        side=target if target else origin
        return ViewBlendFrame(rebind(frame,root),rebind(frame,views[side]),phase if target else 1-phase)
    family=views[origin].get('_continuous_view_family')
    if family is not None and family is views[target].get('_continuous_view_family'):
        a=rebind(frame,views[origin]);b=rebind(frame,views[target])
        t=phase*phase*(3-2*phase);pose={}
        for part in a.pose.keys()|b.pose.keys():
            left=a.pose.get(part,{});right=b.pose.get(part,{})
            values=pose.setdefault(part,{})
            for name in left.keys()|right.keys():
                default=1 if name in ('scale_x','scale_y','opacity') else 0
                x=left.get(name,default);y=right.get(name,default)
                values[name]=x+(y-x)*t
        return RigFrame(result.rig,pose,frame.code,(frame.width,frame.height))
    t=phase*phase*(3-2*phase);amount=1-abs(2*t-1)
    pose=deepcopy(result.pose)
    for part,row in frame.rig.get('view_transition',{}).items():
        values=pose.setdefault(part,{})
        for name,narrowest in row.items():values[name]=values.get(name,1)*(1-(1-narrowest)*amount)
    return RigFrame(result.rig,pose,result.code,(result.width,result.height))

