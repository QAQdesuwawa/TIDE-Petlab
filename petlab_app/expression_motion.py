"""Display-only whole-frame head deformation; expression owns the pixels."""
import math
from .windows_alpha import AlphaFrame


def samples(frame, *, angle_degrees, lower_fraction, duration_ms, horizontal_fraction=0, curve=None, binding=None, _indices=None):
    import cv2
    import numpy as np
    binding=validate_binding(binding)
    curve=validate_curve(curve)
    count=max(3,math.ceil(duration_ms/33))
    y,x=np.mgrid[:frame.height,:frame.width].astype(np.float32)
    # Face, eyes and hair share one transform. The deformation fades smoothly
    # across the neck/upper torso; the lower body never moves or gets overlaid.
    u=np.clip((y/frame.height-binding['rigid_until'])/(binding['fixed_from']-binding['rigid_until']),0,1)
    weight=1-u*u*(3-2*u)
    cx,cy=frame.width*binding['pivot'][0],frame.height*binding['pivot'][1]
    pixels=np.frombuffer(frame.bgra,dtype=np.uint8).reshape(frame.height,frame.width,4)
    result=[]
    for i in range(count) if _indices is None else _indices:
        if i in (0,count-1):result.append(frame);continue
        phase=i/(count-1)
        strength=curve_value(curve,phase) if curve is not None else math.sin(math.pi*phase)**2
        a=math.radians(angle_degrees)*strength*weight
        dy=lower_fraction*frame.height*strength*weight
        dx=horizontal_fraction*frame.width*strength*weight
        mx=cx+np.cos(a)*(x-cx-dx)+np.sin(a)*(y-cy-dy)
        my=cy-np.sin(a)*(x-cx-dx)+np.cos(a)*(y-cy-dy)
        data=cv2.remap(pixels,mx.astype(np.float32),my.astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        result.append(AlphaFrame(frame.width,frame.height,data.tobytes()))
    durations=[duration_ms//count+(i<duration_ms%count) for i in range(count)]
    return tuple(result),durations


def append(frames,declarations,*,binding=None):
    result=dict(frames);clips={};mapping={}
    for key,row in declarations.items():
        if not {'source','angle_degrees','lower_fraction','duration_ms'}<=set(row) or set(row)-{'source','angle_degrees','lower_fraction','duration_ms','horizontal_fraction','curve'}:raise ValueError('invalid expression motion declaration')
        a,dy,duration=row['angle_degrees'],row['lower_fraction'],row['duration_ms']
        if (type(a) not in (int,float) or not math.isfinite(a) or abs(a)>12 or
            type(dy) not in (int,float) or not math.isfinite(dy) or not 0<=dy<=.04 or
            type(duration) is not int or not 300<=duration<=3000):raise ValueError('invalid expression motion bounds')
        horizontal=row.get('horizontal_fraction',0)
        if type(horizontal) not in (int,float) or not math.isfinite(horizontal) or abs(horizontal)>.04:raise ValueError('invalid horizontal motion')
        start=max(len(v) for v in result.values())
        for code,old in result.items():
            motion,timing=lazy_samples(old[0],angle_degrees=a,lower_fraction=dy,duration_ms=duration,horizontal_fraction=horizontal,curve=row.get('curve'),binding=binding)
            result[code]=old+(old[0],)*(start-len(old))+motion
        indices=list(range(start,start+len(motion)))
        clips[key]={'frames':indices,'durations_ms':timing,'continuous_expression_motion':True}
        mapping.update({i:0 for i in indices})
    return result,clips,mapping


DEFAULT_BINDING={'version':'petlab-head-binding-v1','pivot':[.49,.51],'rigid_until':.43,'fixed_from':.65}

def validate_binding(value):
    v=DEFAULT_BINDING if value is None else value
    if set(v)!=set(DEFAULT_BINDING) or v['version']!=DEFAULT_BINDING['version']:raise ValueError('invalid head binding')
    if not isinstance(v['pivot'],(list,tuple)) or len(v['pivot'])!=2:raise ValueError('invalid pivot')
    numbers=[*v['pivot'],v['rigid_until'],v['fixed_from']]
    if any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=1 for n in numbers):raise ValueError('invalid binding coordinates')
    if not v['rigid_until']<v['fixed_from']:raise ValueError('invalid deformation band')
    return v

def validate_curve(value):
    if value is None:return None
    if not isinstance(value,list) or not 3<=len(value)<=16:raise ValueError('invalid pose curve')
    previous=-1
    for row in value:
        if not isinstance(row,list) or len(row)!=2 or any(type(n) not in (int,float) or not math.isfinite(n) for n in row):raise ValueError('invalid curve point')
        t,v=row
        if not previous<t<=1 or not 0<=t or not -1<=v<=1:raise ValueError('invalid curve range')
        previous=t
    if value[0]!=[0,0] or value[-1]!=[1,0]:raise ValueError('curve must return to base')
    return value

def curve_value(curve,phase):
    for (t0,v0),(t1,v1) in zip(curve,curve[1:]):
        if phase<=t1:
            t=max(0,min(1,(phase-t0)/(t1-t0)))
            return v0+(v1-v0)*t*t*(3-2*t)
    return 0


from collections import OrderedDict
from threading import RLock
_FRAME_CACHE=OrderedDict()
_FRAME_CACHE_BYTES=0
_FRAME_CACHE_LIMIT=8*1024*1024
_FRAME_LOCK=RLock()

class MotionFrame:
    """Immutable pose recipe; decoded pixels are shared only in a bounded cache."""
    __slots__=('source','index','options','width','height')
    def __init__(self,source,index,options):
        self.source=source;self.index=index;self.options=options
        self.width=source.width;self.height=source.height
    @property
    def bgra(self):
        global _FRAME_CACHE_BYTES
        with _FRAME_LOCK:
            if self in _FRAME_CACHE:
                value=_FRAME_CACHE.pop(self);_FRAME_CACHE[self]=value;return value
            value=samples(self.source,**self.options,_indices=(self.index,))[0][0].bgra
            if len(value)<=_FRAME_CACHE_LIMIT:
                while _FRAME_CACHE and _FRAME_CACHE_BYTES+len(value)>_FRAME_CACHE_LIMIT:
                    _,old=_FRAME_CACHE.popitem(last=False);_FRAME_CACHE_BYTES-=len(old)
                _FRAME_CACHE[self]=value;_FRAME_CACHE_BYTES+=len(value)
            return value

def lazy_samples(frame,**options):
    # Load the renderer during asset setup, not on the first visible motion.
    import cv2
    import numpy
    from copy import deepcopy
    options=deepcopy(options)
    validate_binding(options.get('binding'));validate_curve(options.get('curve'))
    count=max(3,math.ceil(options['duration_ms']/33))
    rows=tuple(frame if i in (0,count-1) else MotionFrame(frame,i,options) for i in range(count))
    timing=[options['duration_ms']//count+(i<options['duration_ms']%count) for i in range(count)]
    return rows,timing

def cache_info():
    with _FRAME_LOCK:return {'frames':len(_FRAME_CACHE),'bytes':_FRAME_CACHE_BYTES,'limit_bytes':_FRAME_CACHE_LIMIT}
