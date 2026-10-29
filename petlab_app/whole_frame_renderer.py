"""Whole-character asset selection only; never crop or composite face/body parts."""
from pathlib import Path
import hashlib
from .windows_alpha import load_alpha_frames
from model_packages.desktop_pet_v1.face_expression import CODES

def _load(source,root,size):
    root=Path(root).resolve();path=(root/source['image']).resolve()
    if path.parent!=root or hashlib.sha256(path.read_bytes()).hexdigest()!=source['image_sha256']:
        raise ValueError('whole-character asset source differs')
    return load_alpha_frames(path,source['grid'],output_size=size)


def contact_bindings(declaration,known):
    """Bind cached expression codes to local clips; never infer psychology."""
    from model_packages.desktop_pet_v1.face_expression import CODES
    if declaration['version']=='petlab-contact-motion-v1':
        if not set(declaration['clips']).issubset(known):raise ValueError('unknown contact binding')
        return {}
    rows=declaration['expression_variants']
    if not set(rows).issubset(known):raise ValueError('unknown contact base binding')
    used=set(rows)
    for row in rows.values():
        if set(row)!=set(CODES) or any(value not in declaration['clips'] for value in row.values()):
            raise ValueError('invalid contact expression variant')
        used.update(row.values())
    if set(declaration['clips'])!=used:raise ValueError('unbound contact clip')
    return rows


def build_contact_frames(frames,declaration):
    """Finite whole-sprite input acknowledgement; no emotion or state selection."""
    if declaration.get('version')=='petlab-contact-motion-v3':return _build_limb_contacts(frames,declaration)
    expected={'version','clips','expression_variants'} if declaration.get('version')=='petlab-contact-motion-v2' else {'version','clips'}
    if set(declaration)!=expected or declaration['version'] not in {'petlab-contact-motion-v1','petlab-contact-motion-v2'}:
        raise ValueError('invalid contact motion declaration')
    result=dict(frames);clips={};mapping={};offsets={}
    for name,row in declaration['clips'].items():
        if set(row)!={'offsets_px','durations_ms','blink_samples'}:raise ValueError('invalid contact clip')
        points=row['offsets_px'];durations=row['durations_ms'];blink=row['blink_samples']
        if (not isinstance(points,list) or not 2<=len(points)<=32 or len(durations)!=len(points)
            or type(blink) is not int or not 0<=blink<len(points)
            or any(type(t) is not int or not 10<=t<=200 for t in durations)
            or sum(durations)>1000 or points[-1]!=[0,0]
            or any(not isinstance(p,list) or len(p)!=2 or any(type(v) is not int or abs(v)>6 for v in p) for p in points)):
            raise ValueError('invalid contact timeline')
        start=max(len(v) for v in result.values());indices=list(range(start,start+len(points)))
        for code,old in result.items():
            original=old[0];grid={'cell_width':original.width,'cell_height':original.height}
            result[code]=old+(original,)*(start-len(old))+tuple(
                _place_whole_frame(old[2] if code=='normal' and i<blink else original,p,grid) for i,p in enumerate(points))
        clips[name]={'frames':indices,'durations_ms':durations}
        mapping.update({n:2 if i<blink else 0 for i,n in enumerate(indices)})
        offsets.update(dict(zip(indices,points)))
    return result,clips,mapping,offsets


