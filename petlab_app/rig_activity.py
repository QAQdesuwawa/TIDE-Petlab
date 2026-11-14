"""Bound display assets adapted to the existing indexed activity-frame contract."""
import json
import math
from copy import deepcopy
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from .cutout_binding import render, matrices
from .expression_motion import curve_value

_cache=OrderedDict()
_bytes=0
_limit=8*1024*1024
_lock=RLock()

def _validate_bound_curve(curve):
    # Bound clips may end in a persistent authored pose (holding/lying).
    # The host still owns whether that endpoint is retained or interrupted.
    if not isinstance(curve,list) or not 3<=len(curve)<=16:raise ValueError('invalid bound curve')
    previous=-1
    for row in curve:
        if not isinstance(row,list) or len(row)!=2 or any(type(n) not in (int,float) or not math.isfinite(n) for n in row):raise ValueError('invalid bound curve point')
        phase,value=row
        if not previous<phase<=1 or phase<0 or not -1<=value<=1:raise ValueError('invalid bound curve range')
        previous=phase
    if curve[0][0]!=0 or curve[-1][0]!=1:raise ValueError('bound curve must cover full phase')


def _channels(rows, known):
    seen=set()
    for c in rows:
        if set(c)!={'part','parameter','amplitude','curve'}:raise ValueError('invalid rig channel')
        key=(c['part'],c['parameter'])
        if key in seen or key[0] not in known or key[1] not in known[key[0]]:raise ValueError('invalid rig channel target')
        seen.add(key)
        if type(c['amplitude']) not in (int,float) or not math.isfinite(c['amplitude']):raise ValueError('invalid rig amplitude')
        if key[1] not in ('x','y','angle') and abs(c['amplitude'])>1:raise ValueError('invalid deformation curve amplitude')
        _validate_bound_curve(c['curve'])


def _pose(rig, channels, phase):
    pose=deepcopy(rig['base'])
    for c in channels:
        value=c['amplitude']*curve_value(c['curve'],phase)
        if c['parameter'] in ('x','y') and c['part'] in rig['roots']:value*=rig['scale']
        target=pose.setdefault(c['part'],{})
        target[c['parameter']]=target.get(c['parameter'],1 if c['parameter']=='opacity' else 0)+value
    return pose


def rest_samples(original, count, *, blink=False):
    rig=original.rig;idle=rig['idle'];size=(original.width,original.height)
    return tuple(RigFrame(rig,_pose(rig,idle['channels'],i/(count-1)),
                         idle['blink_code'] if i==2 and blink else original.code,size) for i in range(count))


class RigFrame:
    def __init__(self, rig, pose, code, size):
        self.rig=rig;self.pose=pose;self.code=code
        self.width,self.height=size

    @property
    def bgra(self):
        global _bytes
        with _lock:
            if self in _cache:
                _cache.move_to_end(self);return _cache[self]
            from .rig_expression_pose import compose,endpoint
            data=render(self.rig['parts'],self.rig['textures'],compose(self.pose,endpoint(self.rig,self.code)),
                        size=(self.width,self.height),expression=self.code).bgra
            from .rig_motion_marks import behind_limbs
            data=behind_limbs(data,self.rig,self.pose,(self.width,self.height))
            if len(data)<=_limit:
                while _cache and _bytes+len(data)>_limit:
                    _,old=_cache.popitem(last=False);_bytes-=len(old)
                _cache[self]=data;_bytes+=len(data)
            return data


