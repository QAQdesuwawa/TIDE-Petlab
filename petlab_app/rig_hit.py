"""Inverse displayed rig geometry into the existing body-target contract."""
import hashlib
import json
import numpy as np
from .body_regions import BodyRegionMap, VERSION, _inside
from .cutout_binding import matrices, deform_coordinates


def prepare(rig, declaration, texture_specs):
    if declaration is None:return
    if set(declaration)!={'regions','parts'}:raise ValueError('invalid rig body regions')
    parts={p['id']:p for p in rig['parts']}
    if set(declaration['parts'])-set(parts):raise ValueError('unknown hit part')
    hits={}
    for identity,polygons in declaration['parts'].items():
        part=parts[identity];spec=texture_specs[part['texture']]
        asset=spec['asset'];grid=asset['grid'];index=spec['index']
        record={'version':VERSION,'image_sha256':asset['image_sha256'],
                'regions':declaration['regions'],'frames':{str(index):polygons}}
        raw=json.dumps(record,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf8')
        mapping=BodyRegionMap(record,map_sha256=hashlib.sha256(raw).hexdigest(),
            image_sha256=asset['image_sha256'],frame_count=grid['columns']*grid['rows'])
        hits[identity]={'map':mapping,'index':index,'anchor':tuple(part['anchor']),
                        'size':(grid['cell_width'],grid['cell_height'])}
        if spec.get('registration') is not None:
            registration=spec['registration']
            matrix=np.vstack([np.asarray(registration['source_to_local'],dtype=float),[0,0,1]])
            inverse=np.linalg.inv(matrix)
            if registration.get('source_rect'):
                inverse[0,2]+=registration['source_rect'][0];inverse[1,2]+=registration['source_rect'][1]
            hits[identity]['inverse_registration']=inverse
    rig['body_hits']=hits


def hit(frame,x,y,*,only_part=None):
    from .rig_view_blend import ViewBlendFrame
    if isinstance(frame,ViewBlendFrame):return frame.hit(x,y)
    rig=frame.rig;transforms=matrices(rig['parts'],frame.pose)
    parts={p['id']:p for p in rig['parts']};visibility={}
    def visible(identity):
        if identity not in visibility:
            parent=parts[identity].get('parent')
            visibility[identity]=frame.pose.get(identity,{}).get('opacity',1)*(visible(parent) if parent else 1)
        return visibility[identity]
    for part in sorted(rig['parts'],key=lambda p:p['order'],reverse=True):
        if only_part is not None and part['id']!=only_part:continue
        if visible(part['id'])<=0:continue
        local=np.linalg.solve(transforms[part['id']],np.array([x,y,1.]))[:2]
        texture=rig['textures'][part.get('expressions',{}).get(frame.code,part['texture'])]
        px,py=local+part['anchor']
        px,py=deform_coordinates(part,frame.pose,texture,np.asarray(px),np.asarray(py))
        px,py=float(px),float(py)
        if not 0<=px<texture.width or not 0<=py<texture.height:continue
        if part.get('clip_polygon') and not _inside(px,py,part['clip_polygon']):continue
        if texture.bgra[(int(py)*texture.width+int(px))*4+3]*visible(part['id'])<16:continue
        binding=rig.get('body_hits',{}).get(part['id'])
        # An opaque sleeve, prop or torso occludes a hand behind it, even
        # when that foreground material has no named touch region.
        if binding is None:return None
        sx=px+binding['anchor'][0]-part['anchor'][0]
        sy=py+binding['anchor'][1]-part['anchor'][1]
        if 'inverse_registration' in binding:
            sx,sy=(binding['inverse_registration']@np.array([sx,sy,1.]))[:2]
        return binding['map'].hit(frame_index=binding['index'],x=sx,y=sy,
            width=binding['size'][0],height=binding['size'][1],is_opaque=lambda x,y:True)
    return None
