"""Lossless private cache of pure visual deformation, with no model authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
import hashlib,json,os,struct,sys,tempfile,zlib

VERSION='petlab-lossless-visual-frame-cache-v1'
_MAGIC=b'PETFRAME1\n'
_MAX_RAW=128*1024*1024
_MAX_FILE=64*1024*1024
_CURRENT=ContextVar('petlab_visual_cache',default=None)

@contextmanager
def scope(directory,*,seed_directory=None):
    stats={'hits':0,'misses':0,'invalid':0,'writes':0,'seed_hits':0}
    token=_CURRENT.set((Path(directory),stats,None if seed_directory is None else Path(seed_directory)))
    try:yield stats
    finally:_CURRENT.reset(token)

@lru_cache(maxsize=1)
def _implementation():
    root=Path(__file__).resolve().parent
    return hashlib.sha256(b''.join((root/name).read_bytes() for name in ('frame_cache.py','whole_frame_renderer.py','windows_alpha.py'))).hexdigest()


def _key(kind,frame,config):
    material={'version':VERSION,'kind':kind,'implementation':_implementation(),'python':[sys.implementation.name,*sys.version_info[:2]],
        'platform':sys.platform,'width':frame.width,'height':frame.height,'source':hashlib.sha256(frame.bgra).hexdigest(),'parameters':config}
    return hashlib.sha256(json.dumps(material,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _read(path,key,frame,count):
    from .windows_alpha import AlphaFrame
    with path.open('rb') as source:
        if source.read(len(_MAGIC))!=_MAGIC:raise ValueError('visual cache format differs')
        raw=source.read(4)
        if len(raw)!=4:raise ValueError('visual cache header missing')
        size=struct.unpack('>I',raw)[0]
        if size>4096:raise ValueError('visual cache header exceeds bound')
        header=json.loads(source.read(size));payload=source.read(_MAX_FILE+1)
    if not isinstance(header,dict):raise ValueError('visual cache header must be an object')
    expected=frame.width*frame.height*4*count
    if (type(count)is not int or not 1<=count<=512 or not 0<expected<=_MAX_RAW or len(payload)>_MAX_FILE
        or header.get('version')!=VERSION or header.get('key')!=key
        or header.get('width')!=frame.width or header.get('height')!=frame.height or header.get('count')!=count
        or header.get('bytes')!=expected):raise ValueError('visual cache binding or bounds differ')
    decoder=zlib.decompressobj();pixels=decoder.decompress(payload,expected+1)
    if (len(pixels)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(pixels).hexdigest()!=header.get('sha256')):raise ValueError('visual cache payload differs')
    stride=frame.width*frame.height*4
    return tuple(AlphaFrame(frame.width,frame.height,pixels[i:i+stride]) for i in range(0,expected,stride))


def _write(path,key,frame,frames):
    if not frames or len(frames)>512 or any(f.width!=frame.width or f.height!=frame.height or len(f.bgra)!=frame.width*frame.height*4 for f in frames):return False
    size=frame.width*frame.height*4*len(frames)
    if not 0<size<=_MAX_RAW:return False
    pixels=b''.join(f.bgra for f in frames);payload=zlib.compress(pixels,1)
    if len(payload)>_MAX_FILE:return False
    header=json.dumps({'version':VERSION,'key':key,'width':frame.width,'height':frame.height,'count':len(frames),'bytes':size,'sha256':hashlib.sha256(pixels).hexdigest()},separators=(',',':')).encode()
    temporary=None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        descriptor,temporary=tempfile.mkstemp(prefix=key+'.',suffix='.tmp',dir=path.parent)
        with os.fdopen(descriptor,'wb') as target:
            target.write(_MAGIC);target.write(struct.pack('>I',len(header)));target.write(header);target.write(payload)
        os.replace(temporary,path);temporary=None
        return True
    finally:
        if temporary is not None:
            try:os.unlink(temporary)
            except OSError:pass


def _read_scoped(active,key,frame,count):
    directory,stats,seed=active
    for folder,is_seed in ((directory,False),(seed,True)):
        if folder is None:continue
        try:result=_read(folder/(key+'.frames'),key,frame,count)
        except FileNotFoundError:continue
        except (OSError,ValueError,TypeError,KeyError,zlib.error):
            stats['invalid']+=1;continue
        stats['hits']+=1
        if is_seed:stats['seed_hits']+=1
        return result
    return None


def cached_frames(kind,frame,config,produce):
    active=_CURRENT.get()
    if active is None:return produce()
    directory,stats,_seed=active;key=_key(kind,frame,config);path=directory/(key+'.frames')
    result=_read_scoped(active,key,frame,config['frame_count'])
    if result is not None:return result
    stats['misses']+=1;result=produce()
    try:written=_write(path,key,frame,result)
    except OSError:written=False
    if written:stats['writes']+=1
    return result



def cached_alpha_decode(image_path,grid,*,subsample,output_size,produce):
    """Cache original decoded PNG pixels; no changes to the decoding recipe."""
    active=_CURRENT.get()
    if active is None:return produce()
    if (not isinstance(grid,dict) or any(type(grid.get(k))is not int or grid[k]<=0 for k in ('columns','rows','cell_width','cell_height'))
            or type(subsample)is not int or subsample<=0):return produce()
    width=(grid['cell_width']+subsample-1)//subsample
    height=(grid['cell_height']+subsample-1)//subsample
    if output_size is not None:
        if (not isinstance(output_size,(tuple,list)) or len(output_size)!=2
                or any(type(n)is not int or n<=0 for n in output_size)):return produce()
        width,height=output_size
    count=grid['columns']*grid['rows']
    if not 1<=count<=512 or not 0<width*height*4*count<=_MAX_RAW:return produce()
    try:
        source=Path(image_path)
        if source.stat().st_size>_MAX_FILE:return produce()
        raw=source.read_bytes()
    except (OSError,TypeError):return produce()
    if len(raw)>_MAX_FILE:return produce()
    material={'version':VERSION,'kind':'original-png-decode','implementation':_implementation(),
        'python':[sys.implementation.name,*sys.version_info[:2]],'platform':sys.platform,
        'source':hashlib.sha256(raw).hexdigest(),'grid':{k:grid[k] for k in ('columns','rows','cell_width','cell_height','source_size','regions') if k in grid},
        'subsample':subsample,'output_size':list(output_size) if output_size is not None else None}
    key=hashlib.sha256(json.dumps(material,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    from types import SimpleNamespace
    dimensions=SimpleNamespace(width=width,height=height)
    directory,stats,_seed=active;path=directory/(key+'.frames')
    result=_read_scoped(active,key,dimensions,count)
    if result is not None:return result
    stats['misses']+=1
    result=produce()
    try:
        # A changing source cannot earn a reusable cache entry.
        if source.read_bytes()!=raw:return result
        written=_write(path,key,dimensions,result)
    except OSError:written=False
    if written:stats['writes']+=1
    return result
