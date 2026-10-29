"""Local pupil-area warp on each complete source frame; no separated face layer."""
import hashlib,json,math
from collections import OrderedDict
from pathlib import Path
from .windows_alpha import AlphaFrame

class GazeRenderer:
 def __init__(self,asset_dir):
  root=Path(asset_dir);self.cache=OrderedDict();self.available=False
  path=root/'gaze.json'
  if not path.exists():return
  value=json.loads(path.read_text(encoding='utf-8'))
  if (set(value)!={'version','whole_manifest_sha256','open_expressions','eye_regions','max_displacement_source_px','source_size'}
      or value['version']!='petlab-local-gaze-v1'
      or value['whole_manifest_sha256']!=hashlib.sha256((root/'whole-expressions.json').read_bytes()).hexdigest()):raise ValueError('gaze asset declaration differs')
  if value['source_size']!=[512,512] or value['max_displacement_source_px']!=[4,3]:raise ValueError('gaze asset scale differs')
  for r in value['eye_regions']:
   if len(r)!=4 or not 0<=r[0]<r[2]<=1 or not 0<=r[1]<r[3]<=1:raise ValueError('gaze region invalid')
  self.value=value;self.available=True
 def supports(self,code):return self.available and code in self.value['open_expressions']
 def apply(self,frame,horizontal,vertical,code,*,layout=None):
  if not self.available or (not self.supports(code) and not (layout and layout.get('open_eye'))):return frame
  hx,vy=round(horizontal*8)/8,round(vertical*8)/8
  if not hx and not vy:return frame
  # Bound the cache by complete displayed frames; no trajectory accumulation.
  layout_key=None if layout is None else (tuple(tuple(b) for b in layout['regions']),tuple(layout['displacement']),layout.get('open_eye',False))
  key=(id(frame),hx,vy,code,layout_key)
  if key in self.cache:
   source,result=self.cache.pop(key);self.cache[key]=(source,result);return result
  w,h=frame.width,frame.height;data=bytearray(frame.bgra)
  displacement=layout['displacement'] if layout is not None else [self.value['max_displacement_source_px'][0]*w/512,self.value['max_displacement_source_px'][1]*h/512]
  dx,dy=hx*displacement[0],vy*displacement[1]
  regions=layout['regions'] if layout is not None else [[b[0]*w,b[1]*h,b[2]*w,b[3]*h] for b in self.value['eye_regions']]
  for r in regions:
   x0,y0,x1,y1=max(0,math.ceil(r[0])),max(0,math.ceil(r[1])),min(w,math.floor(r[2])),min(h,math.floor(r[3]))
   if x1-x0<3 or y1-y0<3:continue
   for y in range(y0,y1):
    for x in range(x0,x1):
     weight=math.sin(math.pi*(x-x0)/(x1-x0-1))**2*math.sin(math.pi*(y-y0)/(y1-y0-1))**2
     sx=max(x0,min(x1-1,x-dx*weight));sy=max(y0,min(y1-1,y-dy*weight))
     ax,ay=math.floor(sx),math.floor(sy);bx,by=min(x1-1,ax+1),min(y1-1,ay+1);fx,fy=sx-ax,sy-ay
     a,b,c,d=(ay*w+ax)*4,(ay*w+bx)*4,(by*w+ax)*4,(by*w+bx)*4;target=(y*w+x)*4
     for ch in range(4):data[target+ch]=round((frame.bgra[a+ch]*(1-fx)+frame.bgra[b+ch]*fx)*(1-fy)+(frame.bgra[c+ch]*(1-fx)+frame.bgra[d+ch]*fx)*fy)
  result=AlphaFrame(w,h,bytes(data));self.cache[key]=(frame,result)
  while len(self.cache)>64:self.cache.popitem(last=False)
  return result
