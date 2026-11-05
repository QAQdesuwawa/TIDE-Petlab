"""Bind the selected expression's local material to a running pose."""
import numpy as np
import cv2
from .cutout_binding import _material_pixels,render
from .expression_region import ExpressionRegion
from .local_expression_transition import LocalExpressionTransition
from .windows_alpha import AlphaFrame


def prepare(rig, declarations):
    materials={}
    for identity,row in declarations.items():
        required={'rect','feather','duration_ms'}
        if not required<=set(row) or set(row)-required-{'source_to_local','source_to_local_by_code','interpolation','blink_regions'} or type(row['duration_ms']) is not int or not 40<=row['duration_ms']<=500:
            raise ValueError('invalid rig expression region')
        part=next((p for p in rig['parts'] if p['id']==identity),None)
        if part is None:raise ValueError('unknown expression part')
        def masked(key):
            source=rig['textures'][key]
            return AlphaFrame(source.width,source.height,_material_pixels(source,part.get('clip_polygon'),part.get('clip_exclusions',())).tobytes())
        mixer=ExpressionRegion(masked(part['texture']),row['rect'],feather=row['feather'])
        if 'interpolation' in row:
            if row['interpolation']!='column_shape':raise ValueError('unknown local expression interpolation')
            from .feature_shape_transition import morph as feature_morph
            mixer.interpolate=feature_morph
        registration=None
        if 'source_to_local' in row:
            registration=np.asarray(row['source_to_local'],dtype=float)
            if registration.shape!=(2,3) or not np.isfinite(registration).all() or abs(np.linalg.det(registration[:,:2]))<1e-8:
                raise ValueError('invalid expression material registration')
        original=dict(part.get('expressions',{}));original.setdefault('normal',part['texture'])
        registrations={}
        if 'source_to_local_by_code' in row:
            values=row['source_to_local_by_code']
            if not isinstance(values,dict) or set(values)-set(original):raise ValueError('unknown expression registration code')
            for code,value in values.items():
                matrix=np.asarray(value,dtype=float)
                if matrix.shape!=(2,3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:,:2]))<1e-8:
                    raise ValueError('invalid expression material registration')
                registrations[code]=matrix
        endpoints={}
        for code,key in original.items():
            transform=registrations.get(code,registration)
            source=masked(key) if transform is None else rig['textures'][key]
            if transform is not None:
                pixels=np.frombuffer(source.bgra,np.uint8).reshape(source.height,source.width,4)
                pixels=cv2.warpAffine(pixels,transform,(mixer.base.width,mixer.base.height))
                registered=AlphaFrame(mixer.base.width,mixer.base.height,pixels.tobytes())
                source=AlphaFrame(registered.width,registered.height,_material_pixels(registered,part.get('clip_polygon'),part.get('clip_exclusions',())).tobytes())
            frame=mixer.prepare(source);new_key=f'__bound_expression__:{identity}:{code}'
            if new_key in rig['textures']:raise ValueError('duplicate expression material key')
            rig['textures'][new_key]=frame;part.setdefault('expressions',{})[code]=new_key;endpoints[code]=frame
        part['texture']=part['expressions']['normal']
        # Every endpoint is already clipped; intermediate materials inherit it.
        part.pop('clip_polygon',None)
        part.pop('clip_exclusions',None)
        blink=(rig.get('idle') or {}).get('blink_code')
        for code,source in endpoints.items():
            if code==blink:continue
            if row.get('interpolation')=='column_shape':
                from .feature_shape_transition import _field
                _field(mixer.crop(source))
                continue
            for target_code,target in endpoints.items():
                if target_code not in (code,blink):mixer.prepare_pair(source,target)
        blink_regions=[]
        if 'blink_regions' in row:
            if blink not in endpoints or not isinstance(row['blink_regions'],list) or not 1<=len(row['blink_regions'])<=4:raise ValueError('invalid blink regions')
            for rect in row['blink_regions']:
                blink_regions.append(ExpressionRegion(endpoints['normal'],rect,feather=2))
        materials[identity]={'part':part,'mixer':mixer,'endpoints':endpoints,'duration_ms':row['duration_ms'],'blink_regions':blink_regions}
    rig['expression_materials']=materials


