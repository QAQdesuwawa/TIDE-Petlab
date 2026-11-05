"""Local cutout binding prototype: parent transforms shared by art and attachments.

Display-only. Inputs are authored rig data and already-selected pose parameters.
No state, action selection, or memory is read here.
"""
import math
import weakref
from collections import OrderedDict
from threading import RLock
import numpy as np
import cv2
from .windows_alpha import AlphaFrame

_MASK_LIMIT=16*1024*1024
_mask_cache=OrderedDict()
_mask_bytes=0
_mask_lock=RLock()
_mip_cache=OrderedDict()
_mip_bytes=0
_MIP_LIMIT=24*1024*1024


def _minified(source,pixels,polygon,factor,exclusions=()):
    """Bounded immutable source mipmaps; local geometry stays in source units."""
    global _mip_bytes
    key=(id(source),tuple(map(tuple,polygon)) if polygon else None,factor,
         tuple(tuple(map(tuple,item)) for item in exclusions))
    with _mask_lock:
        if key in _mip_cache:
            entry=_mip_cache[key]
            if entry[0]() is source:
                _mip_cache.move_to_end(key);return entry[1]
            _mip_bytes-=entry[2];del _mip_cache[key]
    size=(max(1,math.ceil(source.width/factor)),max(1,math.ceil(source.height/factor)))
    result=cv2.resize(pixels,size,interpolation=cv2.INTER_AREA)
    # The rig already owns the full source. A weak identity guard prevents
    # id reuse without pinning/counting every full atlas for every pet.
    cost=result.nbytes
    if cost<=_MIP_LIMIT:
        with _mask_lock:
            if key not in _mip_cache:
                while _mip_cache and _mip_bytes+cost>_MIP_LIMIT:
                    _,old=_mip_cache.popitem(last=False);_mip_bytes-=old[2]
                _mip_cache[key]=(weakref.ref(source),result,cost);_mip_bytes+=cost
    return result


def _material_pixels(source, polygon, exclusions=()):
    """An authored material boundary in texture coordinates, before deformation."""
    pixels=np.frombuffer(source.bgra,np.uint8).reshape(source.height,source.width,4)
    if polygon is None and not exclusions:return pixels
    if polygon is None:polygon=[[0,0],[source.width,0],[source.width,source.height],[0,source.height]]
    points=np.asarray(polygon,dtype=np.float64)
    if points.ndim!=2 or points.shape[1]!=2 or len(points)<3 or not np.isfinite(points).all():
        raise ValueError('invalid material clip polygon')
    excluded=[]
    for item in exclusions:
        item=np.asarray(item,dtype=np.float64)
        if item.ndim!=2 or item.shape[1]!=2 or len(item)<3 or not np.isfinite(item).all():
            raise ValueError('invalid material exclusion polygon')
        excluded.append(item)
    key=(id(source),tuple(map(tuple,points)),tuple(tuple(map(tuple,item)) for item in excluded))
    global _mask_bytes
    with _mask_lock:
        cached=_mask_cache.get(key)
        if cached is not None and cached[0] is source:
            _mask_cache.move_to_end(key);return cached[1]
    mask=np.zeros((source.height,source.width),np.uint8)
    cv2.fillPoly(mask,[np.rint(points).astype(np.int32)],255)
    for item in excluded:cv2.fillPoly(mask,[np.rint(item).astype(np.int32)],0)
    result=pixels.copy();result[mask==0]=0
    result.flags.writeable=False
    # Both the retained original and prepared pixels count toward the budget.
    cost=len(source.bgra)+result.nbytes
    if cost<=_MASK_LIMIT:
        with _mask_lock:
            if key not in _mask_cache:
                while _mask_cache and _mask_bytes+cost>_MASK_LIMIT:
                    _,old=_mask_cache.popitem(last=False);_mask_bytes-=old[2]
                _mask_cache[key]=(source,result,cost);_mask_bytes+=cost
    return result


def prepare_static_parts(parts,textures):
    """Compile immutable cutout boundaries once, retaining local anchors."""
    for part in parts:
        polygon=part.get('clip_polygon')
        if (polygon is None and not part.get('clip_exclusions')) or part.get('expressions'):continue
        source=textures[part['texture']]
        pixels=_material_pixels(source,polygon,part.get('clip_exclusions',()))
        if part.get('deformers') or polygon is None:
            # Deformer coordinates remain in the original texture canvas.
            x0=y0=0;x1=source.width;y1=source.height
        else:
            points=np.asarray(polygon)
            x0=max(0,int(np.floor(points[:,0].min()))-1);x1=min(source.width,int(np.ceil(points[:,0].max()))+2)
            y0=max(0,int(np.floor(points[:,1].min()))-1);y1=min(source.height,int(np.ceil(points[:,1].max()))+2)
            if x1<=x0 or y1<=y0:raise ValueError('empty static part bounds')
        key='__static_cutout__:'+part['id']
        if key in textures:raise ValueError('duplicate static part texture')
        textures[key]=AlphaFrame(x1-x0,y1-y0,pixels[y0:y1,x0:x1].tobytes())
        part['texture']=key
        part['anchor']=[part['anchor'][0]-x0,part['anchor'][1]-y0]
        part.pop('clip_polygon',None)
        part.pop('clip_exclusions',None)