def _build_limb_contacts(frames,declaration):
    """Whole reaction pose, fixed head/trunk, two local extremity cycles."""
    import json
    if set(declaration)!={'version','clips','expression_variants'}:
        raise ValueError('invalid limb contact declaration')
    result=dict(frames);clips={};mapping={};offsets={};cache={}
    for name,row in declaration['clips'].items():
        if set(row)!={'pose_codes','cycles','motion'} or row['cycles']!=2 or set(row['pose_codes'])!=set(CODES):
            raise ValueError('invalid limb contact clip')
        motion=_drag_motion({'drag_motion':row['motion']})
        if motion.get('whole_displacement_px')!=[0,0] or any(r['region'][1]<.5 for r in motion['regions']):
            raise ValueError('contact must keep whole placement and head fixed')
        count=motion['frame_count']*2+1
        start=max(len(v) for v in result.values());indices=list(range(start,start+count))
        for code,old in result.items():
            pose=row['pose_codes'][code]
            if pose not in (*CODES,'blink'):raise ValueError('unknown whole contact pose')
            original=frames['normal'][2] if pose=='blink' else frames[pose][0]
            key=(pose,json.dumps(motion,sort_keys=True))
            if key not in cache:
                cycle=animate_drag(original,motion)
                cycle=cycle[1:]+cycle[:1]
                cache[key]=cycle*2+(frames[code][0],)
            # Final frame is restored independently for each persistent expression.
            samples=cache[key][:-1]+(frames[code][0],)
            result[code]=old+(original,)*(start-len(old))+samples
        clips[name]={'frames':indices,'durations_ms':motion['durations_ms']*2+[40]}
        mapping.update({n:2 if n!=indices[-1] else 0 for n in indices})
        offsets.update({n:[0,0] for n in indices})
    return result,clips,mapping,offsets


def activity_clips(declaration, tail_clip):
    """Asset-only timing; no behavior eligibility, emotion or state writes."""
    if set(declaration)!={'version','clips'} or declaration['version']!='petlab-local-activities-v1':
        raise ValueError('invalid local activity declaration')
    result={}
    for key,row in declaration['clips'].items():
        if (not isinstance(key,str) or not key or set(row)!={'source','cycles','frame_duration_ms','settle_ms'}
                or row['source']!='tail_motion' or any(type(row[n]) is not int or row[n]<=0 for n in ('cycles','frame_duration_ms','settle_ms'))
                or row['cycles']>8):raise ValueError('invalid activity visual clip')
        frames=list(tail_clip['frames'])*row['cycles']
        result[key]={'frames':frames+[frames[0]],
                     'durations_ms':[row['frame_duration_ms']]*len(frames)+[row['settle_ms']]}
    return result

def build_activity_frames(frames,manifest,asset_dir,declaration,tail_clip):
    """Append action-only tail samples from the immutable source; retain old slots."""
    if declaration.get('version')=='petlab-local-activities-v4-atlas':
        return _atlas_activity_frames(frames,manifest,asset_dir,declaration,tail_clip)
    if declaration.get('version')=='petlab-local-activities-v3':
        return _whole_activity_frames(frames,manifest,asset_dir,declaration,tail_clip)
    if declaration.get('version')=='petlab-local-activities-v1':
        return frames,activity_clips(declaration,tail_clip),{}
    if set(declaration)!={'version','clips'} or declaration['version']!='petlab-local-activities-v2':
        raise ValueError('invalid local activity declaration')
    import math
    result=dict(frames);clips={};mapping={}
    first=next(iter(frames.values()))[0]
    source=manifest['idle'];idle=_load(source,asset_dir,(first.width,first.height))
    for key,row in declaration['clips'].items():
        scale=row.get('amplitude_scale')
        if isinstance(scale,bool) or not isinstance(scale,(int,float)) or not math.isfinite(scale) or not 1<=scale<=2:
            raise ValueError('invalid activity amplitude')
        timing={k:v for k,v in row.items() if k!='amplitude_scale'}
        offset=max(len(v) for v in result.values());count=manifest['tail_motion'].get('playback_frame_count',48)
        config={**manifest['tail_motion'],'frame_count':count,'amplitude_px':manifest['tail_motion']['amplitude_px']*scale}
        clips.update(activity_clips({'version':'petlab-local-activities-v1','clips':{key:timing}},
                                   {'frames':list(range(offset,offset+count))}))
        clips[key]['frames'][-1]=offset+count
        for code,old in result.items():
            index=source['codes'][code]
            original=_place_whole_frame(idle[index],source.get('frame_offsets',{}).get(str(index),[0,0]),source['grid'])
            motion=manifest.get('emotion_motion',{}).get(code)
            dense=_emotion_playback(motion,base_frame_count=manifest['tail_motion']['frame_count']) if motion else None
            # Reuse the selected expression's complete happy wag for this same
            # play_tail action; no alternate action selection or new model call.
            samples=tuple(old[manifest['tail_motion']['frame_count']+6+i] for i in range(len(dense[0]))) if dense else animate_tail(original,config)
            if len(samples)!=count:raise ValueError('activity and authored motion sample counts differ')
            rest=samples[0] if dense else original
            result[code]=old+(rest,)*(offset-len(old))+samples+(rest,)
        mapping.update({i:0 for i in range(offset,offset+count+1)})
    return result,clips,mapping