class RigExpressionPresenter:
    def __init__(self):self.reset()

    def reset(self):
        self.rig=None;self.tracks={};self.pose_track=None
        self._view_states={};self._selected_code=None

    def _select_view(self,rig,code,now_ms):
        from .rig_expression_pose import Transition,endpoint
        family=rig.get('_view_family')
        if self.rig is not rig and (self.rig is None or family is None or family is not self.rig.get('_view_family')):
            self.reset()
        views=rig.get('_expression_views',(rig,))
        blink=(rig.get('idle') or {}).get('blink_code')
        if code!=blink and code!=self._selected_code:
            # Retarget local materials at one clock instant. Inactive views do
            # not render; they only sample their old transition on retarget.
            for view in views:
                state=self._view_states.setdefault(id(view),{'tracks':{},'pose':None})
                for identity,material in view.get('expression_materials',{}).items():
                    value=material['endpoints'].get(code,material['endpoints']['normal'])
                    track=state['tracks'].get(identity)
                    if track is None:state['tracks'][identity]=LocalExpressionTransition(value,interpolate=material['mixer'])
                    else:track.retarget(value,now_ms,duration_ms=material['duration_ms'])
                config=view.get('expression_pose')
                if config:
                    values=endpoint(view,code)
                    if state['pose'] is None:state['pose']=Transition(values)
                    else:state['pose'].retarget(values,now_ms,config['duration_ms'])
            self._selected_code=code
        state=self._view_states.setdefault(id(rig),{'tracks':{},'pose':None})
        self.rig=rig;self.tracks=state['tracks'];self.pose_track=state['pose']

    def present(self, frame, now_ms, *, material_transform=None, _layers=None, _poses=None, _parts=None):
        from .rig_view_blend import ViewBlendFrame
        if isinstance(frame,ViewBlendFrame):
            layers=[];poses=[];active=False
            for endpoint_frame,selected in zip(frame.endpoints,frame.layer_selection()):
                layer={};pose=[]
                transform=(material_transform.for_view(endpoint_frame.rig)
                           if hasattr(material_transform,'for_view') else material_transform)
                _,busy=self.present(endpoint_frame,now_ms,material_transform=transform,_layers=layer,_poses=pose,_parts=selected)
                layers.append(layer);poses.append(pose[0]);active|=busy
            return frame.composite(layers,poses),active
        rig=frame.rig;materials=rig.get('expression_materials',{})
        def rendered(overrides,pose):
            from .rig_motion_marks import behind_limbs
            result=render(rig['parts'],{**rig['textures'],**overrides},pose,
                          size=(frame.width,frame.height),expression=frame.code,layers=_layers,selected_parts=_parts)
            if _poses is not None:_poses.append(pose)
            return AlphaFrame(result.width,result.height,
                behind_limbs(result.bgra,rig,pose,(result.width,result.height)))
        self._select_view(rig,frame.code,now_ms)
        blink=(rig.get('idle') or {}).get('blink_code')
        from .rig_expression_pose import Transition,compose,endpoint
        config=rig.get('expression_pose')
        if frame.code==blink:
            pose=frame.pose
            if config and self.pose_track is not None:
                values=self.pose_track.sample(now_ms)
                declared=endpoint(rig,blink)
                values.update({key:declared[key] for key in config['specified'].get(blink,())})
                pose=compose(frame.pose,values)
            overrides={}
            for identity,material in materials.items():
                track=self.tracks.get(identity)
                if material.get('blink_regions'):
                    current=track.sample(now_ms) if track is not None else material['endpoints']['normal']
                    for region in material['blink_regions']:
                        current=ExpressionRegion(current,region.rect,feather=2).prepare(material['endpoints'][blink])
                    overrides[material['part']['expressions'][blink]]=current
                    continue
                if blink in material['endpoints']:continue
                if track is not None:
                    # Blink owns only explicitly authored blink features.
                    # Preserve the currently displayed mouth, including a
                    # mid-transition sample, rather than reverting to normal.
                    overrides[material['part']['texture']]=track.sample(now_ms)
            if overrides or self.pose_track is not None:
                return rendered(overrides,pose),False
            return (rendered({},pose) if _layers is not None else frame),False
        pose=frame.pose;pose_active=False
        if config:
            values=endpoint(rig,frame.code)
            if self.pose_track is None:self.pose_track=Transition(values)
            else:self.pose_track.retarget(values,now_ms,config['duration_ms'])
            pose=compose(frame.pose,self.pose_track.sample(now_ms));pose_active=self.pose_track.active(now_ms)
        if not materials and not pose_active:return (rendered({},pose) if _layers is not None else frame),False
        overrides={};active=False
        for identity,material in materials.items():
            endpoint=material['endpoints'].get(frame.code,material['endpoints']['normal'])
            track=self.tracks.get(identity)
            if track is None:
                track=LocalExpressionTransition(endpoint,interpolate=material['mixer']);self.tracks[identity]=track
            else:track.retarget(endpoint,now_ms,duration_ms=material['duration_ms'])
            active|=track.source is not track.target and now_ms<track.started_ms+track.duration_ms
            key=material['part']['expressions'].get(frame.code,material['part']['texture'])
            overrides[key]=track.sample(now_ms)
            if material_transform is not None:
                overrides[key]=material_transform(identity,overrides[key],frame.code,pose)
        if not active and not pose_active and material_transform is None and _layers is None:return frame,False
        return rendered(overrides,pose),active or pose_active
