"""Pure whole-source walking poses. No model, clocks, or behavioral selection."""
import hashlib,json,math
from pathlib import Path
from .windows_alpha import AlphaFrame
VERSION='petlab-whole-walk-v1'


def validate(config,whole_bytes):
 expected={'version','whole_manifest_sha256','source_size','frame_count','feet','tail','foot_sway_px','foot_lift_px','tail_sway_px','body_bob_px','lean_px','stride_widths'}
 if (not isinstance(config,dict) or set(config)!=expected or config['version']!=VERSION
     or config['whole_manifest_sha256']!=hashlib.sha256(whole_bytes).hexdigest()
     or config['source_size']!=[512,512] or type(config['frame_count'])is not int
     or not 24<=config['frame_count']<=48):raise ValueError('walk asset binding or timeline differs')
 if not isinstance(config['feet'],list) or len(config['feet'])!=2:raise ValueError('walk needs two original leg regions')
 regions=[*config['feet'],config['tail']]
 for i,r in enumerate(regions):
  if (not isinstance(r,list) or len(r)!=4 or any(type(x)not in (int,float) or not math.isfinite(x) for x in r)
      or not 0<=r[0]<r[2]<=1 or not 0<=r[1]<r[3]<=1):raise ValueError('walk region invalid')
  if any(not(r[2]<=b[0] or b[2]<=r[0] or r[3]<=b[1] or b[3]<=r[1]) for b in regions[:i]):raise ValueError('walk regions overlap')
  if r[1]<.6:raise ValueError('walking must preserve the original head and face')
 for key,limit in (('foot_sway_px',24),('foot_lift_px',28),('tail_sway_px',12),('body_bob_px',8),('lean_px',5),('stride_widths',2)):
  v=config[key]
  if type(v)not in (int,float) or not math.isfinite(v) or not 0<v<=limit:raise ValueError('walk visual amplitude invalid')
 return config


def pose_shift(config,phase,direction,size):
 wave=abs(math.sin(2*math.pi*phase/config['frame_count']))
 return (round(direction*config['lean_px']*size[0]/512*wave),-round(config['body_bob_px']*size[1]/512*wave))


def phase_index(distance,total,width,config):
 if any(type(x)not in (int,float) or not math.isfinite(x) or x<0 for x in (distance,total,width)) or width==0:raise ValueError('walk displacement invalid')
 if total==0 or distance<=0 or distance>=total:return 0
 cycles=max(1,round(total/(width*config['stride_widths'])))
 return round(cycles*distance/total*config['frame_count'])%config['frame_count']


def _local_cycle(frame,config):
 w,h=frame.width,frame.height;regions=[]
 for i,r in enumerate([*config['feet'],config['tail']]):
  x0,y0,x1,y1=math.ceil(r[0]*w),math.ceil(r[1]*h),math.floor(r[2]*w),math.floor(r[3]*h)
  if x1-x0<3 or y1-y0<3:raise ValueError('walk original region too small')
  points=[]
  for y in range(y0,y1):
   for x in range(x0,x1):
    weight=(math.sin(math.pi*(x-x0)/(x1-x0-1))**2*math.sin(math.pi*.5*(y-y0)/(y1-y0-1))**2 if i<2 else
            ((1-(x-x0)/(x1-x0-1)) if config.get('tail_origin')=='right' else (x-x0)/(x1-x0-1))**2*math.sin(math.pi*(y-y0)/(y1-y0-1)))
    points.append((x,y,weight))
  regions.append((i,(x0,y0,x1,y1),points))
 result=[]
 for phase in range(config['frame_count']):
  if phase==0:result.append(frame);continue
  data=bytearray(frame.bgra);angle=2*math.pi*phase/config['frame_count'];wave=math.sin(angle)
  advance=.85*wave+.15*(1-math.cos(angle))
  for i,(x0,y0,x1,y1),points in regions:
   footwave=wave if i==0 else -wave
   dx=config['foot_sway_px']*w/512*(advance if i==0 else -advance) if i<2 else 0
   dy=(config['body_bob_px']*abs(wave)-config['foot_lift_px']*max(0,footwave))*h/512 if i<2 else config['tail_sway_px']*h/512*wave
   for x,y,weight in points:
    sx=max(x0,min(x1-1,x-dx*weight));sy=max(y0,min(y1-1,y-dy*weight))
    ax,ay=math.floor(sx),math.floor(sy);bx,by=min(x1-1,ax+1),min(y1-1,ay+1);fx,fy=sx-ax,sy-ay
    a,b,c,d=(ay*w+ax)*4,(ay*w+bx)*4,(by*w+ax)*4,(by*w+bx)*4;dst=(y*w+x)*4
    for ch in range(4):data[dst+ch]=round((frame.bgra[a+ch]*(1-fx)+frame.bgra[b+ch]*fx)*(1-fy)+(frame.bgra[c+ch]*(1-fx)+frame.bgra[d+ch]*fx)*fy)
  result.append(AlphaFrame(w,h,bytes(data)))
 return result


def _poses(frame,config):
 from .whole_frame_renderer import _place_whole_frame
 local=_local_cycle(frame,config);result=[];count=config['frame_count']
 for direction in (-1,1):
  for phase in range(count):
   dx,dy=pose_shift(config,phase,direction,(frame.width,frame.height))
   result.append(_place_whole_frame(local[(phase*direction)%count],[dx,dy],{'cell_width':frame.width,'cell_height':frame.height}))
 return tuple(result)


def build_walk_frames(frames,whole,asset_dir):
 root=Path(asset_dir);path=root/'walk.json'
 if not path.exists():return frames,{},{},None
 config=validate(json.loads(path.read_text(encoding='utf8')),(root/'whole-expressions.json').read_bytes())
 from .whole_frame_renderer import _load,_place_whole_frame
 from .frame_cache import cached_frames
 size=(next(iter(frames.values()))[0].width,next(iter(frames.values()))[0].height)
 original=_load(whole['idle'],root,size);offsets=whole['idle'].get('frame_offsets',{})
 start=max(len(v) for v in frames.values());count=config['frame_count'];result={};clips={};mapping={}
 cached={**config,'frame_count':count*2,'phase_count':count,'implementation':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
 for code,old in frames.items():
  index=whole['idle']['codes'][code]
  source=_place_whole_frame(original[index],offsets.get(str(index),[0,0]),whole['idle']['grid'])
  poses=cached_frames('whole-walk',source,cached,lambda source=source:_poses(source,config))
  result[code]=old+(old[0],)*(start-len(old))+poses
  clips[code]={'left':list(range(start,start+count)),'right':list(range(start+count,start+count*2))}
 mapping.update({i:0 for i in range(start,start+count*2)})
 return result,clips,mapping,config


def build_carry_frames(frames,activities,config):
 """Walk using the complete authored holding pose: never remove the prop."""
 if config is None or 'eat_rice' not in activities:return frames,{},{}
 from .frame_cache import cached_frames
 source=frames['normal'][activities['eat_rice']['frames'][0]]
 carry={**config,'feet':[[.43,.805,.54,.995],[.54,.805,.65,.995]],
        'tail':[.025,.64,.30,.93],'tail_origin':'right'}
 cached={**carry,'frame_count':config['frame_count']*2,'implementation':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
 poses=cached_frames('whole-carry-walk',source,cached,lambda:_poses(source,carry))
 start=max(len(v) for v in frames.values());count=config['frame_count']
 result={code:old+(old[0],)*(start-len(old))+poses for code,old in frames.items()}
 return result,{'left':list(range(start,start+count)),'right':list(range(start+count,start+2*count))},{i:0 for i in range(start,start+2*count)}