def _atlas_activity_frames(frames,manifest,asset_dir,declaration,tail_clip):
    """Complete authored poses; a sprite sequence remains one actual action."""
    old={k:v for k,v in declaration['clips'].items() if v.get('source')!='whole_atlas'}
    result,clips,mapping=build_activity_frames(frames,manifest,asset_dir,{'version':'petlab-local-activities-v3','clips':old},tail_clip)
    first=next(iter(result.values()))[0];sources={}
    for key,row in declaration['clips'].items():
        if row.get('source')!='whole_atlas':continue
        if set(row)!={'source','asset','poses','durations_ms'}:raise ValueError('invalid atlas activity')
        spec=row['asset'];cache_key=spec['image_sha256']
        if cache_key not in sources:sources[cache_key]=_load(spec,asset_dir,(first.width,first.height))
        poses=sources[cache_key];indices=row['poses'];timing=row['durations_ms']
        if not 2<=len(indices)<=240 or len(indices)!=len(timing) or sum(timing)>30000 or any(type(t)is not int or not 0<t<=1000 for t in timing) or any(type(i)is not int or not 0<=i<len(poses) for i in indices):raise ValueError('invalid atlas activity sequence')
        start=max(len(v) for v in result.values());samples=tuple(poses[i] for i in indices)
        for code,old_frames in result.items():result[code]=old_frames+(poses[0],)*(start-len(old_frames))+samples
        clips[key]={'frames':list(range(start,start+len(samples))),'durations_ms':list(timing)}
        mapping.update({i:0 for i in range(start,start+len(samples))})
    return result,clips,mapping


def _place_whole_frame(frame, offset, grid):
    from .windows_alpha import AlphaFrame
    if not isinstance(offset,list) or len(offset)!=2 or any(isinstance(v,bool) or not isinstance(v,int) for v in offset):
        raise ValueError('whole-frame placement must be integer source pixels')
    dx=round(offset[0]*frame.width/grid['cell_width']);dy=round(offset[1]*frame.height/grid['cell_height'])
    if abs(dx)>=frame.width or abs(dy)>=frame.height:raise ValueError('whole-frame placement exceeds cell')
    if dx==0 and dy==0:return frame
    target=bytearray(len(frame.bgra));width=frame.width
    x0=max(0,dx);x1=min(width,width+dx)
    for y in range(max(0,dy),min(frame.height,frame.height+dy)):
        start=((y-dy)*width+x0-dx)*4;end=start+(x1-x0)*4
        target[(y*width+x0)*4:(y*width+x1)*4]=frame.bgra[start:end]
    return AlphaFrame(frame.width,frame.height,bytes(target))


def _map_drag_frames(frames, source):
    indices=source.get('frame_indices',list(range(6)))
    if not isinstance(indices,list) or len(indices)!=6 or any(isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(frames) for i in indices):
        raise ValueError('whole drag frame mapping invalid')
    return tuple(frames[i] for i in indices)