def matrices(parts,pose):
    by_id={p['id']:p for p in parts}
    if len(by_id)!=len(parts):raise ValueError('duplicate rig part')
    result={};visiting=set()
    def build(key):
        if key in result:return result[key]
        if key in visiting:raise ValueError('cyclic rig')
        visiting.add(key);p=by_id[key]
        parent=p.get('parent');m=np.eye(3) if parent is None else build(parent)
        value=pose.get(key,{})
        x,y=p['joint'];x+=value.get('x',0);y+=value.get('y',0)
        angle=math.radians(value.get('angle',0));sx=value.get('scale_x',1);sy=value.get('scale_y',1)
        if not all(math.isfinite(float(v)) for v in (x,y,angle,sx,sy)) or sx<=0 or sy<=0:raise ValueError('invalid rig transform')
        local=np.array([[math.cos(angle)*sx,-math.sin(angle)*sy,x],[math.sin(angle)*sx,math.cos(angle)*sy,y],[0,0,1]],float)
        result[key]=m@local;visiting.remove(key);return result[key]
    for key in by_id:build(key)
    return result


def attachment(parts,pose,part,point):
    return tuple((matrices(parts,pose)[part]@np.array([*point,1]))[:2])


def deform_coordinates(part,pose,source,map_x,map_y):
    for name,deformer in part.get('deformers',{}).items():
        amount=pose.get(part['id'],{}).get(name,0)
        if not amount:continue
        if not math.isfinite(amount) or abs(amount)>1:raise ValueError('invalid local deformation amount')
        xlo,ylo,xhi,yhi=deformer['region']
        xlo*=source.width;xhi*=source.width;ylo*=source.height;yhi*=source.height
        u=np.clip((map_x-xlo)/(xhi-xlo),0,1)
        v=(map_y-ylo)/(yhi-ylo)
        weight=(1-u if deformer.get('origin','left')=='right' else u)**2
        inside=(map_x>=xlo)&(map_x<=xhi)&(v>=0)&(v<=1)
        # Same fixed-root/zero-Y-edge vertical field as animate_tail,
        # sampled before the shared parent transform, without a new atlas.
        shift=deformer['amplitude_px']*amount*weight*np.sin(np.pi*np.clip(v,0,1))
        map_y=np.where(inside,np.clip(map_y-shift,ylo,yhi-1),map_y).astype(np.float32)
    return map_x,map_y


def render(parts,textures,pose,*,size,expression='normal',layers=None,selected_parts=None):
    transforms=matrices(parts,pose);w,h=size
    by_id={p['id']:p for p in parts};opacities={}
    def opacity(identity):
        if identity in opacities:return opacities[identity]
        value=pose.get(identity,{}).get('opacity',1)
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:raise ValueError('invalid part opacity')
        parent=by_id[identity].get('parent')
        opacities[identity]=value*(opacity(parent) if parent else 1)
        return opacities[identity]
    out=np.zeros((h,w,4),np.float32)
    for part in sorted(parts,key=lambda p:p['order']):
        if selected_parts is not None and part['id'] not in selected_parts:continue
        visibility=opacity(part['id'])
        if visibility==0:continue
        texture=part.get('expressions',{}).get(expression,part['texture'])
        source=textures[texture]
        anchor=part['anchor']
        matrix=transforms[part['id']]@np.array([[1,0,-anchor[0]],[0,1,-anchor[1]],[0,0,1]])
        pixels=_material_pixels(source,part.get('clip_polygon'),part.get('clip_exclusions',()))
        # Include the interpolation support outside the source rectangle.
        corners=matrix@np.array([[-1,source.width+1,source.width+1,-1],[-1,-1,source.height+1,source.height+1],[1,1,1,1]])
        x0=max(0,int(np.floor(corners[0].min())));x1=min(w,int(np.ceil(corners[0].max())))
        y0=max(0,int(np.floor(corners[1].min())));y1=min(h,int(np.ceil(corners[1].max())))
        if x0>=x1 or y0>=y1:continue
        # Sample in global canvas coordinates. Moving the ROI origin must not
        # change interpolation rounding and make stationary edges shimmer.
        inverse=cv2.invertAffineTransform(matrix[:2])
        xx=np.arange(x0,x1,dtype=np.float64)[None,:]
        yy=np.arange(y0,y1,dtype=np.float64)[:,None]
        map_x=(inverse[0,0]*xx+inverse[0,1]*yy+inverse[0,2]).astype(np.float32)
        map_y=(inverse[1,0]*xx+inverse[1,1]*yy+inverse[1,2]).astype(np.float32)
        map_x,map_y=deform_coordinates(part,pose,source,map_x,map_y)
        if part.get('minification')=='area':
            footprint=float(np.linalg.norm(matrix[:2,:2],axis=0).max())
            factor=2**max(0,int(math.floor(math.log2(1/max(footprint,1e-9)))))
            if factor>1:
                pixels=_minified(source,pixels,part.get('clip_polygon'),factor,part.get('clip_exclusions',()))
                map_x=(map_x+.5)*(pixels.shape[1]/source.width)-.5
                map_y=(map_y+.5)*(pixels.shape[0]/source.height)-.5
        layer=cv2.remap(pixels,map_x,map_y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT).astype(np.float32)
        layer*=visibility
        if layers is not None:
            pixels_out=np.zeros((h,w,4),np.uint8)
            pixels_out[y0:y1,x0:x1]=np.clip(np.round(layer),0,255).astype(np.uint8)
            layers[part['id']]=pixels_out
        region=out[y0:y1,x0:x1]
        region[:]=layer+region*(1-layer[:,:,3:4]/255)
    out=np.clip(np.round(out),0,255).astype(np.uint8)
    out[:,:,:3]=np.minimum(out[:,:,:3],out[:,:,3:4])
    return AlphaFrame(w,h,out.tobytes())
