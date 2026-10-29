"""Readonly Tk sprite view implementing the one external execution contract.

Frame timers animate accepted contents; they never authorize model actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk

from product_adapters.desktop_pet_v1.execution import ExecutionOutcome,ExecutionCancelled


@dataclass
class _Playback:
    action_id: str
    contents: tuple
    done: threading.Event = field(default_factory=threading.Event)
    completed: list = field(default_factory=list)
    frames: list = field(default_factory=list)
    started_at_wall_ms: int = 0
    outcome: object = None
    error: object = None
    is_cancelled: object = None
    affects_sprite: bool = False
    presentation_codes: list = field(default_factory=list)
    speech_rendered: list = field(default_factory=list)
    thought_rendered: list = field(default_factory=list)
    thought_accepted: list = field(default_factory=list)
    action_frames: dict = field(default_factory=dict)
    gaze_results: dict = field(default_factory=dict)
    movement_results: dict = field(default_factory=dict)
    meal_results: dict = field(default_factory=dict)
    movement_visuals: dict = field(default_factory=dict)
    presentation_sources: dict = field(default_factory=dict)
    composite: object = None
    child_results: dict = field(default_factory=dict)
    late_speech: object = None
    display_waits: dict = field(default_factory=dict)
    execution_context: object = None
    visible_observer: object = None
    visible_phases: set = field(default_factory=set)


from .execution_context import ExecutionContexts


class DesktopExecutor(ExecutionContexts):
    def __init__(self,root,*,asset_dir,trace_dir,speech_presenter=None):
        self.root=root
        self.speech_presenter=speech_presenter
        self.show_thought=True
        self.speech_slot_available=lambda:True
        self.content_slot_available=lambda content:True
        self.last_displayed_content=None
        self.presentation_policy_version="petlab-ordered-presentation-v1"
        self.ui_thread=threading.get_ident()
        self.scene_snapshot=None
        self.meal_host=None
        self.meal_owner=None
        self.meal_pose=None
        self._meal_mirrors={}
        self.gaze_target=None
        self.current_gaze_direction=None
        from .gaze_renderer import GazeRenderer
        self.gaze_renderer=GazeRenderer(asset_dir)
        self.closed=False
        self._owned_timers=set()
        self.active=None
        self.requests=queue.Queue()
        self._request_lock=threading.Lock()
        self.visual_timer=None
        self.feedback_binding=None
        self.feedback_phase=None
        self._feedback_generation=0
        self.alpha_surface=None
        self.frame_index=0
        self.visual_body_frame_indices={}
        self.expression_frame_indices={}
        self.rest_motion_clips={}
        self.walk_clips={}
        self.carry_walk_clips={}
        self.walk_config=None
        self.walking_action=None
        self.walking_phase=None
        self.facing_assets=None
        self.facing_target=None
        self.facing_action=None
        self.facing_direction=0
        self.drag_motion_clips={}
        self.expression_code="normal"
        self.settled_presentation=None
        self.relation_presentations={}
        self.presentation_target_ref=None
        self.pending_presentation_target=None
        self.persistent_expression_code='normal'
        self.speech_expression_active=False
        self._visual_capabilities=None
        self.expression_frames={}
        self.emotion_motion_clips={}
        self.activity_clips={}
        self.expression_body_maps={}
        self.standing_body_map=None
        self.expression_asset_sha256=None
        self.expression_render_mode="base-frame-v1"
        self._presenting=False
        self.body_map=None;self.body_feedback_clips={}
        self.input_feedback=None;self._last_input_feedback_source=None
        self.trace_dir=Path(trace_dir).resolve()
        self.trace_dir.mkdir(parents=True,exist_ok=True)
        asset_dir=Path(asset_dir).resolve()
        self.manifest=json.loads((asset_dir/"manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("version")!="petlab-sprite-set-v1":
            raise ValueError("unsupported sprite asset manifest")
        image_path=(asset_dir/self.manifest["image"]).resolve()
        if image_path.parent!=asset_dir or hashlib.sha256(image_path.read_bytes()).hexdigest()!=self.manifest["image_sha256"]:
            raise ValueError("sprite source or fingerprint differs from manifest")
        native_alpha=sys.platform=="win32" and speech_presenter is not None
        grid=self.manifest["grid"]
        width,height=grid["cell_width"],grid["cell_height"]
        columns,rows=grid["columns"],grid["rows"]
        if any(type(n)is not int or n<=0 for n in (width,height,columns,rows)):
            raise ValueError("sprite grid dimensions must be positive integers")
        if native_alpha:
            # The native alpha surface owns all visible pixels. Tk only owns
            # the fixed input/layout footprint, never a second decoded atlas.
            self.atlas=None
            placeholder=tk.PhotoImage(master=root,width=(width+1)//2,height=(height+1)//2)
            self.frames=[placeholder]*(columns*rows)
        else:
            self.atlas=tk.PhotoImage(master=root,file=str(image_path))
            if self.atlas.width()!=width*columns or self.atlas.height()!=height*rows:
                raise ValueError("sprite grid does not match actual image")
            self.frames=[]
            for row in range(rows):
                for column in range(columns):
                    frame=tk.PhotoImage(master=root,width=width,height=height)
                    frame.tk.call(str(frame),"copy",str(self.atlas),"-from",column*width,row*height,(column+1)*width,(row+1)*height)
                    self.frames.append(frame.subsample(2,2))
        self.base_frame_count=len(self.frames)
        self.native_layout_frame=tk.PhotoImage(master=root,width=self.frames[0].width(),height=self.frames[0].height())
        self.feedback_bindings={}
        self.feedback_clips={}
        feedback_image=None
        feedback=self.manifest.get("visual_feedback")
        if feedback is not None:
            if (not isinstance(feedback,dict) or set(feedback)!={"version","image","image_sha256","grid","clips","bindings"}
                    or feedback["version"]!="petlab-visual-feedback-v1"):
                raise ValueError("invalid visual feedback manifest")
            feedback_image=(asset_dir/feedback["image"]).resolve()
            if feedback_image.parent!=asset_dir or hashlib.sha256(feedback_image.read_bytes()).hexdigest()!=feedback["image_sha256"]:
                raise ValueError("feedback sprite source or fingerprint differs")
            extra=None if native_alpha else tk.PhotoImage(master=root,file=str(feedback_image))
            layout=feedback["grid"]
            if (not isinstance(layout,dict) or set(layout)!={"columns","rows","cell_width","cell_height"}
                    or any(isinstance(n,bool) or not isinstance(n,int) or n<=0 for n in layout.values())
                    or (not native_alpha and (extra.width()!=layout["columns"]*layout["cell_width"]
                    or extra.height()!=layout["rows"]*layout["cell_height"]))):
                raise ValueError("feedback sprite grid differs from actual image")
            # The native surface resamples to the existing frame dimensions.
            # Tk color-key fallback stays within that same fixed widget footprint.
            sample=max(1,(layout["cell_width"]+self.frames[0].width()-1)//self.frames[0].width(),
                (layout["cell_height"]+self.frames[0].height()-1)//self.frames[0].height())
            if native_alpha:
                placeholder=tk.PhotoImage(master=root,width=(layout['cell_width']+sample-1)//sample,
                    height=(layout['cell_height']+sample-1)//sample)
                self.frames.extend([placeholder]*(layout['columns']*layout['rows']))
            else:
                for row in range(layout["rows"]):
                    for column in range(layout["columns"]):
                        frame=tk.PhotoImage(master=root,width=layout["cell_width"],height=layout["cell_height"])
                        frame.tk.call(str(frame),"copy",str(extra),"-from",column*layout["cell_width"],row*layout["cell_height"],
                            (column+1)*layout["cell_width"],(row+1)*layout["cell_height"])
                        self.frames.append(frame.subsample(sample,sample))
            if not isinstance(feedback["clips"],dict) or not feedback["clips"]:
                raise ValueError("visual feedback clips are missing")
            for name,clip in feedback["clips"].items():
                if (not isinstance(name,str) or not name or not isinstance(clip,dict)
                        or set(clip)!={"frames","durations_ms"} or not clip["frames"]
                        or len(clip["frames"])!=len(clip["durations_ms"])
                        or any(isinstance(n,bool) or not isinstance(n,int) or not 0<=n<len(self.frames)-self.base_frame_count for n in clip["frames"])
                        or any(isinstance(n,bool) or not isinstance(n,int) or n<=0 for n in clip["durations_ms"])):
                    raise ValueError("invalid visual feedback playback clip")
                self.feedback_clips[name]={"frames":[self.base_frame_count+n for n in clip["frames"]],"durations_ms":clip["durations_ms"]}
            if not isinstance(feedback["bindings"],dict):raise ValueError("invalid visual feedback bindings")
            for key,binding in feedback["bindings"].items():
                if (not isinstance(key,str) or not key or not isinstance(binding,dict) or set(binding)!={"start","loop","end"}
                        or any((phase!="end" or name is not None)
                        and (not isinstance(name,str) or name not in self.feedback_clips)
                        for phase,name in binding.items())):
                    raise ValueError("visual feedback binding references a missing clip")
            self.feedback_bindings=feedback["bindings"]
        self.clips=self.manifest["clips"]
        for clip in self.clips.values():
            if (not clip["frames"] or len(clip["frames"])!=len(clip["durations_ms"])
                    or any(isinstance(index,bool) or not isinstance(index,int) or not 0<=index<self.base_frame_count for index in clip["frames"])
                    or any(isinstance(ms,bool) or not isinstance(ms,int) or ms<=0 for ms in clip["durations_ms"])):
                raise ValueError("invalid sprite playback clip")
        self.bindings=self.manifest["action_assets"]
        if any(clip not in self.clips for clip in self.bindings.values()):
            raise ValueError("action asset binding references a missing clip")
        body=self.manifest.get("body_interactions")
        if body is not None:
            from .body_regions import BodyRegionMap
            if not isinstance(body,dict) or set(body)!={"version","map","map_sha256","clips"} or body["version"]!="petlab-body-feedback-v1":raise ValueError("invalid body interaction manifest")
            body_path=(asset_dir/body["map"]).resolve()
            if body_path.parent!=asset_dir or hashlib.sha256(body_path.read_bytes()).hexdigest()!=body["map_sha256"]:raise ValueError("body region source or fingerprint differs")
            self.body_map=BodyRegionMap.load(body_path,image_sha256=self.manifest["image_sha256"],frame_count=self.base_frame_count)
            if not isinstance(body["clips"],dict):raise ValueError("body feedback clips invalid")
            for name,clip in body["clips"].items():
                if (not isinstance(name,str) or not name or not isinstance(clip,dict) or set(clip)!={"frames","durations_ms"}
                        or not clip["frames"] or len(clip["frames"])!=len(clip["durations_ms"])
                        or any(isinstance(n,bool) or not isinstance(n,int) or not 0<=n<self.base_frame_count for n in clip["frames"])
                        or any(isinstance(n,bool) or not isinstance(n,int) or n<=0 for n in clip["durations_ms"])):raise ValueError("invalid body feedback clip")
            body_record=json.loads(body_path.read_text(encoding="utf-8"))
            if any(self.body_map.feedback_clip(key) is not None and self.body_map.feedback_clip(key) not in body["clips"] for key in body_record["regions"]):raise ValueError("body feedback binding missing")
            self.body_feedback_clips=body["clips"]
        self.input_feedback=self.manifest.get('input_feedback')
        if self.input_feedback is not None:
            value=self.input_feedback
            if (not isinstance(value,dict) or set(value)!={'version','clip'}
                    or value['version']!='petlab-input-feedback-v1' or value['clip'] not in self.body_feedback_clips):
                raise ValueError('invalid input feedback declaration')
        self.visual_clip=self.manifest["visual_only_clip"]
        if self.visual_clip not in self.clips:
            raise ValueError("visual-only clip is missing")
        transparent="#010101"
        root.title("TIDE 动画执行验证")
        root.configure(bg=transparent)
        root.wm_attributes("-topmost",True)
        if root.tk.call("tk","windowingsystem")=="win32" and not native_alpha:
            root.wm_attributes("-transparentcolor",transparent)
        self.bubble=tk.Label(root,text="动画素材预览",bg="#243845",fg="#f5faf7",wraplength=240,padx=12,pady=8)
        if speech_presenter is None:self.bubble.pack(padx=8,pady=4)
        self.sprite=tk.Label(root,image=self.frames[0],bg=transparent,borderwidth=0,
            width=self.frames[0].width(),height=self.frames[0].height())
        self.sprite.pack()
        if native_alpha:
            from .frame_cache import scope as visual_cache_scope
            with visual_cache_scope(self.trace_dir/'visual-cache',seed_directory=asset_dir/'visual-cache') as visual_cache_stats:
                from .windows_alpha import WindowsAlphaSurface,load_alpha_frames
                alpha_frames=load_alpha_frames(image_path,grid)
                if feedback_image is not None:
                    alpha_frames+=load_alpha_frames(feedback_image,feedback["grid"],
                        output_size=(alpha_frames[0].width,alpha_frames[0].height))
                self.original_alpha_frames=tuple(alpha_frames)
                whole_manifest=asset_dir/"whole-expressions.json"
                if whole_manifest.exists():
                    whole_bytes=whole_manifest.read_bytes()
                    whole=json.loads(whole_bytes)
                    from .whole_frame_renderer import build_whole_frames
                    self.expression_frames=build_whole_frames(alpha_frames,whole,asset_dir,self.base_frame_count)
                    from .whole_frame_renderer import emotion_motion_clips
                    self.emotion_motion_clips=emotion_motion_clips(whole,base_frame_count=self.base_frame_count)
                    activity_path=asset_dir/'activities.json'
                    activity_indices={}
                    if 'tail_motion' in whole and activity_path.exists():
                        # Reuse immutable whole-character frames: only the existing
                        # distal tail moves, with identical face, hair and lighting.
                        from .whole_frame_renderer import build_activity_frames
                        self.expression_frames,self.activity_clips,activity_indices=build_activity_frames(
                            self.expression_frames,whole,asset_dir,json.loads(activity_path.read_text(encoding='utf-8')),
                            self.emotion_motion_clips['normal'])
                    from .whole_frame_renderer import build_rest_frames
                    self.expression_frames,self.rest_motion_clips,rest_indices=build_rest_frames(self.expression_frames,whole)
                    activity_indices.update(rest_indices)
                    from .whole_frame_renderer import visual_body_frame_indices
                    contact_path=asset_dir/'contact-motion.json'
                    if contact_path.exists():
                        from .whole_frame_renderer import build_contact_frames,contact_bindings
                        contact_declaration=json.loads(contact_path.read_text(encoding="utf-8"))
                        self.contact_expression_bindings=contact_bindings(contact_declaration,self.body_feedback_clips)
                        self.contact_motion_version=contact_declaration["version"]
                        self.expression_frames,contact_clips,contact_indices,self.contact_frame_offsets=build_contact_frames(
                            self.expression_frames,contact_declaration)
                        self.body_feedback_clips.update(contact_clips);activity_indices.update(contact_indices)
                    from .walk_renderer import build_walk_frames,pose_shift
                    self.expression_frames,self.walk_clips,walk_indices,self.walk_config=build_walk_frames(self.expression_frames,whole,asset_dir)
                    activity_indices.update(walk_indices)
                    from .walk_renderer import build_carry_frames
                    self.expression_frames,self.carry_walk_clips,carry_indices=build_carry_frames(self.expression_frames,self.activity_clips,self.walk_config)
                    activity_indices.update(carry_indices)
                    if self.walk_config is not None:
                        if not hasattr(self,'contact_frame_offsets'):self.contact_frame_offsets={}
                        for direction,key in ((-1,'left'),(1,'right')):
                            self.contact_frame_offsets.update({index:pose_shift(self.walk_config,phase,direction,(alpha_frames[0].width,alpha_frames[0].height))
                                for phase,index in enumerate(self.walk_clips['normal'][key])})
                    self.expression_frame_indices={code:visual_body_frame_indices(whole,base_frame_count=self.base_frame_count,expression_code=code)
                        for code in self.expression_frames}
                    for indices in self.expression_frame_indices.values():indices.update(activity_indices)
                    for code,clip in self.rest_motion_clips.items():
                        self.expression_frame_indices[code].update({index:2 if code=='normal' and i==2 else 0 for i,index in enumerate(clip['frames'])})
                    self.visual_body_frame_indices=self.expression_frame_indices[self.expression_code]
                    from .whole_frame_renderer import drag_motion_clips
                    self.drag_motion_clips=drag_motion_clips(whole,base_frame_count=self.base_frame_count)
                    self.expression_asset_sha256=hashlib.sha256(whole_bytes).hexdigest()
                    declaration=whole.get('presentation',{})
                    if not isinstance(declaration,dict) or declaration.get('version','desktop-pet-visual-declaration-v1')!='desktop-pet-visual-declaration-v1':
                        raise ValueError('visual presentation declaration invalid')
                    selected=declaration.get('expressions',list(self.expression_frames))
                    if not isinstance(selected,list) or any(c not in self.expression_frames for c in selected):
                        raise ValueError('declared expression lacks actual loaded frames')
                    from model_packages.desktop_pet_v1.visual_capabilities import create
                    self._visual_capabilities=create(codes=selected,asset_sha256=self.expression_asset_sha256,
                        descriptions=declaration.get('descriptions'))
                    self.expression_render_mode="whole-frame-v1"
                    if "body_regions" in whole:
                        from .body_regions import BodyRegionMap
                        mapping=(asset_dir/whole["body_regions"]["map"]).resolve()
                        if mapping.parent!=asset_dir or hashlib.sha256(mapping.read_bytes()).hexdigest()!=whole["body_regions"]["map_sha256"]:
                            raise ValueError("whole-character body map source differs")
                        self.body_map=BodyRegionMap.load(mapping,image_sha256=whole["idle"]["image_sha256"],frame_count=self.base_frame_count)
                    self.standing_body_map=self.body_map
                    for code,source in whole.get('emotion_motion',{}).items():
                        registration=source.get('body_regions')
                        if registration is not None:
                            mapping=(asset_dir/registration['map']).resolve()
                            if mapping.parent!=asset_dir or hashlib.sha256(mapping.read_bytes()).hexdigest()!=registration['map_sha256']:
                                raise ValueError('emotion motion body map source differs')
                            self.expression_body_maps[code]=BodyRegionMap.load(mapping,image_sha256=source['image_sha256'],frame_count=self.base_frame_count)
                    self.body_map=self.expression_body_maps.get(self.expression_code,self.standing_body_map)
                    alpha_frames=self.expression_frames[self.expression_code]
                from .facing_renderer import FacingAssets
                self.facing_assets=FacingAssets(asset_dir,(alpha_frames[0].width,alpha_frames[0].height))
                if self.facing_assets.available:
                    self.native_layout_frame=tk.PhotoImage(master=root,width=self.facing_assets.size[0],height=self.facing_assets.size[1])
                    self.sprite.configure(image=self.native_layout_frame,width=self.facing_assets.size[0],height=self.facing_assets.size[1])
                self.alpha_surface=WindowsAlphaSurface(root,alpha_frames)
                root.bind("<Map>",self._window_changed,add="+")
                self.visual_cache_diagnostics=dict(visual_cache_stats)
        root.bind("<Configure>",self._window_changed,add="+")
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.bind("<Escape>",lambda event:self.close())
        self._schedule(0,self._pump)
        self._visual_frame(0)

    def _schedule(self,delay,callback):
        if self.closed:return None
        owned=getattr(self,'_owned_timers',None)
        if owned is None:owned=set();self._owned_timers=owned
        job=None
        def fire():
            owned.discard(job)
            if not self.closed:callback()
        job=self.root.after(delay,fire)
        owned.add(job)
        return job

    def _cancel_scheduled(self,job):
        self._owned_timers.discard(job)
        try:self.root.after_cancel(job)
        except tk.TclError:pass

    def _window_changed(self,event):
        if event.widget is not self.root:return
        # A moved layered HWND retains its pixels; remaps/resizes need upload.
        if (event.type==tk.EventType.Configure and self.alpha_surface is not None
                and self.alpha_surface.size==(event.width,event.height)):return
        self._present_alpha()

    @property
    def _sprite_active(self):
        return self.active is not None and self.active.affects_sprite

    def _present_alpha(self):
        if self.alpha_surface is None or self.closed or self._presenting:return
        self._presenting=True
        try:
            current=getattr(self,'rendering_action',None) if self._sprite_active else None
            walking=current is not None and current.semantic_key=='move_near'
            if self.expression_frames:
                activity=current is not None and current.semantic_key in self.activity_clips
                self.alpha_surface.frames=(self.original_alpha_frames if self._sprite_active and not activity and not walking
                                           else self.expression_frames[self.expression_code])
            self.current_gaze_direction=None
            transient=False;layout=None
            scene=self.scene_snapshot() if callable(self.scene_snapshot) else None
            from model_packages.desktop_pet_v1.spatial import direction,targets
            visible=() if scene is None else targets(scene)
            if self.facing_target not in visible:self.facing_target=None
            settling=getattr(self,'meal_settle',None)
            settling=settling is not None and time.monotonic()<settling['until']
            profile=(self.facing_assets is not None and self.facing_assets.available and self.facing_target is not None and self.meal_pose is None and not settling
                and (not self._sprite_active or walking) and self.feedback_binding is None and getattr(self,'phone_activity_id',None) is None)
            self.facing_direction=0
            frames=self.alpha_surface.frames;index=self.frame_index;source=frames[index]
            meal_active=current is not None and 'meal' in current.parameters
            meal_target=None
            if meal_active:meal_target=current.parameters['meal']['target_ref']
            elif settling and scene is not None:meal_target=next((r['ref'] for r in scene['participants'] if r['ref']!=self.meal_owner),None)
            elif walking and self.meal_pose is not None:meal_target=current.parameters['movement']['target_ref']
            elif not walking and self.meal_pose is not None and scene is not None:meal_target=next((r['ref'] for r in scene['participants'] if r['ref']!=self.meal_owner),None)
            if meal_target is not None and scene is not None:
                target=meal_target
                if target in visible and direction(scene,target)['horizontal']<0:
                    # Whole-pose mirroring only, not an independent face overlay.
                    from .windows_alpha import AlphaFrame
                    cache_key=id(source)
                    if cache_key not in self._meal_mirrors:
                        w,h=source.width,source.height
                        data=b''.join(b''.join(source.bgra[(y*w+x)*4:(y*w+x+1)*4] for x in range(w-1,-1,-1)) for y in range(h))
                        self._meal_mirrors[cache_key]=AlphaFrame(w,h,data)
                    source=self._meal_mirrors[cache_key]
            if profile:
                dx=direction(scene,self.facing_target)['horizontal']
                self.facing_direction=-1 if dx<0 else 1
                phase=self.walking_phase if self.walking_action is not None else None
                source=self.facing_assets.frame(self.expression_code,self.facing_direction,phase=phase,rest_phase=int(time.monotonic()*6)%24)
                layout=self.facing_assets.gaze_layout(self.expression_code,self.facing_direction,phase=phase)
            elif self.facing_assets is not None and self.facing_assets.available:
                source=self.facing_assets.front(source)
                layout=self.facing_assets.gaze_layout(self.expression_code,0,front_shift=getattr(self,'contact_frame_offsets',{}).get(self.frame_index,(0,0)),front_regions=self.gaze_renderer.value['eye_regions'])
            if not meal_active and (self.meal_pose is None or walking) and self.gaze_target is not None and self.gaze_target in visible and self._gaze_pose_supported():
                d=direction(scene,self.gaze_target);self.current_gaze_direction=d
                transformed=self.gaze_renderer.apply(source,d['horizontal'],d['vertical'],self.expression_code,layout=layout)
                transient=transformed is not source;source=transformed
            self.alpha_surface.frames=frames[:index]+(source,)+frames[index+1:]
            self.alpha_surface.present(self.frame_index,x=self.sprite.winfo_x(),y=self.sprite.winfo_y(),**({"cache":False} if transient else {}))
        finally:self._presenting=False

    def presentation_capabilities(self):
        from model_packages.desktop_pet_v1.visual_capabilities import validate
        return validate(self._visual_capabilities)

    def set_expression(self,code):
        self.persistent_expression_code=code
        self.speech_expression_active=False
        self._display_expression(code)

    def _gaze_pose_supported(self):
        current=getattr(self,'rendering_action',None)
        walking=current is not None and current.semantic_key=='move_near'
        return (self.alpha_surface is not None and (self.facing_direction!=0 or self.gaze_renderer.supports(self.expression_code))
                and self.feedback_binding is None and (not self._sprite_active or walking)
                and getattr(self,'phone_activity_id',None) is None
                and (self.facing_direction!=0 or self.visual_body_frame_indices.get(self.frame_index,self.frame_index)!=2))

    def clear_gaze(self):
        self.gaze_target=None;self.current_gaze_direction=None
        self.facing_target=None;self.facing_action=None;self.facing_direction=0
        self._present_alpha()

    def _apply_presentation(self,task,content,*,speech=False):
        from model_packages.desktop_pet_v1.face_expression import validate
        from model_packages.desktop_pet_v1 import spatial
        p=content.parameters['presentation'];code=validate(p)
        blocked_front=(p.get('version')==spatial.PRESENTATION and p['gaze']['target_ref']=='front' and self.facing_action==task.action_id)
        if p.get('version')==spatial.PRESENTATION and not blocked_front:
            self.facing_target=None if p['gaze']['target_ref']=='front' else p['gaze']['target_ref']
        self.set_speech_expression(code) if speech else self._display_expression(code)
        if p.get('version')==spatial.PRESENTATION:
            scene=self.scene_snapshot() if callable(self.scene_snapshot) else None
            actual=spatial.resolve(p,scene,renderable=not blocked_front and (self._gaze_pose_supported() or (p['gaze']['target_ref']=='front' and self.alpha_surface is not None)))
            task.gaze_results[content.content_id]=actual
            if self.facing_direction!=0 and self.facing_assets is not None:
                side='left' if self.facing_direction<0 else 'right'
                task.presentation_sources[content.content_id]={'facing_version':self.facing_assets.value['version'],
                    'facing_manifest_sha256':self.facing_assets.manifest_sha256,'image_sha256':self.facing_assets.value['profiles'][side]['image_sha256'],
                    'direction':side,'target_ref':self.facing_target,'requested_gaze':actual['target_ref'],'gaze_status':actual['status']}
            if not blocked_front:self.gaze_target=actual['target_ref'] if actual['status']=='shown' else None
            self._present_alpha()
        task.presentation_codes.append(self.expression_code)

    def apply_settled_presentation(self,value):
        if value is None:return
        from model_packages.desktop_pet_v1.settled_presentation import validate_snapshot,expression_from_snapshot
        from framework.model_api import thaw_model_data
        validate_snapshot(value)
        previous=self.settled_presentation
        if previous is not None:
            if value['session_binding']!=previous['session_binding']:raise ValueError('presentation session changed')
            if value['ordinal']<previous['ordinal']:return
            if value['ordinal']==previous['ordinal']:
                if value['fingerprint']!=previous['fingerprint']:raise ValueError('conflicting presentation at same settlement')
                return
        self.settled_presentation=thaw_model_data(value)
        from model_packages.desktop_pet_v1.relation_presentation import VERSION as RELATIONS
        if value['version']==RELATIONS:
            self.relation_presentations=thaw_model_data(value['relation_presentations'])
            pending=getattr(self,'pending_presentation_target',None)
            if value['source_kind']=='event' and value['target_ref']==pending:
                self.pending_presentation_target=None
                pending=None
            current=getattr(self,'presentation_target_ref',None)
            # A progress/preview for another relationship is not an expression
            # directed at that peer. New human contact keeps its chosen target.
            peer=(pending or (value['target_ref'] if value['source_kind']=='event'
                  else current or value['display_target_ref']))
            self.select_presentation_target(peer)
            return
        self.persistent_expression_code=expression_from_snapshot(value)
        self.formal_feedback_tokens=thaw_model_data(value.get('reaction',{}).get('feedback',{}))
        self.feedback_token_source=value.get('reaction',{}).get('fingerprint')
        if not self.speech_expression_active and self.expression_frames:
            self._display_expression(self.persistent_expression_code)

    def select_presentation_target(self,target_ref,*,pending=False):
        entries=getattr(self,'relation_presentations',{})
        if pending:self.pending_presentation_target=target_ref
        self.presentation_target_ref=target_ref
        # A single-instance/old snapshot retains its original behavior.
        if not entries:return
        reaction=entries.get(target_ref,{}).get('reaction')
        from framework.model_api import thaw_model_data
        self.formal_feedback_tokens={} if reaction is None else thaw_model_data(reaction['feedback'])
        self.feedback_token_source=None if reaction is None else reaction['fingerprint']
        self.persistent_expression_code='normal' if reaction is None else reaction['feedback']['expression']['token']
        if not self.speech_expression_active and self.expression_frames:
            self._display_expression(self.persistent_expression_code)

    def _select_execution_target(self,task):
        context=task.execution_context
        if context is None:return
        from .execution_context import validate
        validate(context)
        snap=self.settled_presentation
        if snap is not None and context['session_binding']!=snap['session_binding']:
            raise ValueError('execution belongs to a different presentation session')
        if getattr(self,'pending_presentation_target',None) not in (None,context['target_ref']):return
        self.select_presentation_target(context['target_ref'])

    def restore_settled_expression(self):
        if not self.speech_expression_active:return
        self.speech_expression_active=False
        self._display_expression(self.persistent_expression_code)

    def set_speech_expression(self,code):
        self._display_expression(code)
        self.speech_expression_active=True

    def _display_expression(self,code):
        from model_packages.desktop_pet_v1.face_expression import CODES
        if threading.get_ident()!=self.ui_thread:raise RuntimeError("face display requires Tk thread")
        if code not in CODES:raise ValueError("unknown face display code")
        if not self.expression_frames:raise ValueError("face expression assets unavailable")
        old_pose=self.visual_body_frame_indices.get(self.frame_index,self.frame_index)
        if self.frame_index>=len(self.expression_frames[code]):self.frame_index=old_pose
        self.expression_code=code
        self.visual_body_frame_indices=self.expression_frame_indices.get(code,{})
        self.body_map=self.expression_body_maps.get(code,self.standing_body_map)
        self.alpha_surface.frames=self.expression_frames[code]
        if self.walking_action is not None:
            self._present_alpha()
        elif self.feedback_binding is None and not self._sprite_active:
            # Idle expression changes select that sequence immediately. During
            # contact/drag/formal action, the current channel keeps its timer.
            self._stop_visual_timer();self._visual_frame(0)
        else:self._present_alpha()

    def _show_frame(self,index):
        self.frame_index=index
        # Native alpha owns the entire visible character; the Tk widget is only
        # the fixed hit/layout surface and must not repaint an older atlas.
        self.sprite.configure(image=self.frames[index] if self.alpha_surface is None else self.native_layout_frame)
        self._present_alpha()

    def set_phone_activity(self,event_id=None):
        # Retain the selected complete pose while its tool is running. This is
        # presentation of the same activity, not another action or state update.
        if event_id is not None and 'read_web' not in self.activity_clips:raise ValueError('phone pose unavailable')
        self.phone_activity_id=event_id
        self._stop_visual_timer();self._visual_frame(0)

    def update_meal_pose(self,value):
        previous=getattr(self,'_meal_snapshot',None)
        self._meal_snapshot=value
        if (previous is not None and value is not None and previous['interaction_id']==value['interaction_id']
                and previous['holder_ref']!=value['holder_ref'] and self.meal_owner in (previous['holder_ref'],value['holder_ref'])):
            # The transfer already committed. Show its local follow-through,
            # never a second independently selected/settled action.
            now=time.monotonic();receiving=value['holder_ref']==self.meal_owner
            frames=([self.activity_clips['offer_bowl']['frames'][-1],self.activity_clips['eat_rice']['frames'][0]] if receiving
                    else [self.activity_clips['accept_bowl']['frames'][-1]])
            self.meal_settle={'start':now,'until':now+.35,'frames':frames}
        pose=None
        if value is not None and value['portions'] and value['holder_ref']==self.meal_owner:
            pose='offer_bowl' if value['offer'] is not None else 'hold_bowl'
        if pose==self.meal_pose:return
        self.meal_pose=pose
        if not self._sprite_active:
            self._stop_visual_timer();self._visual_frame(0)

    def _visual_frame(self,index):
        if self.closed or self._sprite_active or self.feedback_binding is not None or self.walking_action is not None:return
        # Several completion/settlement paths can restart idle in one Tk turn.
        # Keep one owner of the visual clock instead of interleaving two loops.
        self._stop_visual_timer()
        clip=(getattr(self,'rest_motion_clips',{}).get(self.expression_code,
                    self.emotion_motion_clips.get(self.expression_code,self.clips[self.visual_clip]))
              if self.visual_clip==self.manifest['visual_only_clip'] else self.clips[self.visual_clip])
        if getattr(self,'phone_activity_id',None) is not None and self.visual_clip==self.manifest['visual_only_clip']:
            clip=self.activity_clips['read_web']
        elif self.meal_pose is not None and self.visual_clip==self.manifest['visual_only_clip']:
            activity=self.activity_clips['eat_rice' if self.meal_pose=='hold_bowl' else self.meal_pose]
            clip={'frames':[activity['frames'][-1 if self.meal_pose=='offer_bowl' else 0]],'durations_ms':[1000]}
        settle=getattr(self,'meal_settle',None)
        if settle is not None:
            elapsed=time.monotonic()-settle['start']
            if elapsed<.35:
                phase=min(len(settle['frames'])-1,int(elapsed/.2))
                clip={'frames':[settle['frames'][phase]],'durations_ms':[25]}
            else:self.meal_settle=None
        index%=len(clip["frames"])
        self._show_frame(clip["frames"][index])
        self.visual_timer=self._schedule(clip["durations_ms"][index],lambda:self._visual_frame(index+1))

    def _prepare_target_facing(self,task,identity,planned,target):
        if self.facing_assets is None or not self.facing_assets.available:return 0
        self._cancel_visual_feedback()
        self.facing_target=target;self.gaze_target=target;self.facing_action=task.action_id
        self._present_alpha()
        from .walk_renderer import VERSION
        task.movement_visuals[identity]={'version':VERSION,'direction':-1 if planned['end'][0]<planned['start'][0] else 1,
            'samples':0,'phase_mask':0,'facing_version':self.facing_assets.value['version'],'target_ref':target,
            'look_displayed_at_ms':time.time_ns()//1_000_000,'first_motion_at_ms':None,
            'facing_manifest_sha256':self.facing_assets.manifest_sha256,
            'facing_image_sha256':self.facing_assets.value['profiles']['left' if self.facing_direction<0 else 'right']['image_sha256']}
        task.action_frames[identity]=task.action_frames.get(identity,0)+1
        return self.facing_assets.value['look_before_move_ms']

    def _walk_pose(self,task,identity,planned,actual):
        if self.closed or self.walk_config is None or planned['distance_px']==0:return
        from .walk_renderer import phase_index,VERSION
        import math
        if self.walking_action!=task.action_id:
            self._cancel_visual_feedback();self.walking_action=task.action_id
        direction=-1 if planned['end'][0]<planned['start'][0] else 1
        phase=phase_index(math.dist(planned['start'],actual),planned['distance_px'],self.frames[0].width(),self.walk_config)
        self.walking_phase=phase
        row=task.movement_visuals.setdefault(identity,{'version':VERSION,'direction':direction,'samples':0,'phase_mask':0})
        row['samples']+=1;row['phase_mask']|=1<<phase
        clips=self.carry_walk_clips if self.meal_pose is not None and self.carry_walk_clips else self.walk_clips[self.expression_code]
        self._show_frame(clips['left' if direction<0 else 'right'][phase])

    def _end_walk(self,task):
        if self.walking_action!=task.action_id:return
        self.walking_action=None;self.walking_phase=None
        if not self.closed:self._visual_frame(0)

    def _stop_visual_timer(self):
        if self.visual_timer is not None:
            self._cancel_scheduled(self.visual_timer)
            self.visual_timer=None

    def _cancel_visual_feedback(self):
        self._stop_visual_timer()
        self._feedback_generation+=1
        self.feedback_binding=None
        self.feedback_phase=None

    def is_sprite_opaque(self,x,y):
        width,height=self.sprite.winfo_width(),self.sprite.winfo_height()
        if not 0<=x<width or not 0<=y<height:return False
        if self.alpha_surface is not None:
            frame=self.alpha_surface.frames[self.frame_index]
            return frame.bgra[(int(y)*frame.width+int(x))*4+3]>0
        frame=self.frames[self.frame_index]
        return 0<=x<frame.width() and 0<=y<frame.height() and not frame.transparency_get(int(x),int(y))

    def hit_body_region(self,x,y):
        if self.body_map is None:return None
        if getattr(self,'facing_assets',None) is not None and self.facing_assets.available and self.facing_direction!=0:
            return self.facing_assets.hit(self.expression_code,self.facing_direction,x,y,is_opaque=self.is_sprite_opaque,phase=self.walking_phase)
        dx,dy=getattr(self,'contact_frame_offsets',{}).get(self.frame_index,(0,0))
        if getattr(self,'facing_assets',None) is not None and self.facing_assets.available:
            dx+=self.facing_assets.front_offset[0];dy+=self.facing_assets.front_offset[1]
        return self.body_map.hit(frame_index=self.visual_body_frame_indices.get(self.frame_index,self.frame_index),x=x-dx,y=y-dy,width=self.frames[0].width(),height=self.frames[0].height(),is_opaque=lambda px,py:self.is_sprite_opaque(px+dx,py+dy))

    def acknowledge_input(self,value):
        """Attend using the last formal relation, without simulating a touch."""
        if threading.get_ident()!=self.ui_thread:raise RuntimeError('input feedback requires Tk thread')
        snap=self.settled_presentation
        if (self.closed or self._sprite_active or self.walking_action is not None
                or self.feedback_binding is not None or self.speech_expression_active
                or getattr(self,'phone_activity_id',None) is not None
                or snap is None or value.get('version')!='petlab-input-feedback-v1'
                or value.get('session_binding')!=snap['session_binding']
                or not value.get('source_ref') or not value.get('target_ref')
                or value['source_ref']==getattr(self,'_last_input_feedback_source',None)):
            return False
        self._last_input_feedback_source=value['source_ref']
        self.select_presentation_target(value['target_ref'])
        snapshot=getattr(self,'scene_snapshot',None)
        scene=snapshot() if callable(snapshot) else None
        if scene is not None:
            from model_packages.desktop_pet_v1.spatial import targets
            if value['target_ref'] in targets(scene):
                self.facing_target=value['target_ref'];self.gaze_target=value['target_ref']
                self._present_alpha()
        # Receiving speech is not physical contact or an authored body action.
        # Actual touching/dragging retains its immediate dedicated feedback;
        # ordinary listening leaves the quiet idle animation in control.
        return True

    def start_body_feedback(self,target):
        """A finite input acknowledgement; no Action, provider, or root access."""
        if threading.get_ident()!=self.ui_thread:raise RuntimeError("body feedback requires Tk thread")
        if self.closed or self._sprite_active or self.body_map is None or target is None:return False
        self.restore_settled_expression()
        from .body_regions import validate_body_target
        validate_body_target(target)
        # The click belongs to the image visible at contact. Restoring a speech
        # expression may already have selected a different loaded body map.
        loaded=[self.body_map,getattr(self,'standing_body_map',None),
            *getattr(self,'expression_body_maps',{}).values()]
        maps={m.map_sha256:m for m in loaded if m is not None}
        if getattr(self,'facing_assets',None) is not None:maps.update(self.facing_assets.body_maps)
        source_map=maps.get(target['map_sha256'])
        if source_map is None or target['image_sha256']!=source_map.image_sha256:raise ValueError("body feedback source differs")
        clip=source_map.feedback_clip(target["region_id"])
        if clip is None:return False
        clip=getattr(self,'contact_expression_bindings',{}).get(clip,{}).get(self.persistent_expression_code,clip)
        self._cancel_visual_feedback();self.feedback_binding="body:"+clip;self.feedback_phase="touch"
        self._body_feedback_frame(clip,0,self._feedback_generation)
        return True

    def _body_feedback_frame(self,clip_name,index,generation):
        if self.closed or self._sprite_active or generation!=self._feedback_generation:return
        clip=self.body_feedback_clips[clip_name]
        if index>=len(clip["frames"]):
            self.feedback_binding=None;self.feedback_phase=None;self._visual_frame(0);return
        self._show_frame(clip["frames"][index])
        self.visual_timer=self._schedule(clip["durations_ms"][index],lambda:self._body_feedback_frame(clip_name,index+1,generation))

    def start_visual_feedback(self,binding_key):
        """Show configured physical feedback, without an execution identity."""
        if threading.get_ident()!=self.ui_thread:raise RuntimeError("visual feedback requires the Tk thread")
        if self.closed or self._sprite_active or binding_key not in self.feedback_bindings:return False
        self.restore_settled_expression()
        if self.feedback_binding==binding_key and self.feedback_phase!="end":return False
        self._cancel_visual_feedback()
        self.feedback_binding=binding_key
        self._feedback_frame("start",0,self._feedback_generation)
        return True

    def finish_visual_feedback(self,*,cancelled=False):
        """A null end binding restores original idle immediately, without a duplicate rest pose."""
        if threading.get_ident()!=self.ui_thread:raise RuntimeError("visual feedback requires the Tk thread")
        if self.closed or self._sprite_active or self.feedback_binding is None:return False
        self._stop_visual_timer()
        self._feedback_generation+=1
        if cancelled or self.feedback_binding.startswith(("body:","input:")) or self.feedback_bindings[self.feedback_binding]["end"] is None:
            self.feedback_binding=None;self.feedback_phase=None
            self._visual_frame(0)
        else:self._feedback_frame("end",0,self._feedback_generation)
        return True

    def _feedback_clip(self,phase):
        clip=self.feedback_clips[self.feedback_bindings[self.feedback_binding][phase]]
        if self.feedback_binding=='drag' and phase=='loop':
            return self.drag_motion_clips.get(self.expression_code,clip)
        return clip

    def _feedback_frame(self,phase,index,generation):
        if (self.closed or self._sprite_active or generation!=self._feedback_generation
                or self.feedback_binding is None):return
        clip=self._feedback_clip(phase)
        if index==len(clip["frames"]):
            if phase=="end":
                self.feedback_binding=None;self.feedback_phase=None
                self._visual_frame(0)
                return
            phase="loop";index=0
            clip=self._feedback_clip(phase)
        self.feedback_phase=phase
        self._show_frame(clip["frames"][index])
        self.visual_timer=self._schedule(clip["durations_ms"][index],
            lambda:self._feedback_frame(phase,index+1,generation))

    def __call__(self,action_id,contents):
        return self.execute_guarded(action_id,contents)

    def execute_composite(self,action_id,contents,*,plan,is_cancelled=None):
        plan.validate_contents(contents)
        return self.execute_guarded(action_id,contents,is_cancelled=is_cancelled,composite=plan)

    def execute_staged(self,action_id,contents,*,plan,late_speech,is_cancelled=None):
        from model_packages.desktop_pet_v1.composite import CompositePlan
        immediate={key:row for key,row in plan.children.items() if row['kind'] not in {'speech','thought'}}
        if sum(row['kind']=='speech' for row in plan.children.values())!=1:raise ValueError('staged playback requires exactly one late speech')
        CompositePlan.create(immediate).validate_contents(contents)
        return self.execute_guarded(action_id,contents,composite=plan,late_speech=late_speech,is_cancelled=is_cancelled)

    def execute_guarded(self,action_id,contents,*,is_cancelled=None,composite=None,late_speech=None):
        if threading.get_ident()==self.ui_thread:
            raise RuntimeError("external execution must run on the serial actor worker, not the Tk thread")
        if self.closed:raise RuntimeError("desktop window is closed")
        if not isinstance(action_id,str) or not action_id:raise ValueError("execution identity is missing")
        contents=tuple(contents)
        if not contents:raise ValueError("execution contents are empty")
        for content in contents:
            if content.kind not in ({'action','speech','expression','thought'} if composite is not None else {'action','speech'}):raise ValueError("desktop content is not deliverable")
            if content.kind=="action" and content.semantic_key not in self.bindings and content.semantic_key not in self.activity_clips and content.semantic_key!='move_near':
                raise ValueError("selected action has no configured visual asset")
        if composite is not None:
            if late_speech is None:composite.validate_contents(contents)
            else:
                from model_packages.desktop_pet_v1.composite import CompositePlan
                immediate={key:row for key,row in composite.children.items() if row['kind'] not in {'speech','thought'}}
                if sum(row['kind']=='speech' for row in composite.children.values())!=1:raise ValueError('one late speech required')
                CompositePlan.create(immediate).validate_contents(contents)
                if not callable(getattr(late_speech,'done',None)) or not callable(getattr(late_speech,'result',None)):
                    raise ValueError('late speech requires a completion handle')
        task=_Playback(action_id,contents,is_cancelled=is_cancelled,composite=composite,late_speech=late_speech,execution_context=self.execution_context(action_id),visible_observer=self.execution_observer(action_id))
        with self._request_lock:
            if self.closed:raise RuntimeError("desktop window is closed")
            self.requests.put(task)
        # Closing the window resolves every pending task; no GUI timer sends a model action.
        task.done.wait()
        if task.error is not None:raise task.error
        return task.outcome

    def _pump(self):
        if self.closed:return
        if self.active is not None and self.active.late_speech is not None:
            self._poll_late_speech(self.active)
        if self.active is None:
            try:task=self.requests.get_nowait()
            except queue.Empty:pass
            else:
                if task.is_cancelled is not None and task.is_cancelled():
                    task.error=ExecutionCancelled("gesture ended before UI playback");task.done.set()
                    self._schedule(16,self._pump);return
                self.active=task
                task.affects_sprite=any(item.kind=="action" and item.semantic_key!="move_near" for item in task.contents)
                task.started_at_wall_ms=time.time_ns()//1_000_000
                try:
                    if task.affects_sprite:
                        self._cancel_visual_feedback()
                        # Formal actions use the original atlas, including when a
                        # speech prefix changes expression before the first action.
                        if self.frame_index in self.visual_body_frame_indices:
                            self._show_frame(self.visual_body_frame_indices[self.frame_index])
                    if task.composite is not None:self._start_composite(task)
                    else:self._next_content(task,0)
                except Exception as error:self._failed(task,error)
        self._schedule(16,self._pump)

    def _poll_late_speech(self,task):
        future=task.late_speech
        if task.is_cancelled is not None and task.is_cancelled():
            self._failed(task,ExecutionCancelled('source expired while awaiting speech'));return
        if not future.done():return
        task.late_speech=None
        slots={key:row for key,row in task.composite.children.items() if row['kind'] in {'speech','thought'}}
        private=any(row['kind']=='thought' for row in slots.values())
        try:
            value=future.result()
            children=tuple(value) if private else (value,)
            if any(c.content_id not in slots for c in children):raise ValueError('late content identity differs')
            task.composite.validate_contents((*task.contents,*children))
        except Exception as error:
            from product_adapters.desktop_pet_v1.quiet_realization import SpeechNotSelected
            quiet=isinstance(error,SpeechNotSelected)
            for key in slots:
                task.child_results[key]={'status':'cancelled' if quiet else 'failed','started_at_ms':None,'ended_at_ms':time.time_ns()//1_000_000,
                    'reason':('no_content_selected' if private else 'no_speech_selected') if quiet else 'technical_failure','cause_ref':None}
            self._maybe_finish_composite(task)
            return
        present={c.content_id for c in children}
        for key in set(slots)-present:
            task.child_results[key]={'status':'cancelled','started_at_ms':None,'ended_at_ms':time.time_ns()//1_000_000,
                'reason':'no_content_selected','cause_ref':None}
        index=len(task.contents)
        task.contents=(*task.contents,*children)
        try:self._start_composite(task,start=index)
        except Exception as error:self._failed(task,error)

    def _notify_visible(self,task):
        if task.visible_observer is None:return
        from model_packages.desktop_pet_v1.composite import SEQUENCE_VERSION
        sequence=task.composite is not None and task.composite.version==SEQUENCE_VERSION
        terminal=(set(task.child_results)==set(task.composite.children) and all(r['status']!='running' for r in task.child_results.values())) if sequence else False
        first_body=next((c.content_id for c in task.contents if c.kind=='action'),None)
        items=[]
        for c in task.contents:
            row=task.child_results.get(c.content_id,{})
            if c.private or c.kind not in {'action','expression','speech'} or row.get('started_at_ms') is None:continue
            phase='started' if row['status']=='running' else row['status']
            if sequence and c.kind=='action' and not terminal:
                # Progress stays local; publish one opening and one final actual
                # prefix, rather than one peer inference for each clip boundary.
                if c.content_id!=first_body or phase!='started':continue
            if phase not in {'started','completed','interrupted'}:continue
            key=(c.content_id,'started' if phase=='started' else 'ended')
            if key in task.visible_phases:continue
            items.append({'content':c.to_record(),'phase':phase,'started_at_ms':row['started_at_ms'],
                'at_ms':row['started_at_ms'] if phase=='started' else row['ended_at_ms'],
                'reason':row.get('reason'),'cause_ref':row.get('cause_ref'),
                **({'gaze_result':task.gaze_results[c.content_id]} if c.content_id in task.gaze_results else {}),
                **({'meal_result':task.meal_results[c.content_id]} if c.content_id in task.meal_results else {}),
                **({'movement_result':task.movement_results[c.content_id]} if c.content_id in task.movement_results else {})})
            task.visible_phases.add(key)
        if items:task.visible_observer({'version':'petlab-visible-effect-v3-object' if any('meal_result' in r for r in items) else 'petlab-visible-effect-v2-spatial' if any('gaze_result' in r or 'movement_result' in r for r in items) else 'petlab-visible-effect-v1','action_id':task.action_id,'items':items})

    def _maybe_finish_composite(self,task):
        if task.done.is_set() or getattr(task,'_starting_children',False) or getattr(task,'_advancing_children',False):return
        task._advancing_children=True
        try:
            for index in range(len(task.contents)):self._start_composite_child(task,index)
        finally:task._advancing_children=False
        if set(task.child_results)==set(task.composite.children) and all(
                row['status']!='running' for row in task.child_results.values()):self._finish(task)

    def _await_surface(self,task,index,callback):
        """UI backpressure on existing authorized playback, never a send grant."""
        item=task.contents[index]
        available=getattr(self,'speech_slot_available',lambda:True)
        if not available() or not getattr(self,"content_slot_available",lambda content:True)(item):
            task.display_waits.setdefault(item.content_id,{
                'started_at_wall_ms':time.time_ns()//1_000_000,'status':'waiting'})
            DesktopExecutor._schedule(self,16,callback)
            return True
        row=task.display_waits.get(item.content_id)
        if row is not None:row.update(ended_at_wall_ms=time.time_ns()//1_000_000,status='admitted')
        return False

    def _start_composite(self,task,*,start=0):
        task._starting_children=True
        # A declared but unselected verbal slot has no content and no effect.
        if start==0 and task.late_speech is None:
            present={item.content_id for item in task.contents}
            for key in set(task.composite.children)-present:
                task.child_results[key]={'status':'cancelled','started_at_ms':None,
                    'ended_at_ms':time.time_ns()//1_000_000,'reason':'no_content_selected','cause_ref':None}
        # Independent channels: a pending speech/face never pauses the body clip.
        for index in range(start,len(task.contents)):
            self._start_composite_child(task,index)
        task._starting_children=False
        DesktopExecutor._notify_visible(self,task)
        self._maybe_finish_composite(task)

    def _start_composite_child(self,task,index):
        if self.closed or task.done.is_set():return
        content=task.contents[index]
        if content.content_id in task.child_results:return
        for dep in task.composite.children[content.content_id].get('after',()):
            previous=task.child_results.get(dep)
            if previous is None or previous['status']=='running':return
            if previous['status']!='completed':
                task.child_results[content.content_id]={'status':'cancelled','started_at_ms':None,
                    'ended_at_ms':time.time_ns()//1_000_000,'reason':previous.get('reason') or 'source_expired',
                    'cause_ref':previous.get('cause_ref') or task.action_id+'#dependency:'+dep}
                return
        if task.is_cancelled is not None and task.is_cancelled():
            self._failed(task,ExecutionCancelled('source expired before child effect'));return
        hidden_thought=content.kind=='thought' and not self.show_thought
        if not hidden_thought and content.kind in {'speech','expression','thought'} and self._await_surface(task,index,
                lambda:self._start_composite_child(task,index)):return
        if content.kind!='thought':self._select_execution_target(task)
        stamp=time.time_ns()//1_000_000
        if content.kind=='action':
            task.affects_sprite=True
            self.rendering_action=content
            if content.semantic_key=='move_near':
                from .movement_playback import play
                play(self,task,index)
            else:self._play_frame(task,index,content.semantic_key if content.semantic_key in self.activity_clips else self.bindings[content.semantic_key],0)
        elif content.kind=='expression':
            from model_packages.desktop_pet_v1.face_expression import validate
            self._apply_presentation(task,content)
            self.speech_expression_active=True
            task.completed.append(content.content_id)
        else:
            self.current_speech_guard=task.is_cancelled
            if 'presentation' in content.parameters:
                from model_packages.desktop_pet_v1.face_expression import validate
                self._apply_presentation(task,content,speech=True)
            display_text='（'+content.description+'）' if content.kind=='thought' else content.description
            if content.kind=='thought':
                task.thought_accepted.append({'content_id':content.content_id,'at_unix_ms':stamp,'shown':not hidden_thought})
            if not hidden_thought:
                self.last_displayed_content=content
                self.bubble.configure(text=display_text)
                if self.speech_presenter is not None:self.speech_presenter(display_text)
                rendered=task.thought_rendered if content.kind=='thought' else task.speech_rendered
                rendered.append({'content_id':content.content_id,'at_unix_ms':stamp})
            task.completed.append(content.content_id)
        if task.done.is_set():return
        task.child_results.setdefault(content.content_id,{'status':'running' if content.kind=='action' else 'completed',
            'started_at_ms':stamp,'ended_at_ms':None if content.kind=='action' else time.time_ns()//1_000_000})
        if not getattr(task,'_starting_children',False):DesktopExecutor._notify_visible(self,task)
        self._maybe_finish_composite(task)

    def _next_content(self,task,index):
        if task.done.is_set() or self.closed:return
        if index==len(task.contents):
            self._finish(task)
            return
        content=task.contents[index]
        # Recheck at every actual effect, including speech after an animation.
        # Previous effects remain factual; an unshown suffix is never committed.
        if task.is_cancelled is not None and task.is_cancelled():
            self._failed(task,ExecutionCancelled('source expired before next visible effect'))
            return
        if content.kind=="speech":
            if DesktopExecutor._await_surface(self,task,index,lambda:self._next_content(task,index)):return
            self._select_execution_target(task)
            self.current_speech_guard=task.is_cancelled
            if "presentation" in content.parameters:
                from model_packages.desktop_pet_v1.face_expression import validate
                self._apply_presentation(task,content,speech=True)
            self.last_displayed_content=content
            self.bubble.configure(text=content.description)
            if self.speech_presenter is not None:self.speech_presenter(content.description)
            self.root.update_idletasks()
            task.speech_rendered.append({'content_id':content.content_id,'at_unix_ms':time.time_ns()//1_000_000})
            task.completed.append(content.content_id)
            self._next_content(task,index+1)
        else:
            self.rendering_action=content
            if content.semantic_key=='move_near':
                from .movement_playback import play
                play(self,task,index)
            else:self._play_frame(task,index,content.semantic_key if content.semantic_key in self.activity_clips else self.bindings[content.semantic_key],0)

    def _play_frame(self,task,content_index,clip_name,frame_index):
        if self.closed or task.done.is_set():return
        clip=self.activity_clips.get(clip_name,self.clips.get(clip_name))
        try:
            content=task.contents[content_index]
            self.rendering_action=content
            if 'meal' in content.parameters:
                if self.meal_host is None or self.meal_owner!=content.parameters['meal']['actor_ref']:raise ExecutionCancelled('meal scene/actor unavailable')
                try:self.meal_host.check(content.parameters['meal'])
                except ValueError as error:raise ExecutionCancelled('meal source expired') from error
                if content.parameters.get('meal_space') is not None:
                    from model_packages.desktop_pet_v1 import meal_space
                    try:
                        if content.parameters['meal_space']!=meal_space.VERSION:raise ValueError('unknown meal reach contract')
                        scene=self.scene_snapshot() if callable(self.scene_snapshot) else None
                        meal_space.check(scene,self.meal_owner,content.parameters['meal']['target_ref'])
                    except ValueError as error:raise ExecutionCancelled('meal target out of reach') from error
            if clip_name in self.activity_clips and task.is_cancelled is not None and task.is_cancelled():
                self.interrupt_activity(reason='source_expired',cause_ref=task.action_id+'#source-guard')
                return
            if frame_index==len(clip["frames"]):
                if 'meal' in content.parameters:
                    task.meal_results[content.content_id]=self.meal_host.finish(task.action_id+'#'+content.content_id,content.parameters['meal'],status='completed',frames=task.action_frames[content.content_id])
                task.completed.append(task.contents[content_index].content_id)
                if task.composite is not None:
                    task.child_results[task.contents[content_index].content_id].update(status='completed',ended_at_ms=time.time_ns()//1_000_000)
                    DesktopExecutor._notify_visible(self,task)
                    self._maybe_finish_composite(task)
                    if not task.done.is_set() and not any(r['status']=='running' and task.composite.children[k]['kind']=='action' for k,r in task.child_results.items()):
                        task.affects_sprite=False
                        self._visual_frame(0)
                else:self._next_content(task,content_index+1)
                return
            if frame_index==0:self._select_execution_target(task)
            index=clip["frames"][frame_index]
            self._show_frame(index)
            if task.composite is not None:
                task.child_results.setdefault(task.contents[content_index].content_id,{
                    'status':'running','started_at_ms':time.time_ns()//1_000_000,'ended_at_ms':None})
            task.frames.append(index)
            if clip_name in self.activity_clips:
                content_id=task.contents[content_index].content_id
                task.action_frames[content_id]=task.action_frames.get(content_id,0)+1
            self._schedule(clip["durations_ms"][frame_index],
                lambda:self._play_frame(task,content_index,clip_name,frame_index+1))
        except Exception as error:self._failed(task,error)

    def interrupt_activity(self,*,reason='unknown',cause_ref=None):
        """UI stops physical playback; the worker still settles the actual result."""
        task=self.active
        if task is None or not any(c.semantic_key in self.activity_clips or c.semantic_key=='move_near' for c in task.contents):return
        if reason not in {'unknown','user_contact','source_expired','technical_failure','window_closed'}:
            raise ValueError('invalid interruption reason')
        if reason in {'user_contact','source_expired'} and not cause_ref:raise ValueError('interruption needs causal reference')
        if not task.action_frames and not task.completed:
            task.error=ExecutionCancelled('activity cancelled before first frame')
            task.done.set();self.active=None
            return
        self._finish(task,interrupted=True,reason=reason,cause_ref=cause_ref)

    def _finish(self,task,*,interrupted=False,reason='unknown',cause_ref=None):
        if task.done.is_set():return
        self._end_walk(task)
        self.rendering_action=None
        if self.facing_action==task.action_id:self.facing_action=None
        for row in task.display_waits.values():
            if row['status']=='waiting':row.update(status='cancelled',ended_at_wall_ms=time.time_ns()//1_000_000)
        try:
            for c in task.contents:
                if 'meal' in c.parameters and task.action_frames.get(c.content_id) and c.content_id not in task.meal_results:
                    task.meal_results[c.content_id]=self.meal_host.finish(task.action_id+'#'+c.content_id,c.parameters['meal'],status='interrupted',frames=task.action_frames[c.content_id],reason=reason)
            if task.composite is not None:
                end=time.time_ns()//1_000_000
                for content_id in task.composite.children:
                    row=task.child_results.get(content_id)
                    if row is None:
                        task.child_results[content_id]={'status':'cancelled','started_at_ms':None,'ended_at_ms':end,'reason':reason,'cause_ref':cause_ref}
                    elif row['status']=='running':row.update(status='interrupted',ended_at_ms=end,reason=reason,cause_ref=cause_ref)
            if task.composite is not None:DesktopExecutor._notify_visible(self,task)
            action_results={key:{'status':'interrupted' if interrupted else 'completed','rendered_frames':count}
                            for key,count in task.action_frames.items()}
            for key,value in task.movement_results.items():
                if key in action_results:
                    action_results[key]['movement_result']=value
                    if key in task.movement_visuals:action_results[key]['locomotion']=task.movement_visuals[key]
            for key,value in task.meal_results.items():
                if key in action_results:action_results[key]['meal_result']=value
            if interrupted and task.composite is None:
                for row in action_results.values():row.update(reason=reason,cause_ref=cause_ref)
            if task.composite is not None:
                for key,row in action_results.items():
                    row['status']=task.child_results[key]['status']
                    if row['status']=='interrupted':row.update(reason=reason,cause_ref=cause_ref)
            # A completed content here is a finalized actual episode, not a
            # claim that an interrupted animation reached its intended end.
            for key in action_results:
                if key not in task.completed:task.completed.append(key)
            path=self.trace_dir/(hashlib.sha256(task.action_id.encode("utf-8")).hexdigest()+".json")
            trace={"version":"petlab-desktop-playback-v1","action_id":task.action_id,
                "completed_content_ids":task.completed,"frames":task.frames,"action_results":action_results,
                **({"gaze_results":task.gaze_results} if task.gaze_results else {}),
                **({"presentation_sources":task.presentation_sources} if task.presentation_sources else {}),
                **({"execution_context":task.execution_context} if task.execution_context is not None else {}),
                "speech_rendered":task.speech_rendered,**({"thought_rendered":task.thought_rendered,"thought_accepted":task.thought_accepted} if any(c.kind=="thought" for c in task.contents) else {}),"display_waits":task.display_waits,"presentation_policy":self.presentation_policy_version,"presentation_codes":task.presentation_codes,"expression_asset_sha256":self.expression_asset_sha256,"expression_render_mode":self.expression_render_mode,
                "image_sha256":self.manifest["image_sha256"],"interrupted":interrupted,
                "renderer":"win32-premultiplied-alpha-v1" if self.alpha_surface is not None else "tk-color-key-v1",
                "started_at_wall_ms":task.started_at_wall_ms,"finished_at_wall_ms":time.time_ns()//1_000_000}
            # An existing physical execution trace must never be overwritten by a retry.
            if task.composite is not None:
                trace['composite_plan_fingerprint']=task.composite.fingerprint
                trace['child_results']=task.child_results
                trace['termination']={'reason':reason if interrupted else None,'cause_ref':cause_ref}
            with path.open("x",encoding="utf-8") as output:json.dump(trace,output,ensure_ascii=False)
            details={"adapter":"tk-sprite-v1","interrupted":interrupted,"renderer":trace["renderer"]}
            if action_results:details['action_results']=action_results
            if task.gaze_results:details['gaze_results']=task.gaze_results
            if task.presentation_sources:details['presentation_sources']=task.presentation_sources
            if task.composite is not None:
                details['child_results']=task.child_results
                if task.composite.version=='desktop-pet-composite-v4-sequence':details['composite_version']=task.composite.version
            if task.is_cancelled is not None:details["occurred_at_wall_ms"]=trace["finished_at_wall_ms"]
            task.outcome=ExecutionOutcome(tuple(task.completed),str(path),details)
        except Exception as error:task.error=error
        task.done.set()
        self.active=None
        if task.composite is not None and not self.closed and not task.speech_rendered:
            # A standalone gaze is a displayed expression, not the prelude to
            # absent speech. Keep its pixels until the next formal presentation
            # or contact, without overwriting the settled mood/cache.
            standalone_gaze=(not interrupted and not any(c.kind=='action' for c in task.contents)
                and any(c.kind=='expression' and c.content_id in task.completed
                    and c.content_id in task.gaze_results for c in task.contents))
            if standalone_gaze:self.speech_expression_active=False
            else:self.restore_settled_expression()
        if not self.closed and task.affects_sprite:self._visual_frame(0)

    def _failed(self,task,error):
        self._end_walk(task)
        if task.completed or task.action_frames:
            reason='window_closed' if self.closed else 'source_expired' if isinstance(error,ExecutionCancelled) else 'technical_failure'
            self._finish(task,interrupted=True,reason=reason,cause_ref=task.action_id+'#source-guard' if reason=='source_expired' else None)
        else:
            task.error=error
            task.done.set()
            self.active=None
            if not self.closed and task.affects_sprite:self._visual_frame(0)

    def close(self):
        with self._request_lock:
            if self.closed:return
            self.closed=True
            self._cancel_visual_feedback()
            for job in tuple(self._owned_timers):self._cancel_scheduled(job)
            pending=[]
            while not self.requests.empty():pending.append(self.requests.get_nowait())
        if self.active is not None:self._failed(self.active,RuntimeError("desktop playback was interrupted"))
        for task in pending:
            task.error=ExecutionCancelled("desktop closed before any guarded effect") if task.is_cancelled is not None else RuntimeError("desktop window closed before execution")
            task.done.set()
        if self.alpha_surface is not None:self.alpha_surface.close()
        self.root.destroy()


def bind_preview_drag(root,view):
    """Physical-only preview bindings; no product, provider or event sink."""
    origin=[None]
    def cancel():
        origin[0]=None
        view.finish_visual_feedback(cancelled=True)
    def press(event):
        if not view.closed:origin[0]=(event.x_root,event.y_root,root.winfo_x(),root.winfo_y())
    def move(event):
        if view.closed or origin[0] is None:return
        x,y,left,top=origin[0]
        if (event.x_root,event.y_root)!=(x,y):view.start_visual_feedback("drag")
        left=max(0,min(root.winfo_screenwidth()-root.winfo_width(),left+event.x_root-x))
        top=max(0,min(root.winfo_screenheight()-root.winfo_height(),top+event.y_root-y))
        root.geometry(f"+{left}+{top}")
    def release(event):
        if view.closed or origin[0] is None:return
        origin[0]=None;view.finish_visual_feedback()
    view.sprite.bind("<ButtonPress-1>",press)
    view.sprite.bind("<B1-Motion>",move)
    view.sprite.bind("<ButtonRelease-1>",release)
    return cancel


def main():
    import argparse
    parser=argparse.ArgumentParser(description="Readonly desktop sprite preview; no model or memory writes.")
    parser.add_argument("--assets",type=Path,default=Path(__file__).resolve().parents[1]/"assets/pets/deepseek-maid-v1")
    parser.add_argument("--traces",type=Path,default=Path(__file__).resolve().parents[1]/"output/desktop-preview")
    parser.add_argument("--close-after-ms",type=int)
    parser.add_argument("--window-info",type=Path)
    args=parser.parse_args()
    root=tk.Tk();root.overrideredirect(True)
    view=DesktopExecutor(root,asset_dir=args.assets,trace_dir=args.traces,speech_presenter=lambda text:None)
    cancel_drag=bind_preview_drag(root,view)
    menu=tk.Menu(root,tearoff=False);menu.add_command(label="退出预览",command=view.close)
    def popup(event):
        cancel_drag()
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:
            if not view.closed:menu.grab_release()
    view.sprite.bind("<Button-3>",popup)
    if args.window_info is not None:
        def publish_window_info():
            args.window_info.parent.mkdir(parents=True,exist_ok=True)
            args.window_info.write_text(json.dumps({"window_id":root.winfo_id()}),encoding="utf-8")
        root.after(300,publish_window_info)
    if args.close_after_ms is not None:root.after(args.close_after_ms,view.close)
    root.mainloop()


if __name__=="__main__":main()