def _emotion_playback(source,*,base_frame_count):
    """Versioned decoded frame samples; visual timing never advances model time."""
    dense=source.get('playback')
    if dense is None:return None
    if (not isinstance(dense,dict) or set(dense)!={'version','frame_indices','durations_ms','body_frame_indices'}
            or dense['version']!='whole-emotion-playback-v1'):
        raise ValueError('dense emotion playback schema invalid')
    indices=dense['frame_indices'];durations=dense['durations_ms'];body=dense['body_frame_indices']
    size=source['grid']['columns']*source['grid']['rows']
    if (not isinstance(indices,list) or not 2<=len(indices)<=240
            or not isinstance(durations,list) or not isinstance(body,list)
            or len(indices)!=len(durations) or len(indices)!=len(body)
            or any(type(i)is not int or not 0<=i<size for i in indices)
            or any(type(i)is not int or not 0<=i<base_frame_count for i in body)
            or any(type(t)is not int or t<=0 for t in durations)):
        raise ValueError('dense emotion playback samples invalid')
    return indices,durations,body


def build_whole_frames(base_frames,manifest,asset_dir,base_frame_count):
    if manifest.get('version')!='petlab-whole-expression-assets-v1' or set(manifest['idle']['codes'])!=set(CODES) or set(manifest['drag'])!=set(CODES):
        raise ValueError('whole-character expression registry differs')
    if len(base_frames)-base_frame_count!=6:raise ValueError('whole drag atlas requires six physical frame slots')
    size=(base_frames[0].width,base_frames[0].height)
    idle=_load(manifest['idle'],asset_dir,size);result={}
    offsets=manifest['idle'].get('frame_offsets',{})
    if not isinstance(offsets,dict) or any(not str(i).isdigit() or not 0<=int(i)<len(idle) for i in offsets):
        raise ValueError('whole idle placement frame invalid')
    idle=tuple(_place_whole_frame(frame,offsets.get(str(i),[0,0]),manifest['idle']['grid']) for i,frame in enumerate(idle))
    dense=_dense_tail_playback(manifest,base_frame_count=base_frame_count)
    drag_motion=_drag_motion(manifest)
    motions=manifest.get('emotion_motion',{})
    if not isinstance(motions,dict) or not set(motions).issubset(CODES):raise ValueError('whole emotion motion codes invalid')
    for code in CODES:
        index=manifest['idle']['codes'][code];blink=manifest['idle']['neutral_blink_frame']
        if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(idle) or not isinstance(blink,int) or not 0<=blink<len(idle):
            raise ValueError('whole idle frame index invalid')
        drag=_map_drag_frames(_load(manifest['drag'][code],asset_dir,size),manifest['drag'][code])
        if len(drag)!=6:raise ValueError('whole drag frame count invalid')
        # Every output entry is an entire decoded frame, including its own alpha.
        standing=tuple(idle[blink if code=='normal' and i==2 else index] for i in range(base_frame_count))
        if "tail_motion" in manifest:
            standing=animate_tail(idle[index],manifest["tail_motion"])
            if len(standing)!=base_frame_count:raise ValueError("tail motion must fit existing frame slots")
            # Slot 2 is the existing contact-blink frame. Dense tail playback
            # below has its own slots and must not consume this authored pose.
            if code=='normal':standing=standing[:2]+(idle[blink],)+standing[3:]
        if code in motions:
            source=motions[code];frames=_load(source,asset_dir,size);indices=source.get('frame_indices')
            if (not isinstance(indices,list) or len(indices)!=base_frame_count
                    or any(isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(frames) for i in indices)):
                raise ValueError('whole emotion motion frame mapping invalid')
            offsets=source.get('frame_offsets',{})
            if not isinstance(offsets,dict) or any(not str(i).isdigit() or not 0<=int(i)<len(frames) for i in offsets):
                raise ValueError('whole emotion motion placement invalid')
            frames=tuple(_place_whole_frame(f,offsets.get(str(i),[0,0]),source['grid']) for i,f in enumerate(frames))
            standing=tuple(frames[i] for i in indices)
        result[code]=standing+tuple(drag)
        motion_dense=_emotion_playback(motions[code],base_frame_count=base_frame_count) if code in motions else None
        if motion_dense is not None:
            result[code]+=tuple(frames[i] for i in motion_dense[0])
        elif dense is not None and code not in motions:
            config,_clip=dense
            result[code]+=animate_tail(idle[index],config)
        if drag_motion is not None:
            result[code]+=animate_drag(drag[0],drag_motion)
    return result


