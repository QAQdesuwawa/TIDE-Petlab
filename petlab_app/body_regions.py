"""Body hits are local geometry facts, never semantic or action decisions."""
from __future__ import annotations
import hashlib,json,math
from pathlib import Path

VERSION="petlab-body-region-map-v1"

def _number(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)

def _inside(x,y,polygon):
    inside=False
    for (ax,ay),(bx,by) in zip(polygon,polygon[1:]+polygon[:1]):
        cross=(x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross)<=1e-12 and min(ax,bx)-1e-12<=x<=max(ax,bx)+1e-12 and min(ay,by)-1e-12<=y<=max(ay,by)+1e-12:return True
        if (ay>y)!=(by>y) and x<(bx-ax)*(y-ay)/(by-ay)+ax:inside=not inside
    return inside

class BodyRegionMap:
    def __init__(self,record,*,map_sha256,image_sha256,frame_count):
        if not isinstance(record,dict) or set(record)!={"version","image_sha256","regions","frames"} or record["version"]!=VERSION:
            raise ValueError("invalid body region map version or fields")
        if record["image_sha256"]!=image_sha256:raise ValueError("body region map image differs")
        if not isinstance(map_sha256,str) or len(map_sha256)!=64 or any(c not in '0123456789abcdef' for c in map_sha256):raise ValueError("body region map fingerprint invalid")
        if isinstance(frame_count,bool) or not isinstance(frame_count,int) or frame_count<=0:raise ValueError("body map frame count invalid")
        regions=record["regions"];frames=record["frames"]
        if not isinstance(regions,dict) or not regions or not isinstance(frames,dict):raise ValueError("body region definitions missing")
        for key,value in regions.items():
            if not isinstance(key,str) or not key or not isinstance(value,dict) or set(value)!={"label","description","feedback_clip"}:raise ValueError("invalid body region definition")
            if any(not isinstance(value[k],str) or not value[k].strip() for k in ('label','description')):raise ValueError("body region text missing")
            if value['feedback_clip'] is not None and (not isinstance(value['feedback_clip'],str) or not value['feedback_clip']):raise ValueError("invalid body feedback clip")
        for frame,layout in frames.items():
            if not isinstance(frame,str) or not frame.isdigit() or str(int(frame))!=frame or not 0<=int(frame)<frame_count:raise ValueError("body frame is outside bound image")
            if not isinstance(layout,list):raise ValueError("body frame layout invalid")
            ids=[]
            for item in layout:
                if not isinstance(item,dict) or set(item)!={"region_id","polygon"} or item['region_id'] not in regions:raise ValueError("body frame references unknown region")
                ids.append(item['region_id']);polygon=item['polygon']
                if not isinstance(polygon,list) or len(polygon)<3 or any(not isinstance(p,list) or len(p)!=2 or any(not _number(v) or not 0<=v<=1 for v in p) for p in polygon):raise ValueError("invalid normalized body polygon")
                area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(polygon,polygon[1:]+polygon[:1]))
                if abs(area)<=1e-12:raise ValueError("body polygon has no area")
            if len(set(ids))!=len(ids):raise ValueError("duplicate body region in one frame")
        self._record=json.loads(json.dumps(record,allow_nan=False));self.map_sha256=map_sha256;self.image_sha256=image_sha256

    @classmethod
    def load(cls,path,*,image_sha256,frame_count):
        raw=Path(path).read_bytes()
        return cls(json.loads(raw.decode('utf-8')),map_sha256=hashlib.sha256(raw).hexdigest(),image_sha256=image_sha256,frame_count=frame_count)

    def feedback_clip(self,region_id):return self._record['regions'][region_id]['feedback_clip']

    def hit(self,*,frame_index,x,y,width,height,is_opaque):
        if isinstance(frame_index,bool) or not isinstance(frame_index,int) or frame_index<0:raise ValueError("body hit frame invalid")
        if any(not _number(v) for v in (x,y,width,height)) or width<=0 or height<=0:raise ValueError("body hit dimensions invalid")
        if not 0<=x<width or not 0<=y<height or not is_opaque(int(x),int(y)):return None
        nx,ny=x/width,y/height
        for item in self._record['frames'].get(str(frame_index),()):
            if _inside(nx,ny,item['polygon']):
                region_id=item['region_id'];definition=self._record['regions'][region_id]
                return {'version':VERSION,'map_sha256':self.map_sha256,'image_sha256':self.image_sha256,'frame_index':frame_index,
                    'region_id':region_id,'label':definition['label'],'description':definition['description'],'normalized_press':[nx,ny]}
        return None


def validate_body_target(target):
    if target is None:return None
    from collections.abc import Mapping
    if not isinstance(target,Mapping) or set(target)!={'version','map_sha256','image_sha256','frame_index','region_id','label','description','normalized_press'} or target['version']!=VERSION:raise ValueError('invalid body target capture')
    for key in ('map_sha256','image_sha256'):
        value=target[key]
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('invalid body target source fingerprint')
    if isinstance(target['frame_index'],bool) or not isinstance(target['frame_index'],int) or target['frame_index']<0:raise ValueError('invalid body target frame')
    if any(not isinstance(target[k],str) or not target[k].strip() for k in ('region_id','label','description')):raise ValueError('invalid body target definition')
    point=target['normalized_press']
    if not isinstance(point,(list,tuple)) or len(point)!=2 or any(not _number(v) or not 0<=v<1 for v in point):raise ValueError('invalid body target press coordinates')
    return target