def load_rig(root, binding_name, size, *, allow_views=True, _sources=None):
    from .whole_frame_renderer import _load
    if not isinstance(binding_name,str) or not binding_name:raise ValueError('missing rig binding')
    root=Path(root).resolve();path=(root/binding_name).resolve()
    if path.parent!=root:raise ValueError('rig binding outside asset directory')
    binding=json.loads(path.read_text('utf8'))
    if set(binding)-{'view_registration','pose_basis','idle','expression_regions','expression_pose','facing','gait','input_feedback','visibility_bridges','gaze_part','gaze_layout','body_regions','views','view_transition'}!={'version','canvas','textures','parts','base_pose'} or binding['version']!='petlab-cutout-binding-v1':
        raise ValueError('invalid cutout binding')
    cw,ch=binding['canvas']
    if any(type(n) is not int or n<=0 for n in (cw,ch)):raise ValueError('invalid rig canvas')
    scale=min(size[0]/cw,size[1]/ch);offset=((size[0]-cw*scale)/2,(size[1]-ch*scale)/2)
    textures={};sources={} if _sources is None else _sources
    for name,row in binding['textures'].items():
        spec=row['asset'];grid=spec['grid']
        source_key=json.dumps(spec,sort_keys=True)
        if source_key not in sources:sources[source_key]=_load(spec,root,(grid['cell_width'],grid['cell_height']))
        loaded=sources[source_key]
        index=row['index']
        if type(index) is not int or not 0<=index<len(loaded):raise ValueError('invalid rig texture index')
        from .rig_texture import register
        textures[name]=register(loaded[index],row.get('registration'))
    parts=deepcopy(binding['parts']);base=deepcopy(binding['base_pose'])
    known={p['id']:{'x','y','angle','opacity',*p.get('deformers',{})} for p in parts}
    for p in parts:
        if p.get('minification') not in (None,'area'):raise ValueError('unknown material minification')
        if p.get('parent') is None:
            p['joint']=[p['joint'][i]*scale+offset[i] for i in range(2)]
            pose=base.setdefault(p['id'],{})
            for axis in ('scale_x','scale_y'):pose[axis]=pose.get(axis,1)*scale
            for axis in ('x','y'):pose[axis]=pose.get(axis,0)*scale
        if any(key not in textures for key in [p['texture'],*p.get('expressions',{}).values()]):
            raise ValueError('unknown rig texture')
        for name,d in p.get('deformers',{}).items():
            if name in ('x','y','angle','opacity','scale_x','scale_y') or set(d)!={'kind','region','amplitude_px','origin'} or d['kind']!='tail_vertical' or d['origin'] not in ('left','right'):
                raise ValueError('invalid local deformer')
            box=d['region'];a=d['amplitude_px'];texture=textures[p['texture']]
            if (not isinstance(box,list) or len(box)!=4 or any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=1 for n in box)
                or not box[0]<box[2] or not box[1]<box[3] or type(a) not in (int,float) or not math.isfinite(a) or not 0<a<=min(texture.width,texture.height)*.1):
                raise ValueError('invalid local deformer region')
    matrices(parts,base)
    idle=binding.get('idle')
    if idle is not None:
        if set(idle)!={'channels','blink_code'} or not isinstance(idle['blink_code'],str):raise ValueError('invalid rig idle')
        _channels(idle['channels'],known)
        if not any(idle['blink_code'] in p.get('expressions',{}) for p in parts):raise ValueError('missing rig blink expression')
    rig={'parts':parts,'textures':textures,'base':base,'idle':idle,'scale':scale,'roots':{p['id'] for p in parts if p.get('parent') is None},
         '_binding_key':(str(path),tuple(size))}
    basis=binding.get('pose_basis',{'reflection':1,'pairs':[]})
    if (not isinstance(basis,dict) or set(basis)!={'reflection','pairs'}
        or type(basis['reflection']) is not int or basis['reflection'] not in (-1,1)
        or not isinstance(basis['pairs'],list)):raise ValueError('invalid local pose basis')
    swaps={}
    for pair in basis['pairs']:
        if (not isinstance(pair,list) or len(pair)!=2 or any(not isinstance(p,str) or p not in known or p in swaps for p in pair)
            or pair[0]==pair[1]):raise ValueError('invalid local pose mirror pair')
        a,b=pair;swaps[a]=b;swaps[b]=a
    rig['pose_basis']={'reflection':basis['reflection'],'parts':swaps}
    # Pose-only views share pixels, local units, hit maps and expressions.
    # Never interpolate base scales of unrelated generated artwork.
    rig['_view_material_signature']=json.dumps({k:v for k,v in binding.items()
        if k not in ('base_pose','views','view_transition','facing')},sort_keys=True)
    from .rig_hit import prepare as prepare_hits
    prepare_hits(rig,binding.get('body_regions'),binding['textures'])
    from .rig_overlap import prepare as prepare_overlap
    prepare_overlap(parts,textures,base)
    from .cutout_binding import prepare_static_parts
    prepare_static_parts(parts,textures)
    from .rig_expression import prepare
    prepare(rig,binding.get('expression_regions',{}))
    if 'gaze_part' in binding:
        if binding['gaze_part'] not in rig['expression_materials']:raise ValueError('unknown gaze material')
        rig['gaze_part']=binding['gaze_part']
    if 'gaze_layout' in binding:
        layout=binding['gaze_layout']
        if 'gaze_part' not in rig or not isinstance(layout,dict) or set(layout)!={'regions','displacement','open_eye'} or type(layout['open_eye']) is not bool:
            raise ValueError('invalid material gaze layout')
        texture=rig['expression_materials'][rig['gaze_part']]['mixer'].base
        if not isinstance(layout['regions'],list) or not 1<=len(layout['regions'])<=4:raise ValueError('invalid material eye regions')
        for rect in layout['regions']:
            if len(rect)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in rect) or not (0<=rect[0]<rect[2]<=texture.width and 0<=rect[1]<rect[3]<=texture.height):
                raise ValueError('invalid material eye region')
        if len(layout['displacement'])!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=16 for v in layout['displacement']):raise ValueError('invalid material gaze displacement')
        rig['gaze_layout']=deepcopy(layout)
    from .rig_expression_pose import prepare as prepare_pose
    prepare_pose(rig,binding.get('expression_pose'))
    if 'visibility_bridges' in binding:
        bridges=binding['visibility_bridges']
        if not isinstance(bridges,dict) or set(bridges)-set(known):raise ValueError('invalid visibility bridges')
        for rows in bridges.values():
            prepare_pose({'parts':parts},{'duration_ms':100,'states':{'normal':rows}})
        rig['visibility_bridges']=deepcopy(bridges)
    if 'gait' in binding:
        _channels(binding['gait'],known)
        rig['gait']=deepcopy(binding['gait'])
    if 'input_feedback' in binding:
        feedback=binding['input_feedback']
        if set(feedback)!={'drag','contact'}:raise ValueError('invalid bound input feedback')
        for channels in feedback.values():_channels(channels,known)
        rig['input_feedback']=deepcopy(feedback)
    if 'facing' in binding:
        facing=binding['facing']
        if set(facing)!={'left','right'}:raise ValueError('invalid bound facing sides')
        probe={'parts':parts}
        prepare_pose(probe,{'duration_ms':100,'states':{'normal':{},**facing}})
        rig['facing']=deepcopy(facing)
    if 'views' in binding:
        if not allow_views or set(binding['views'])!={'left','right'}:raise ValueError('invalid nested or incomplete rig views')
        views={0:rig}
        for direction,name in ((-1,'left'),(1,'right')):
            view=load_rig(root,binding['views'][name],size,allow_views=False,_sources=sources)
            if {p['id'] for p in view['parts']}!=set(known):raise ValueError('view part identities differ')
            views[direction]=view
        rig['views']=views
        family=object()
        for direction,view in views.items():
            view['_view_family']=family
            view['_expression_views']=tuple(views.values())
            view['_view_direction']=direction
            view['_view_root']=rig
        if 'view_registration' in binding:
            registration_path=(root/binding['view_registration']).resolve()
            if registration_path.parent!=root:raise ValueError('view registration outside asset directory')
            registration=json.loads(registration_path.read_text('utf8'))
            from .rig_view_blend import validate
            validate(registration,views)
            rig['_view_registration']=registration
        if all(view['_view_material_signature']==rig['_view_material_signature'] for view in views.values()):
            for view in views.values():view['_continuous_view_family']=family
        transition=binding.get('view_transition',{})
        if not isinstance(transition,dict) or set(transition)-set(known):raise ValueError('invalid view transition parts')
        for row in transition.values():
            if not isinstance(row,dict) or set(row)-{'scale_x','scale_y'} or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<v<=1 for v in row.values()):
                raise ValueError('invalid view transition scale')
        rig['view_transition']=deepcopy(transition)
    elif 'view_transition' in binding:raise ValueError('view transition without views')
    return rig