def emotion_motion_clips(manifest,*,base_frame_count=None):
    result={}
    for code,source in manifest.get('emotion_motion',{}).items():
        clip=source.get('visual_clip')
        if (code not in CODES or not isinstance(clip,dict)
                or set(clip)!={'frames','durations_ms'}
                or not isinstance(clip['frames'],list) or not clip['frames']
                or not isinstance(clip['durations_ms'],list) or len(clip['durations_ms'])!=len(clip['frames'])
                or any(isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(source['frame_indices']) for i in clip['frames'])
                or any(isinstance(t,bool) or not isinstance(t,int) or t<=0 for t in clip['durations_ms'])):
            raise ValueError('emotion motion visual clip invalid')
        dense=_emotion_playback(source,base_frame_count=base_frame_count or len(source['frame_indices']))
        result[code]=({'frames':list(range((base_frame_count or len(source['frame_indices']))+6,
                        (base_frame_count or len(source['frame_indices']))+6+len(dense[0]))),
                      'durations_ms':list(dense[1])} if dense is not None else clip)
    if 'tail_motion' in manifest:
        clip=manifest['tail_motion']['visual_clip'];count=manifest['tail_motion']['frame_count']
        if (set(clip)!={'frames','durations_ms'} or len(clip['frames'])!=len(clip['durations_ms'])
                or not clip['frames'] or any(isinstance(i,bool) or not isinstance(i,int) or not 0<=i<count for i in clip['frames'])
                or any(isinstance(t,bool) or not isinstance(t,int) or t<=0 for t in clip['durations_ms'])):
            raise ValueError('tail display clip invalid')
        result.update({c:clip for c in CODES if c not in result})
        dense=_dense_tail_playback(manifest,base_frame_count=base_frame_count)
        if dense is not None:
            result.update({c:dense[1] for c in CODES if c not in manifest.get('emotion_motion',{})})
    return result



