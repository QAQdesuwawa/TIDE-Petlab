"""Blend display transforms in one rig; expression materials stay independent."""
from .rig_activity import RigFrame


def compatible(source,target):
    if source.rig is target.rig:return True
    family=source.rig.get('_view_family')
    return family is not None and family is target.rig.get('_view_family')


def between(source,target,phase):
    if not compatible(source,target) or phase>=1:return target
    if phase<=0:
        from .rig_view_blend import ViewBlendFrame
        if isinstance(source,ViewBlendFrame):
            a,b=source.endpoints
            return ViewBlendFrame(RigFrame(a.rig,a.pose,target.code,(a.width,a.height)),
                                  RigFrame(b.rig,b.pose,target.code,(b.width,b.height)),source.phase)
        return RigFrame(source.rig,source.pose,target.code,(target.width,target.height))
    root=target.rig.get('_view_root',target.rig)
    if root.get('_view_registration') and (source.rig is not target.rig or hasattr(source,'direction') or hasattr(target,'direction')):
        from .rig_view_transition import rebind,turn
        a=getattr(source,'direction',source.rig.get('_view_direction',0))
        b=getattr(target,'direction',target.rig.get('_view_direction',0))
        f=between(rebind(source,root),rebind(target,root),phase)
        t=phase*phase*(3-2*phase);direction=a+(b-a)*t
        return turn(f,0,1 if direction>=0 else -1,abs(direction))
    if source.rig is not target.rig:
        from .rig_view_transition import rebind
        view=source.rig if phase<.5 else target.rig
        family=source.rig.get('_continuous_view_family')
        if family is None or family is not target.rig.get('_continuous_view_family'):
            source=rebind(source,view);target=rebind(target,view)
    t=max(0.,phase);t=t*t*(3-2*t);pose={}
    bridges={}
    for visible,rows in source.rig.get('visibility_bridges',{}).items():
        before=source.pose.get(visible,{}).get('opacity',1)
        after=target.pose.get(visible,{}).get('opacity',1)
        if (before>0)==(after>0):continue
        # Only the exposed ordinary limbs approach the authored grip pose.
        # Hidden geometry and ownership remain controlled by the target pose.
        if (after>0 and t<.5) or (before>0 and t>=.5):
            for part,row in rows.items():
                for name,value in row.items():bridges[part,name]=(value,after>0)
    for part in source.pose.keys()|target.pose.keys():
        a=source.pose.get(part,{});b=target.pose.get(part,{})
        values={}
        for name in a.keys()|b.keys():
            neutral=1 if name in ('opacity','scale_x','scale_y') else 0
            x=a.get(name,neutral);y=b.get(name,neutral)
            # Authored visibility selects a part, not an extra translucent
            # limb. Keep replacement parts exclusive during a pose bridge.
            values[name]=(x if t<.5 else y) if name=='opacity' else x+(y-x)*t
        for (bridge_part,name),(value,entering) in bridges.items():
            if bridge_part!=part:continue
            neutral=1 if name in ('scale_x','scale_y') else 0
            values[name]=(a.get(name,neutral)+(value-a.get(name,neutral))*t*2 if entering
                          else value+(b.get(name,neutral)-value)*(t-.5)*2)
        pose[part]=values
    return RigFrame(target.rig,pose,target.code,(target.width,target.height))