def append(frames, root, binding_name, declarations):
    if not declarations:return frames,{},{}
    first=next(iter(frames.values()))[0];size=(first.width,first.height)
    existing=first.rig if isinstance(first,RigFrame) else None
    key=(str((Path(root)/binding_name).resolve()),tuple(size))
    # Appending another action family must not replace the standing rig while
    # leaving existing action frames attached to a different orientation family.
    rig=existing if existing is not None and existing.get('_binding_key')==key else load_rig(root,binding_name,size)
    base=rig['base'];idle=rig['idle']
    known={p['id']:{'x','y','angle','opacity',*p.get('deformers',{})} for p in rig['parts']}
    result=dict(frames);clips={};mapping={}
    if idle is not None:
        for code,old in result.items():
            rows=list(old);rows[0]=RigFrame(rig,base,code,size)
            if code=='normal' and len(rows)>2:rows[2]=RigFrame(rig,base,idle['blink_code'],size)
            result[code]=tuple(rows)
    for name,row in declarations.items():
        if set(row)-{'bound_turn'}!={'source','duration_ms','channels'}:raise ValueError('invalid bound activity')
        if 'bound_turn' in row and (type(row['bound_turn']) is not int or row['bound_turn'] not in (-1,1) or 'facing' not in rig):raise ValueError('invalid bound turn')
        duration=row['duration_ms']
        if type(duration) is not int or not 300<=duration<=30000:raise ValueError('invalid bound activity duration')
        _channels(row['channels'],known)
        count=max(3,math.ceil(duration/33));start=max(map(len,result.values()))
        poses=[]
        for i in range(count):
            poses.append(_pose(rig,row['channels'],i/(count-1)))
        for code,old in result.items():
            result[code]=old+(old[0],)*(start-len(old))+tuple(RigFrame(rig,pose,code,size) for pose in poses)
        indices=list(range(start,start+count))
        clips[name]={'frames':indices,'durations_ms':[duration//count+(i<duration%count) for i in range(count)],'continuous_expression_motion':True}
        if 'bound_turn' in row:clips[name]['bound_turn']=row['bound_turn']
        mapping.update({i:0 for i in indices})
    return result,clips,mapping