def _dense_tail_playback(manifest,*,base_frame_count=None):
    """Optional visual samples appended after legacy pose/action/drag slots.

    Subdivide the existing phase durations; neither gesture nor model time is
    advanced. The old atlas indices and their pixels remain unchanged.
    """
    tail=manifest.get('tail_motion',{})
    count=tail.get('playback_frame_count')
    if count is None:return None
    original=tail.get('frame_count');clip=tail.get('visual_clip',{})
    if base_frame_count is None:base_frame_count=original
    if (isinstance(original,bool) or not isinstance(original,int) or not 2<=original<=64
            or isinstance(base_frame_count,bool) or not isinstance(base_frame_count,int) or base_frame_count!=original
            or isinstance(count,bool) or not isinstance(count,int) or not original<count<=240 or count%original
            or clip.get('frames')!=list(range(original))
            or not isinstance(clip.get('durations_ms'),list) or len(clip['durations_ms'])!=original):
        raise ValueError('tail playback subdivision is invalid')
    factor=count//original;durations=[]
    for duration in clip['durations_ms']:
        if isinstance(duration,bool) or not isinstance(duration,int) or duration<factor:
            raise ValueError('tail playback interval cannot be subdivided')
        durations.extend((i+1)*duration//factor-i*duration//factor for i in range(factor))
    config={**tail,'frame_count':count}
    offset=base_frame_count+6
    return config,{'frames':list(range(offset,offset+count)),'durations_ms':durations}


def visual_body_frame_indices(manifest,*,base_frame_count,expression_code='normal'):
    """Map display samples to legacy poses, including no-hit drag slots."""
    dense=_dense_tail_playback(manifest,base_frame_count=base_frame_count)
    motion=manifest.get('emotion_motion',{}).get(expression_code)
    emotion=_emotion_playback(motion,base_frame_count=base_frame_count) if motion is not None else None
    count=(len(emotion[0]) if emotion is not None else dense[0]['frame_count']
           if dense is not None and motion is None else 0)
    result={base_frame_count+6+i:(emotion[2][i] if emotion is not None else i//(count//base_frame_count)) for i in range(count)}
    motion=_drag_motion(manifest)
    if motion is not None:
        result.update({base_frame_count+6+count+i:base_frame_count+1 for i in range(motion['frame_count'])})
    return result


def animate_tail(frame,config):
    from .frame_cache import cached_frames
    return cached_frames('tail',frame,config,lambda:_animate_tail_uncached(frame,config))


def _animate_tail_uncached(frame,config):
    """Display deformation of one immutable frame; all pixels outside ROI stay exact."""
    import math
    from .windows_alpha import AlphaFrame
    region=config['region'];count=config['frame_count'];amplitude=config['amplitude_px']
    if (not isinstance(region,list) or len(region)!=4
            or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in region)
            or not region[0]<region[2] or not region[1]<region[3]
            or isinstance(count,bool) or not isinstance(count,int) or not 2<=count<=64
            or isinstance(amplitude,bool) or not isinstance(amplitude,(int,float)) or not math.isfinite(amplitude) or not 0<amplitude<=min(frame.width,frame.height)*.1):
        raise ValueError('tail display deformation invalid')
    x0=math.ceil(region[0]*frame.width);x1=min(frame.width,math.floor(region[2]*frame.width))
    y0=math.ceil(region[1]*frame.height);y1=min(frame.height,math.floor(region[3]*frame.height))
    if x1-x0<2 or y1-y0<2:raise ValueError('tail display region too small')
    frames=[]
    for phase in range(count):
        data=bytearray(frame.bgra);wave=math.sin(phase*2*math.pi/count+.13)
        for x in range(x0,x1):
            weight=((x-x0)/(x1-x0-1))**2
            for y in range(y0,y1):
                dy=amplitude*wave*weight*math.sin(math.pi*(y-y0)/(y1-y0-1))
                sample=max(y0,min(y1-1,y-dy));lo=math.floor(sample);hi=min(y1-1,lo+1);mix=sample-lo
                dst=(y*frame.width+x)*4;a=(lo*frame.width+x)*4;b=(hi*frame.width+x)*4
                for channel in range(4):data[dst+channel]=round(frame.bgra[a+channel]*(1-mix)+frame.bgra[b+channel]*mix)
        frames.append(AlphaFrame(frame.width,frame.height,bytes(data)))
    return tuple(frames)


def _drag_motion(manifest):
    import math
    motion=manifest.get('drag_motion')
    if motion is None:return None
    if not isinstance(motion,dict) or set(motion)-{'whole_displacement_px'}!={'version','frame_count','durations_ms','regions'}:
        raise ValueError('drag motion fields invalid')
    count=motion['frame_count'];durations=motion['durations_ms'];regions=motion['regions']
    if (motion['version']!='local-whole-drag-deformation-v1' or type(count) is not int or not 4<=count<=120
            or not isinstance(durations,list) or len(durations)!=count or any(type(t) is not int or t<=0 for t in durations)
            or not isinstance(regions,list) or not 1<=len(regions)<=8):raise ValueError('drag motion timeline invalid')
    def finite(v):return not isinstance(v,bool) and isinstance(v,(int,float)) and math.isfinite(v)
    whole=motion.get('whole_displacement_px',[0,0])
    if not isinstance(whole,list) or len(whole)!=2 or any(not finite(v) or abs(v)>6 for v in whole):
        raise ValueError('whole drag displacement invalid')
    prior=[]
    for item in regions:
        if not isinstance(item,dict) or set(item)!={'region','displacement','phase'}:raise ValueError('drag motion region invalid')
        box=item['region'];vector=item['displacement'];phase=item['phase']
        if (not isinstance(box,list) or len(box)!=4 or any(not finite(v) or not 0<=v<=1 for v in box)
                or not box[0]<box[2] or not box[1]<box[3]
                or not isinstance(vector,list) or len(vector)!=2 or any(not finite(v) or abs(v)>.04 for v in vector)
                or not any(vector) or not finite(phase) or phase not in (0,.5)):
            raise ValueError('drag motion displacement invalid')
        if any(not (box[2]<=b[0] or b[2]<=box[0] or box[3]<=b[1] or b[3]<=box[1]) for b in prior):
            raise ValueError('drag motion regions overlap')
        prior.append(box)
    return motion


def drag_motion_clips(manifest,*,base_frame_count):
    motion=_drag_motion(manifest)
    if motion is None:return {}
    dense=_dense_tail_playback(manifest,base_frame_count=base_frame_count)
    result={}
    for code in CODES:
        source=manifest.get('emotion_motion',{}).get(code)
        emotion=_emotion_playback(source,base_frame_count=base_frame_count) if source is not None else None
        count=(len(emotion[0]) if emotion is not None else dense[0]['frame_count']
               if dense is not None and source is None else 0)
        start=base_frame_count+6+count
        result[code]={'frames':list(range(start,start+motion['frame_count'])),'durations_ms':list(motion['durations_ms'])}
    return result


def animate_drag(frame,config):
    from .frame_cache import cached_frames
    return cached_frames('drag',frame,config,lambda:_animate_drag_uncached(frame,config))


def _animate_drag_uncached(frame,config):
    """Warp one immutable whole pose; face/body are never separate layers.

    Four local, zero-edge displacement fields move existing dot extremities.
    Every phase reads the same original pixels, preserving color and lighting.
    """
    import math
    from .windows_alpha import AlphaFrame
    _drag_motion({'drag_motion':config})
    regions=[];w,h=frame.width,frame.height
    for item in config['regions']:
        a,b,c,d=item['region'];x0,y0,x1,y1=math.ceil(a*w),math.ceil(b*h),math.floor(c*w),math.floor(d*h)
        if x1-x0<3 or y1-y0<3:raise ValueError('drag motion region too small')
        points=[]
        for y in range(y0,y1):
            for x in range(x0,x1):
                weight=math.sin(math.pi*(x-x0)/(x1-x0-1))**2*math.sin(math.pi*(y-y0)/(y1-y0-1))**2
                points.append((x,y,weight))
        regions.append((points,(x0,y0,x1,y1),item['displacement'],item['phase']))
    result=[]
    for phase in range(config['frame_count']):
        data=bytearray(frame.bgra)
        for points,(x0,y0,x1,y1),vector,offset in regions:
            wave=math.sin(2*math.pi*(phase/config['frame_count']+offset))
            dx,dy=vector[0]*w*wave,vector[1]*h*wave
            for x,y,weight in points:
                sx=max(x0,min(x1-1,x-dx*weight));sy=max(y0,min(y1-1,y-dy*weight))
                ax,ay=math.floor(sx),math.floor(sy);bx,by=min(x1-1,ax+1),min(y1-1,ay+1)
                fx,fy=sx-ax,sy-ay
                a,b,c,d=(ay*w+ax)*4,(ay*w+bx)*4,(by*w+ax)*4,(by*w+bx)*4
                target=(y*w+x)*4
                for channel in range(4):
                    data[target+channel]=round((frame.bgra[a+channel]*(1-fx)+frame.bgra[b+channel]*fx)*(1-fy)
                        +(frame.bgra[c+channel]*(1-fx)+frame.bgra[d+channel]*fx)*fy)
        sample=AlphaFrame(w,h,bytes(data))
        whole=config.get('whole_displacement_px',[0,0])
        wave=math.sin(2*math.pi*phase/config['frame_count'])
        result.append(_place_whole_frame(sample,[round(whole[0]*wave),round(whole[1]*wave)],{'cell_width':w,'cell_height':h}))
    return tuple(result)


def build_rest_frames(frames,manifest):
    """Separate quiet cosmetic idle from finite authored Action clips."""
    row=manifest.get('rest_motion')
    if row is None:return frames,{},{}
    import math
    if (set(row)!={'version','frame_count','amplitude_px','period_ms'} or row['version'] not in {'petlab-rest-motion-v1','petlab-rest-motion-v2'}
        or type(row['frame_count']) is not int or not 12<=row['frame_count']<=64
        or type(row['period_ms']) is not int or row['period_ms']<row['frame_count']*20
        or isinstance(row['amplitude_px'],bool) or not isinstance(row['amplitude_px'],(int,float))
        or not math.isfinite(row['amplitude_px']) or not 0<=row['amplitude_px']<=(2 if row['version']=='petlab-rest-motion-v1' else manifest['tail_motion']['amplitude_px'])):
        raise ValueError('invalid quiet rest motion')
    result={};clips={};mapping={}
    start=max(len(v) for v in frames.values());count=row['frame_count']
    durations=[((i+1)*row['period_ms'])//count-(i*row['period_ms'])//count for i in range(count)]
    config={**manifest['tail_motion'],'frame_count':count,'amplitude_px':row['amplitude_px']}
    for code,old in frames.items():
        original=old[0]
        samples=animate_tail(original,config)
        if code=='normal':samples=samples[:2]+(old[2],)+samples[3:]
        result[code]=old+(original,)*(start-len(old))+samples
        clips[code]={'frames':list(range(start,start+count)),'durations_ms':list(durations)}
    mapping.update({start+i:2 if i==2 else 0 for i in range(count)})
    return result,clips,mapping



def _whole_activity_frames(frames,manifest,asset_dir,declaration,tail_clip):
    """Complete-character samples only; preserve every old activity index."""
    if set(declaration)!={'version','clips'}:raise ValueError('invalid activity declaration')
    old={k:v for k,v in declaration['clips'].items() if v.get('source')!='whole_character'}
    result,clips,mapping=build_activity_frames(frames,manifest,asset_dir,{'version':'petlab-local-activities-v2','clips':old},tail_clip)
    for key,row in declaration['clips'].items():
        if row.get('source')!='whole_character':continue
        if set(row)-{'local_motion'}!={'source','asset','offsets','durations_ms'} or not isinstance(key,str) or not key:raise ValueError('invalid whole activity')
        offsets=row['offsets'];durations=row['durations_ms']
        if (not isinstance(offsets,list) or not 2<=len(offsets)<=240 or len(durations)!=len(offsets)
            or any(type(t)is not int or not 0<t<=1000 for t in durations)
            or sum(durations)>30000):raise ValueError('invalid whole activity timing')
        first=next(iter(result.values()))[0]
        source=_load(row['asset'],asset_dir,(first.width,first.height))
        if len(source)!=1:raise ValueError('whole activity needs exactly one source pose')
        motion=row.get('local_motion')
        if motion is not None:
            _drag_motion({'drag_motion':motion})
            if motion.get('whole_displacement_px',[0,0])!=[0,0] or motion['frame_count']!=len(offsets) or motion['durations_ms']!=durations:
                raise ValueError('whole activity local motion timing differs')
            poses=animate_drag(source[0],motion)
        else:poses=(source[0],)*len(offsets)
        cache={};samples=[]
        for phase,shift in enumerate(offsets):
            # The whole character translates together; face/body/hair stay intact.
            if not isinstance(shift,list) or len(shift)!=2 or any(type(v)is not int or abs(v)>32 for v in shift):raise ValueError('invalid whole activity displacement')
            shift_key=(phase if motion is not None else 0,tuple(shift))
            if shift_key not in cache:cache[shift_key]=_place_whole_frame(poses[phase],shift,row['asset']['grid'])
            samples.append(cache[shift_key])
        start=max(len(v) for v in result.values())
        for code,original in result.items():result[code]=original+(source[0],)*(start-len(original))+tuple(samples)
        indexes=list(range(start,start+len(samples)));clips[key]={'frames':indexes,'durations_ms':list(durations)}
        mapping.update({i:0 for i in indexes})
    return result,clips,mapping
