"""Registered whole-character target-facing assets; no behavioral authority."""
from pathlib import Path
from collections import OrderedDict
import hashlib,json,math
from .windows_alpha import AlphaFrame,load_alpha_frames
from .walk_renderer import _poses,_local_cycle,pose_shift
VERSION='petlab-target-facing-v1'


def validate(value,root):
 root=Path(root).resolve()
 if (value.get('version')!=VERSION or value.get('canvas')!=[640,544] or value.get('front_origin')!=[64,16]
     or value.get('target_anchor')!=[312,512] or value.get('profile_scale')!=.98
     or value.get('look_before_move_ms')!=140 or set(value.get('profiles',{}))!={'left','right'}
     or value.get('whole_manifest_sha256')!=hashlib.sha256((root/'whole-expressions.json').read_bytes()).hexdigest()):raise ValueError('target-facing asset declaration differs')
 for side,row in value['profiles'].items():
  for name,sha in (('image','image_sha256'),('body_map','body_map_sha256')):
   path=(root/row[name]).resolve()
   if path.parent!=root or hashlib.sha256(path.read_bytes()).hexdigest()!=row[sha]:raise ValueError('target-facing source differs')
  if row['grid']!={'columns':3,'rows':2,'cell_width':512,'cell_height':512} or row['codes']!={'normal':0,'happy':1,'sad':2,'aggrieved':3,'angry':4}:raise ValueError('facing atlas layout differs')
  if len(row['anchors'])!=6 or any(len(a)!=2 or any(type(n)is not int or not 0<=n<=512 for n in a) for a in row['anchors']):raise ValueError('facing source anchor invalid')
  for b in [*row['eye_regions'],*row['feet'],row['tail']]:
   if len(b)!=4 or any(type(x)not in (int,float) or not math.isfinite(x) for x in b) or not 0<=b[0]<b[2]<=1 or not 0<=b[1]<b[3]<=1:raise ValueError('facing region invalid')
 return value


def embed(frame,size,offset):
 w,h=size;dx,dy=offset;data=bytearray(w*h*4)
 x0=max(0,dx);x1=min(w,dx+frame.width)
 for y in range(max(0,dy),min(h,dy+frame.height)):
  start=((y-dy)*frame.width+x0-dx)*4;end=start+(x1-x0)*4
  data[(y*w+x0)*4:(y*w+x1)*4]=frame.bgra[start:end]
 return AlphaFrame(w,h,bytes(data))


class FacingAssets:
 def __init__(self,asset_dir,base_size):
  root=Path(asset_dir);path=root/'facing.json';self.available=False;self.base_size=base_size;self.size=base_size;self.front_offset=(0,0);self.cache=OrderedDict()
  self.profiles={};self.body_maps={}
  if not path.exists():return
  self.value=validate(json.loads(path.read_text(encoding='utf8')),root);self.available=True
  self.manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
  self.size=tuple(round(v*base_size[i]/512) for i,v in enumerate(self.value['canvas']))
  self.front_offset=tuple(round(v*base_size[i]/512) for i,v in enumerate(self.value['front_origin']))
  self.source_size=tuple(round(v*self.value['profile_scale']) for v in base_size)
  target=tuple(round(v*base_size[i]/512) for i,v in enumerate(self.value['target_anchor']))
  walk=json.loads((root/'walk.json').read_text(encoding='utf8'))
  from .frame_cache import cached_frames
  from .body_regions import BodyRegionMap
  implementation=hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('walk_renderer.py').read_bytes()).hexdigest()
  for side,direction in (('left',-1),('right',1)):
   row=self.value['profiles'][side];atlas=load_alpha_frames(root/row['image'],row['grid'],output_size=self.source_size)
   body=BodyRegionMap.load(root/row['body_map'],image_sha256=row['image_sha256'],frame_count=6);self.body_maps[body.map_sha256]=body
   group={}
   for code,index in row['codes'].items():
    source=atlas[index];anchor=row['anchors'][index]
    offset=tuple(target[i]-round(anchor[i]*self.source_size[i]/512) for i in (0,1))
    tx=(anchor[0]-row['anchors'][0][0])/512;ty=(anchor[1]-row['anchors'][0][1])/512
    def box(b):return [max(0,min(.999,v+(tx if i%2==0 else ty))) for i,v in enumerate(b)]
    config={**walk,'feet':[box(b) for b in row['feet']],'tail':box(row['tail']),'tail_origin':'right' if side=='right' else 'left'}
    cached={**config,'frame_count':config['frame_count']*2,'phase_count':config['frame_count'],'implementation':implementation,'asset':row['image_sha256'],'code':code}
    gait=cached_frames('target-facing-gait',source,cached,lambda source=source,config=config:_poses(source,config))
    start=0 if direction<0 else config['frame_count'];gait=gait[start:start+config['frame_count']]
    idle_config={**config,'frame_count':24,'foot_sway_px':0,'foot_lift_px':0,'body_bob_px':0,'tail_sway_px':3}
    idle=cached_frames('target-facing-idle',source,{**idle_config,'implementation':implementation},lambda source=source,config=idle_config:tuple(_local_cycle(source,config)))
    group[code]={'gait':tuple(embed(f,self.size,offset) for f in gait),'idle':tuple(embed(f,self.size,offset) for f in idle),
     'offset':offset,'source':source,'config':config,'index':index,'eyes':[box(b) for b in row['eye_regions']],'body':body}
   self.profiles[direction]=group
 def front(self,frame):
  if not self.available:return frame
  key=id(frame)
  if key in self.cache:
   original,result=self.cache.pop(key);self.cache[key]=(original,result);return result
  result=embed(frame,self.size,self.front_offset);self.cache[key]=(frame,result)
  while len(self.cache)>96:self.cache.popitem(last=False)
  return result
 def frame(self,code,direction,*,phase=None,rest_phase=0):
  row=self.profiles[direction][code]
  return row['idle'][rest_phase%24] if phase is None else row['gait'][phase%len(row['gait'])]
 def gaze_layout(self,code,direction,*,phase=None,front_shift=(0,0),front_regions=None):
  if direction==0:
   ox,oy=self.front_offset;ox+=front_shift[0];oy+=front_shift[1];w,h=self.base_size;boxes=front_regions
  else:
   row=self.profiles[direction][code];ox,oy=row['offset'];w,h=self.source_size;boxes=row['eyes']
   if phase is not None:
    sx,sy=pose_shift(row['config'],phase,direction,self.source_size);ox+=sx;oy+=sy
  return {'open_eye':direction!=0,'regions':[[ox+b[0]*w,oy+b[1]*h,ox+b[2]*w,oy+b[3]*h] for b in boxes],
          'displacement':[4*self.base_size[0]/512,3*self.base_size[1]/512]}
 def hit(self,code,direction,x,y,*,is_opaque,phase=None):
  row=self.profiles[direction][code];ox,oy=row['offset']
  if phase is not None:
   sx,sy=pose_shift(row['config'],phase,direction,self.source_size);ox+=sx;oy+=sy
  return row['body'].hit(frame_index=row['index'],x=x-ox,y=y-oy,width=self.source_size[0],height=self.source_size[1],
   is_opaque=lambda px,py:is_opaque(px+ox,py+oy))
