"""Transparent desktop consumer; psychological decisions stay in the package."""
from __future__ import annotations
from collections.abc import Mapping

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import queue
import re
import threading
import sys
import tkinter as tk
import uuid

from model_packages.desktop_pet_v1.events import Speech,Interaction
from model_packages.desktop_pet_v1.expression_profile import validate_expression_profile
from framework.model_api import thaw_model_data
from .composition import PetDefinition,validate_definition
from .speech_bubble import PetSpeechBubble
from .desktop_view import DesktopExecutor
from .ingress import MouseInputAdapter,ContinuousMouseInputAdapter
from .product import wall_clock_ms
from .providers import ChatTransport
from .provider_diagnostics import format_product_error


@dataclass(frozen=True,kw_only=True)
class DesktopConfiguration:
    definition:PetDefinition
    transport:ChatTransport
    writer_temperature:float
    writer_model:str|None=None
    mouse_definitions:dict
    mouse_response_policy:str
    numbered_memory:bool=False
    memory_links:bool=False
    writer_budget_chars:int|None=None
    observe_during_hold:bool=False
    observation_bounds_ms:tuple=(2000,5000)
    expression_profile:object=None
    expression_codes:bool=False
    private_thought:bool=False
    show_thought:bool=True
    opening_mode:str="legacy"
    idle_behavior:str="speech"
    memory_recall_version:str|None=None
    web_reading:Mapping|None=None
    reading_sources:tuple=()
    reading_searches:tuple=()
    mouse_batch_quiet_ms:int|None=None

    def __post_init__(self):
        from .web_reading import validate_url
        if not isinstance(self.reading_sources,(list,tuple)) or len(self.reading_sources)>64:
            raise ValueError('reading sources must be a bounded URL list')
        sources=tuple(validate_url(url) for url in self.reading_sources)
        if len(set(sources))!=len(sources):raise ValueError('duplicate reading source')
        from .web_search import resources,decode
        searches=resources(self.reading_searches)
        object.__setattr__(self,'reading_searches',tuple(decode(url) for url in searches))
        if (sources or searches) and self.idle_behavior!='symbolic':raise ValueError('reading sources require symbolic action selection')
        object.__setattr__(self,'reading_sources',sources)
        if self.web_reading is not None:
            from urllib.parse import urlsplit
            w=self.web_reading
            if (not isinstance(w,Mapping) or not {'provider','endpoint'}<=set(w) or set(w)-{'provider','endpoint','timeout_s'}
                or w['provider']!='deepseek_fetch' or not isinstance(w['endpoint'],str)
                or urlsplit(w['endpoint']).scheme not in ('https','http') or not urlsplit(w['endpoint']).hostname
                or urlsplit(w['endpoint']).username or urlsplit(w['endpoint']).password
                or type(w.get('timeout_s',45)) not in (int,float) or not 0<w.get('timeout_s',45)<=120):raise ValueError('invalid read-only web tool configuration')
        if self.writer_model is not None and (not isinstance(self.writer_model,str) or not self.writer_model.strip()):
            raise ValueError("writer model must be an explicit nonempty name or None")
        if self.memory_recall_version not in (None,'desktop-pet-grounded-retention-recall-v2','desktop-pet-declared-form-recall-v4','desktop-pet-source-bound-reference-recall-v5','desktop-pet-owner-bound-reference-recall-v6','desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8'):raise ValueError('unknown pet recall model')
        from model_packages.desktop_pet_v1.behavior import MODES
        if self.idle_behavior not in MODES:raise ValueError('unknown idle behavior')
        if self.idle_behavior!='speech' and self.opening_mode!='shared-opening-v1':
            raise ValueError('idle behavior requires formal shared opening')
        if self.mouse_batch_quiet_ms is not None and (type(self.mouse_batch_quiet_ms) is not int or self.mouse_batch_quiet_ms<=0 or not self.observe_during_hold):raise ValueError('mouse batching requires continuous capture and a positive tail window')
        if self.opening_mode not in {'legacy','shared-opening-v1'}:raise ValueError('opening mode is invalid')
        if self.opening_mode=='shared-opening-v1' and not self.observe_during_hold:
            raise ValueError('shared opening requires continuous observation')
        if not isinstance(self.show_thought,bool):raise ValueError("show_thought must be boolean")
        if not isinstance(self.private_thought,bool):raise ValueError("private_thought must be boolean")
        if not isinstance(self.expression_codes,bool):raise ValueError("expression_codes must be boolean")
        object.__setattr__(self,"expression_profile",validate_expression_profile(self.expression_profile))
        if not isinstance(self.definition,PetDefinition) or not isinstance(self.transport,ChatTransport):
            raise TypeError("desktop configuration requires explicit role and transport")
        if (isinstance(self.writer_temperature,bool) or not isinstance(self.writer_temperature,(int,float))
                or not math.isfinite(self.writer_temperature) or not 0<=self.writer_temperature<=2):
            raise ValueError("writer temperature must be a finite configured value in [0,2]")
        if self.mouse_response_policy not in {"required","eligible"}:
            raise ValueError("mouse response policy is invalid")
        if not isinstance(self.numbered_memory,bool) or not isinstance(self.memory_links,bool) or (self.memory_links and not self.numbered_memory):
            raise ValueError("memory links require explicitly numbered memory")
        if self.writer_budget_chars is not None and (isinstance(self.writer_budget_chars,bool) or not isinstance(self.writer_budget_chars,int) or self.writer_budget_chars<=0):
            raise ValueError("writer budget must be a positive integer")
        if not isinstance(self.observe_during_hold,bool):raise ValueError("continuous observation flag is invalid")
        bounds=self.observation_bounds_ms
        if not isinstance(bounds,(list,tuple)) or len(bounds)!=2 or any(isinstance(v,bool) or not isinstance(v,int) or v<=0 for v in bounds) or bounds[0]>bounds[1]:raise ValueError("observation engineering bounds are invalid")
        object.__setattr__(self,"observation_bounds_ms",tuple(bounds))
        validate_definition(self.definition)
        checked=MouseInputAdapter(recipient_ref=self.definition.owner_ref,definitions=self.mouse_definitions,
            submit=lambda *args:None)
        object.__setattr__(self,"mouse_definitions",checked.definitions)


def load_configuration(path):
    value=json.loads(Path(path).read_text(encoding="utf-8-sig"),
        parse_constant=lambda value:(_ for _ in ()).throw(ValueError("non-finite configuration")))
    return configuration_from_record(value)


def configuration_from_record(value,*,require_api_key=True):
    """One validator for saved files and unsaved setup; neither invokes a provider."""
    if not isinstance(require_api_key,bool):raise TypeError("key requirement must be explicit")
    required={"version","definition","provider","writer_temperature","mouse"}
    if isinstance(value,dict) and value.get("version")=="desktop-pet-app-config-v2":
        required=required|{"memory_delivery"}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{"private_thought","show_thought","expression_profile","expression_codes","opening_mode","idle_behavior","memory_recall_version","writer_model","web_reading","reading_sources","reading_searches"} or value["version"] not in {"desktop-pet-app-config-v1","desktop-pet-app-config-v2"}:
        raise ValueError("desktop configuration fields or version are invalid")
    provider=value["provider"]
    allowed={"endpoint","model","api_key_env","timeout_s","json_mode","send_temperature","thinking_mode"}
    if not isinstance(provider,dict) or not {"endpoint","model"}<=set(provider) or set(provider)-allowed:
        raise ValueError("provider configuration fields are invalid")
    selected=dict(provider);key_env=selected.pop("api_key_env",None)
    key=None
    if key_env is not None:
        if not isinstance(key_env,str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",key_env):
            raise ValueError("provider key environment name is invalid")
        key=os.environ.get(key_env)
        if not key and require_api_key:
            from .local_credentials import load
            key=load(key_env)
        if not key and require_api_key:raise ValueError("configured provider key environment variable is not set")
    mouse=value["mouse"]
    if not isinstance(mouse,dict) or not {"definitions","response_policy"}<=set(mouse) or set(mouse)-{"definitions","response_policy","observation","batch_quiet_ms"}:
        raise ValueError("mouse configuration fields are invalid")
    observation=mouse.get("observation",{"enabled":False,"bounds_ms":[2000,5000]})
    if not isinstance(observation,dict) or set(observation)!={"enabled","bounds_ms"}:raise ValueError("mouse observation configuration fields are invalid")
    memory=value.get("memory_delivery",{"numbered":False,"links":False,"budget_chars":None})
    if not isinstance(memory,dict) or set(memory)!={"numbered","links","budget_chars"}:
        raise ValueError("memory delivery configuration fields are invalid")
    return DesktopConfiguration(show_thought=value.get("show_thought",True),private_thought=value.get("private_thought",False),reading_searches=value.get("reading_searches",()),reading_sources=value.get("reading_sources",()),web_reading=value.get("web_reading"),writer_model=value.get("writer_model"),memory_recall_version=value.get("memory_recall_version"),mouse_batch_quiet_ms=mouse.get("batch_quiet_ms"),opening_mode=value.get("opening_mode","legacy"),expression_codes=value.get("expression_codes",False),expression_profile=value.get("expression_profile"),observe_during_hold=observation["enabled"],observation_bounds_ms=observation["bounds_ms"],numbered_memory=memory["numbered"],memory_links=memory["links"],
        idle_behavior=value.get('idle_behavior','speech'),writer_budget_chars=memory["budget_chars"],definition=PetDefinition.from_record(value["definition"]),
        transport=ChatTransport(**selected,api_key=key),writer_temperature=value["writer_temperature"],
        mouse_definitions=mouse["definitions"],mouse_response_policy=mouse["response_policy"])


class DesktopPetWindow:
    def __init__(self,root,*,product,configuration,asset_dir,trace_dir,view=None):
        if product.application.definition.fingerprint!=configuration.definition.fingerprint:
            raise ValueError("window configuration differs from its product role")
        self.root=root;self.product=product;self.configuration=configuration
        self.diagnostics=None
        self.busy=False;self.closed=False;self.completed_jobs=0;self.last_error=None
        self.jobs=queue.Queue();self.results=queue.Queue();self._drag_origin=None;self._press_body_target=None;self._drag_moved=False
        self._recovery_drag_origin=None;self._actor_unavailable=False
        self._speech_generation=0;self.observed_jobs=0;self._pending_ui_jobs=0;self._sampling=False
        self._follow_job=None
        self._tick_alarm=None;self._tick_plan=None
        self._desired_position=None
        self._batch=None;self._batch_alarm=None;self._batch_advice=None
        # Native drag tolerance is input recognition, not a psychological threshold.
        try:
            import ctypes
            self._drag_slop=(max(1,ctypes.windll.user32.GetSystemMetrics(68)),max(1,ctypes.windll.user32.GetSystemMetrics(69)))
        except (AttributeError,OSError):self._drag_slop=(4,4)
        self.view=view if view is not None else DesktopExecutor(root,asset_dir=asset_dir,trace_dir=trace_dir,
            speech_presenter=self.show_speech)
        if self.view.root is not root:raise ValueError('preloaded view belongs to another window')
        activity_key='play_tail' if configuration.idle_behavior in {'play_tail_composite','play_tail_speech'} else configuration.idle_behavior
        if activity_key=='symbolic':
            from model_packages.desktop_pet_v1.phone_catalog import catalog
            from .web_search import resources
            urls=configuration.reading_sources+resources(configuration.reading_searches)
            candidates=catalog(urls[0] if urls else None)
            if any(c['key'] not in self.view.activity_clips for c in candidates['candidates']):
                raise ValueError('registered action entity has no animation in this character pack')
        elif activity_key!='speech' and activity_key not in self.view.activity_clips:
            raise ValueError('configured autonomous behavior has no animation in this character pack')
        if not getattr(product,'is_isolated',False):product.idle_behavior=configuration.idle_behavior
        self.view.speech_presenter=self.show_speech
        self.view.show_thought=configuration.show_thought
        if configuration.expression_codes:self.view.set_expression("normal")
        root.overrideredirect(True)
        root.title(configuration.definition.name+" · TIDE")
        # Auxiliary surfaces are withdrawn until the user opens them or actual speech arrives.
        self.dialog=tk.Toplevel(root);self.dialog.withdraw();self.dialog.overrideredirect(True)
        self.dialog.configure(bg="#243845");self.dialog.wm_attributes("-topmost",True)
        heading=tk.Frame(self.dialog,bg="#243845");heading.pack(fill="x")
        tk.Label(heading,text="与"+configuration.definition.name+"交谈",bg="#243845",fg="#f5faf7",
            font=("Microsoft YaHei UI",10)).pack(side="left",padx=10,pady=8)
        tk.Button(heading,text="×",command=self.hide_dialog,bg="#243845",fg="#f5faf7",
            borderwidth=0,width=3).pack(side="right")
        panel=tk.Frame(self.dialog,bg="#243845");panel.pack(fill="x",padx=8,pady=4)
        self.entry=tk.Entry(panel,width=26,bg="#152b38",fg="#f5faf7",insertbackground="#f5faf7",
            relief="flat",font=("Microsoft YaHei UI",10))
        self.entry.pack(side="left",fill="x",expand=True,padx=5,pady=7)
        self.send=tk.Button(panel,text="发送",command=self.send_text,bg="#a5decd",fg="#152b38",borderwidth=0)
        self.send.pack(side="right",padx=5)
        self.status=tk.Label(self.dialog,text="",bg="#243845",fg="#b9d3ce",
            font=("Microsoft YaHei UI",9),wraplength=260)
        self.status.pack(padx=10,pady=(0,6))
        self.retry=tk.Button(self.dialog,text="继续未完成的互动",command=self.resume,
            bg="#243845",fg="#d8e9e2",borderwidth=0)
        self.retry.pack(pady=(0,6))
        self.dialog.bind("<Escape>",lambda event:self.hide_dialog())
        self.dialog.protocol("WM_DELETE_WINDOW",self.hide_dialog)
        self.speech_bubble=PetSpeechBubble(root,position=self._position_surface)
        self.speech_bubble.on_hide=self._on_speech_hidden
        self.view.speech_slot_available=self.speech_bubble.accepts_speech
        if getattr(product,'is_isolated',False):
            self.view.apply_settled_presentation(product.startup_receipt.get('settled_presentation'))
        else:
            from product_adapters.desktop_pet_v1.settled_presentation import snapshot
            self.view.apply_settled_presentation(snapshot(product.application))
            product.on_settled_presentation=lambda:self.results.put(('settled_presentation',snapshot(product.application)))
        self.speech_window=self.speech_bubble.window;self.speech_label=self.speech_bubble.label
        self.speech_bubble.canvas.bind("<Button-1>",lambda event:self.hide_speech())
        self.speech_bubble.canvas.bind("<Button-3>",self.open_menu)
        self.speech_label.bind("<Button-1>",lambda event:self.hide_speech())
        self.context_menu=tk.Menu(root,tearoff=False)
        self.context_menu.add_command(label="交谈…",command=self.open_dialog)
        self.context_menu.add_command(label="阅读网页…",command=self.read_webpage)
        self.context_menu.add_command(label="恢复联网阅读",command=self.resume_reading,state="disabled")
        self._reading_failure_id=None
        if not getattr(product,'is_isolated',False):product.web_reader_binding=configuration.web_reading
        self.interaction_menu=tk.Menu(self.context_menu,tearoff=False)
        if "press_release" in configuration.mouse_definitions:
            self.interaction_menu.add_command(label="轻触互动",command=self.menu_interaction)
        self.context_menu.add_cascade(label="互动",menu=self.interaction_menu,
            state="normal" if "press_release" in configuration.mouse_definitions else "disabled")
        self.context_menu.add_command(label="继续未完成的互动",command=self.resume)
        self.context_menu.add_command(label="收起气泡",command=self.hide_speech)
        self.context_menu.add_command(label="最近发言…",command=self.speech_bubble.show_history)
        if configuration.private_thought:
            self.thought_visibility=tk.BooleanVar(root,value=configuration.show_thought)
            self.context_menu.add_checkbutton(label="显示内心独白",variable=self.thought_visibility,
                command=lambda:setattr(self.view,'show_thought',self.thought_visibility.get()))
        self.context_menu.add_command(label="诊断…",command=self.open_diagnostics)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="退出",command=self.close)
        self.observer=None
        if configuration.observe_during_hold:
            from .interaction_observer import InteractionObserver
            self.mouse=ContinuousMouseInputAdapter(recipient_ref=configuration.definition.owner_ref,definitions=configuration.mouse_definitions)
            self.observer=(product.observer if getattr(product,'is_isolated',False) else
                InteractionObserver(product=product,mouse=self.mouse,bounds_ms=configuration.observation_bounds_ms,response_policy=configuration.mouse_response_policy))
        else:
            self.mouse=MouseInputAdapter(recipient_ref=configuration.definition.owner_ref,
                definitions=configuration.mouse_definitions,submit=self._mouse_submit,capture_version="petlab-mouse-observation-v3")
        if configuration.mouse_batch_quiet_ms is not None:
            if not getattr(product,'is_isolated',False):raise ValueError('batched desktop input requires the isolated production actor')
            from .mouse_batch import MouseBatchBuffer
            self._batch_advice=product.startup_receipt['batch_sampling_advice']
            self._batch=MouseBatchBuffer(quiet_ms=configuration.mouse_batch_quiet_ms,interval_ms=self._batch_advice['interval_ms'])
        self.view.sprite.bind("<ButtonPress-1>",self._press)
        self.view.sprite.bind("<B1-Motion>",self._move)
        self.view.sprite.bind("<ButtonRelease-1>",self._release)
        self.view.sprite.bind("<Button-3>",self.open_menu)
        self.speech_label.bind("<Button-3>",self.open_menu)
        self.entry.bind("<Return>",self.send_text)
        root.bind("<Escape>",lambda event:self.close())
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.bind("<Configure>",self._root_position_changed,add="+")
        root.update_idletasks()
        root.geometry("+"+str(max(0,root.winfo_screenwidth()-root.winfo_width()-40))+"+"
            +str(max(0,root.winfo_screenheight()-root.winfo_height()-80)))
        self.worker=threading.Thread(target=self._work,name="pet-serial-actor",daemon=False)
        self.worker.start()
        self._poll_job=root.after(30,self._poll)
        if getattr(product,'recovery_pending',0):
            self._recovery_prompt_job=root.after_idle(self._needs_ingress_recovery)

    def _position_surface(self,surface,*,anchor=None):
        # Let Tk apply layout in its own loop; do not nest repaint in motion.
        if surface is self.speech_window:
            width,height=self.speech_bubble._width,self.speech_bubble._body_height+10
        else:width,height=surface.winfo_reqwidth(),surface.winfo_reqheight()
        if anchor is None:
            anchor=(self._desired_position if self._drag_origin is not None and self._desired_position is not None
                else (self.root.winfo_x(),self.root.winfo_y()))
        left=min(max(0,anchor[0]+self.root.winfo_width()//2-width//2),
            max(0,self.root.winfo_screenwidth()-width))
        top=anchor[1]-height-8
        if surface is self.speech_window:self.speech_bubble.set_tail(top<0)
        if top<0:top=anchor[1]+self.root.winfo_height()+8
        top=min(max(0,top),max(0,self.root.winfo_screenheight()-height))
        surface.geometry(f"+{left}+{top}")

    def _root_position_changed(self,event):
        if event.widget is not self.root or self.closed or self._follow_job is not None:return
        self._follow_job=self.root.after_idle(self._follow_surfaces)

    def _follow_surfaces(self):
        self._follow_job=None
        if self.closed:return
        if self.speech_bubble.mode!="hidden":self._position_surface(self.speech_window)
        if self.dialog.winfo_viewable():self._position_surface(self.dialog)

    def open_menu(self,event):
        if self.closed:return "break"
        self._cancel_drag()
        try:self.context_menu.tk_popup(event.x_root,event.y_root)
        finally:
            if not self.closed:self.context_menu.grab_release()
        return "break"

    def open_dialog(self):
        if self.closed:return
        self.hide_speech()
        self._position_surface(self.dialog)
        self.dialog.deiconify();self.dialog.lift();self.entry.focus_set()

    def hide_dialog(self):
        if not self.closed:self.dialog.withdraw()
        return "break"

    def show_speech(self,text):
        if self.closed:return
        self.hide_dialog()
        self._speech_generation+=1
        self.speech_bubble.show_speech(text)

    def _on_speech_hidden(self):
        self._speech_generation+=1
        self.view.current_speech_guard=None
        self.view.restore_settled_expression()

    def hide_speech(self):
        if not self.closed and self.speech_bubble.mode!="hidden":self.speech_bubble.hide()

    def read_webpage(self,url=None):
        if self.closed or self._needs_ingress_recovery():return
        if url is None:
            from tkinter import simpledialog
            url=simpledialog.askstring('阅读网页','粘贴要阅读的网页链接：',parent=self.root)
        if not url:return
        from .web_reading import validate_url
        try:url=validate_url(url.strip())
        except ValueError as error:
            self.status.configure(text=str(error));return
        if self._batch is not None and self._batch.parts:self._flush_batch('explicit')
        self._set_busy(True,preserve_capture=True);self.last_error=None;self.hide_dialog();self.hide_speech()
        self.status.configure(text='正在读取网页…')
        self._enqueue({'kind':'web_read','event_id':'read:'+uuid.uuid4().hex,'url':url,'coordinate':wall_clock_ms()})

    def menu_interaction(self):
        if self.closed:return
        definition=self.configuration.mouse_definitions.get("press_release")
        if definition is None:return
        # A menu selection is recorded as a menu selection, never as a sampled physical drag.
        self._submit(Interaction(content_id="menu:"+uuid.uuid4().hex,
            description="用户通过角色右键菜单选择了轻触互动",
            semantic_key=definition["semantic_key"],recipient_refs=(self.configuration.definition.owner_ref,),
            parameters={"input_kind":"context_menu","selection":"轻触互动",
                "definition":{"semantic_key":definition["semantic_key"],
                    "description":"用户通过角色右键菜单选择了轻触互动"}}),
            wall_clock_ms(),self.configuration.mouse_response_policy,queue_while_busy=True)

    def _enqueue(self,job):
        if job["kind"]=="gesture_end":job={**job,"release_response_policy":self.configuration.mouse_response_policy}
        try:
            if getattr(self.product,'is_isolated',False):self.product.send_job(job)
            else:self.jobs.put(job)
        except Exception as error:
            # No queue acceptance occurred. Do not leave a fictional pending
            # task disabling the explicit recovery control.
            self._set_busy(self._pending_ui_jobs>0 or self._sampling or getattr(self,'_multi_processing',False),preserve_capture=True)
            self._show_failure(str(error));return False
        self._pending_ui_jobs+=1
        return True

    def _show_failure(self,result):
        self.last_error=str(result)
        summary,separator,_detail=self.last_error.partition("\n详细信息：")
        self.status.configure(text=summary if separator else "处理失败，可点继续："+self.last_error)
        self.open_dialog()

    def _set_busy(self,value,*,preserve_capture=False):
        self.busy=value
        state="disabled" if value else "normal"
        # Processing state does not own human input availability. New input is
        # queued by the existing actor and invalidates unexecuted idle candidates.
        for widget in (self.entry,self.send):widget.configure(state="normal")
        self.retry.configure(state=state)

    def _needs_ingress_recovery(self):
        if getattr(self,'_actor_unavailable',False):
            self._show_failure(self.last_error or '后台进程已退出，请重新运行START.cmd恢复原存档。')
            return True
        if not getattr(self.product,'recovery_pending',0) or getattr(self.product,'recovering',False):return False
        if self.last_error:self._show_failure(self.last_error)
        else:
            self.open_dialog();self.status.configure(text='有上次未完成的互动，请点“继续未完成的互动”。')
        return True

    def _submit(self,content,coordinate,policy,*,queue_while_busy=False):
        if self.closed or (self.busy and not queue_while_busy):return
        if self._needs_ingress_recovery():return
        if self._batch is not None and self._batch.parts:self._flush_batch("explicit")
        self._set_busy(True,preserve_capture=True);self.last_error=None
        self.hide_dialog()
        self.status.configure(text="正在回应…")
        self.hide_speech()
        self.view.clear_gaze()
        self.view.select_presentation_target(self.configuration.definition.user_ref,pending=True)
        if policy=="required" and content.kind=="speech":self.speech_bubble.show_typing()
        self._enqueue({"kind":"input","event_id":"input:"+uuid.uuid4().hex,
            "contents":(content,),"coordinate":coordinate,"response_policy":policy})

    def send_text(self,event=None):
        if not self.closed:
            if self._needs_ingress_recovery():return "break"
            text=self.entry.get()
            if text.strip():
                self.entry.delete(0,"end")
                content=Speech(content_id="text:"+uuid.uuid4().hex,description=text,
                    recipient_refs=(self.configuration.definition.owner_ref,))
                self._submit(content,wall_clock_ms(),"required",queue_while_busy=True)
        return "break"

    def _mouse_submit(self,content,coordinate):
        self._submit(content,coordinate,self.configuration.mouse_response_policy,queue_while_busy=True)

    def _cancel_drag(self,*,queue_end=True):
        self._recovery_drag_origin=None
        was_dragging=self._drag_origin is not None
        if queue_end and self._batch is not None and self._batch.batch_id is not None:
            self._flush_batch('explicit',continue_capture=False)
        gesture=self.mouse.current_gesture_id if self.observer is not None else None
        self.mouse.cancel();self._drag_origin=None;self._press_body_target=None;self._drag_moved=False
        self._desired_position=None
        if was_dragging and self.observer is not None and getattr(self.product,'is_isolated',False):self.product.capture('cancel',gesture)
        if was_dragging and self.observer is not None and queue_end and self._batch is None:self._enqueue({"kind":"gesture_end","gesture_id":gesture})
        if was_dragging:
            self.speech_bubble.stop_contact();self.speech_bubble.stop_typing()
            self.view.finish_visual_feedback(cancelled=True)

    def _press(self,event):
        if self.closed or self._drag_origin is not None or getattr(self,'_recovery_drag_origin',None) is not None:return
        if self.view.body_map is not None and not self.view.is_sprite_opaque(event.x,event.y):return
        if getattr(self,'_actor_unavailable',False) or (getattr(self.product,'recovery_pending',0) and not getattr(self.product,'recovering',False)):
            # Recovery protects semantic ingress, not the physical window.
            # Moving this surface does not become a new gesture/event and
            # cannot settle, cancel, or replay the retained interaction.
            self._recovery_drag_origin=(event.x_root,event.y_root,self.root.winfo_x(),self.root.winfo_y())
            return
        # End old speech and its temporary face together at real new contact.
        visible_body_target=self.view.hit_body_region(event.x,event.y)
        self.hide_speech()
        self.view.clear_gaze()
        self.view.select_presentation_target(self.configuration.definition.user_ref,pending=True)
        self.view.restore_settled_expression()
        self.speech_bubble.stop_typing()
        self._press_body_target=visible_body_target;self._drag_moved=False
        gesture=(self.mouse.press(x=event.x_root,y=event.y_root,source_kind="tk-synthetic-test-events" if getattr(event,"send_event",False) else "tk-pointer-events",body_target=self._press_body_target,drag_slop_px=self._drag_slop)
            if self.observer is not None else self.mouse.press(x=event.x_root,y=event.y_root,coordinate=wall_clock_ms(),body_target=self._press_body_target,drag_slop_px=self._drag_slop,
                source_kind="tk-synthetic-test-events" if getattr(event,"send_event",False) else "tk-pointer-events"))
        # Legacy capture returns no gesture identity. This reference identifies
        # the actual UI callback, not a committed semantic input event.
        cause_ref=gesture if gesture is not None else 'tk-press:'+__import__('uuid').uuid4().hex
        try:
            self.view.set_phone_activity(None)
            self.view.interrupt_activity(reason='user_contact',cause_ref=cause_ref)
            self.view.start_body_feedback(self._press_body_target)
        except Exception:
            # No backend press has been delivered yet. A failed visual effect
            # must not leave a captured gesture that blocks the next contact.
            self.mouse.cancel();self._press_body_target=None;self._drag_moved=False
            self.view.finish_visual_feedback(cancelled=True)
            raise
        self._drag_origin=(event.x_root,event.y_root,self.root.winfo_x(),self.root.winfo_y())
        if self.observer is not None:
            if getattr(self.product,'is_isolated',False):
                info=self.mouse.capture_info(gesture)
                self.product.capture('press',gesture,info['start_point'],
                    source_kind="tk-synthetic-test-events" if getattr(event,"send_event",False) else "tk-pointer-events",
                    body_target=None if self._press_body_target is None else thaw_model_data(self._press_body_target),drag_slop_px=list(self._drag_slop))
            if self._batch is not None:
                self._begin_batch(gesture,info['start_point'][2]);return
            self._set_busy(True,preserve_capture=True)
            self._enqueue({"kind":"gesture_begin","gesture_id":gesture})

    def _move(self,event):
        recovery_origin=getattr(self,'_recovery_drag_origin',None)
        if recovery_origin is not None:
            x,y,*_=recovery_origin
            if abs(event.x_root-x)>self._drag_slop[0] or abs(event.y_root-y)>self._drag_slop[1]:
                self._place_drag(event,recovery_origin)
            return
        if self._drag_origin is None:return
        self.mouse.move(x=event.x_root,y=event.y_root,coordinate=None if self.observer is not None else wall_clock_ms())
        if self.observer is not None and getattr(self.product,'is_isolated',False):
            gesture=self.mouse.current_gesture_id;self.product.capture('move',gesture,self.mouse.capture_info(gesture)['last_point'])
        x,y,left,top=self._drag_origin
        if not self._drag_moved and (abs(event.x_root-x)>self._drag_slop[0] or abs(event.y_root-y)>self._drag_slop[1]):
            self._drag_moved=True;self.view.start_visual_feedback("drag")
        if not self._drag_moved:return
        self._place_drag(event,self._drag_origin)

    def _place_drag(self,event,origin):
        x,y,left,top=origin
        left=max(0,min(self.root.winfo_screenwidth()-self.root.winfo_width(),left+event.x_root-x))
        top=max(0,min(self.root.winfo_screenheight()-self.root.winfo_height(),top+event.y_root-y))
        self._desired_position=(left,top)
        self.root.geometry(f"+{left}+{top}")
        # Both windows receive the same target in this callback; normal Tk
        # layout coalesces movement without a forced repaint or stale root read.
        if self.speech_bubble.mode!="hidden":self._position_surface(self.speech_window,anchor=(left,top))
        if self.dialog.winfo_viewable():self._position_surface(self.dialog,anchor=(left,top))

    def _release(self,event):
        if getattr(self,'_recovery_drag_origin',None) is not None:
            self._recovery_drag_origin=None;self._desired_position=None;return
        if self._drag_origin is None:return
        self.speech_bubble.stop_contact();self.speech_bubble.stop_typing()
        start_x,start_y,*_=self._drag_origin
        was_dragging=self._drag_moved
        self._drag_origin=None;self._press_body_target=None;self._drag_moved=False
        self._desired_position=None
        # A short click already started its finite acknowledgement on press.
        # Let it finish; release only stops the ongoing suspended drag pose.
        if was_dragging:self.view.finish_visual_feedback(cancelled=True)
        gesture=self.mouse.release(x=event.x_root,y=event.y_root,coordinate=None if self.observer is not None else wall_clock_ms())
        speech_guard=getattr(self.view,'current_speech_guard',None)
        if speech_guard is not None and speech_guard():
            self.speech_bubble.hide()
            self.view.current_speech_guard=None
        if self.observer is not None:
            if getattr(self.product,'is_isolated',False):self.product.capture('release',gesture,self.mouse.capture_info(gesture)['last_point'])
            if self._batch is not None:
                self._freeze_batch_part(gesture,ended=True)
                self._batch.release(self.mouse.capture_info(gesture)['last_point'][2]);self._schedule_batch();return
            self._set_busy(True,preserve_capture=True)
            self._enqueue({"kind":"gesture_end","gesture_id":gesture})

    def _begin_batch(self,gesture,coordinate,*,continued=False):
        new=self._batch.batch_id is None
        if new:
            from model_packages.desktop_pet_v1.observation_policy import accelerate
            capture_origin=self.mouse.capture_info(gesture)['start_point'][2]
            policy=accelerate(base_interval_ms=self._batch_advice['interval_ms'],min_interval_ms=self.configuration.observation_bounds_ms[0],gesture_origin_ms=capture_origin,planning_coordinate=coordinate)
            self._batch.interval_ms=policy['effective_advice']['after_ms']
        batch=self._batch.begin(gesture,coordinate)
        if new:
            self._batch_source_advice=self._batch_advice
            self.product.record_batch({'operation':'begin','batch_id':batch,'gesture_id':gesture,'origin_ms':coordinate,
                'tick_at_ms':self._batch.tick_at,'advice_fingerprint':self._batch_advice['fingerprint'],
                'quiet_ms':self._batch.quiet_ms,'response_policy':self.configuration.mouse_response_policy,'continued':continued,
                'capture_origin_ms':capture_origin,'sampling_policy':policy})
        elif not continued:self.product.note_batch_activity()
        self._schedule_batch()

    def _freeze_batch_part(self,gesture,*,ended):
        if ended:observation=self.mouse.observe_finished(gesture)
        else:
            info=self.mouse.capture_info(gesture);point=info['last_point']
            self.mouse.move(x=point[0],y=point[1]);point=self.mouse.capture_info(gesture)['last_point']
            self.product.capture('move',gesture,point)
            boundary=self.mouse.freeze_boundary(gesture);observation=self.mouse.observe_boundary(boundary)
        if observation is not None:
            row=observation.content.to_record()
            self.product.record_batch({'operation':'part','batch_id':self._batch.batch_id,'content':row})
            self._batch.parts.append(row);self.mouse.accept(observation)

    def _schedule_batch(self):
        if self._batch_alarm is not None:self.root.after_cancel(self._batch_alarm)
        deadline=self._batch.deadline
        self._batch_alarm=None if deadline is None else self.root.after(max(0,deadline-wall_clock_ms()),self._batch_due)

    def _batch_due(self):
        self._batch_alarm=None
        if self.closed:return
        reason=self._batch.due(wall_clock_ms())
        if reason is None:self._schedule_batch();return
        self._flush_batch(reason)

    def _flush_batch(self,reason,*,continue_capture=True):
        from model_packages.desktop_pet_v1.mouse_batch import build_batch
        if self._batch_alarm is not None:self.root.after_cancel(self._batch_alarm);self._batch_alarm=None
        gesture=self.mouse.current_gesture_id;held=gesture is not None and self.mouse.is_active(gesture)
        if held:self._freeze_batch_part(gesture,ended=False)
        if reason!='explicit' and (self._pending_ui_jobs>0 or self._sampling or self.last_error is not None):
            self._batch.defer(reason,wall_clock_ms())
            self._schedule_batch()
            return
        if self._batch.parts:
            content=build_batch(batch_id=self._batch.batch_id,parts=self._batch.parts,recipient_ref=self.configuration.definition.owner_ref,
                reason=reason,sealed_at_ms=wall_clock_ms(),window_origin_ms=self._batch.origin,planned_tick_at_ms=self._batch.tick_at,
                advice_fingerprint=self._batch_source_advice['fingerprint'],quiet_ms=self._batch.quiet_ms)
            self._set_busy(True,preserve_capture=True)
            self._enqueue({'kind':'input','event_id':'input:'+self._batch.batch_id,'batch_id':self._batch.batch_id,'contents':(content,),
                'coordinate':content.parameters['trajectory'][-1][2],'response_policy':self.configuration.mouse_response_policy})
        at=self.mouse.capture_info(gesture)['last_point'][2] if held else wall_clock_ms();self._batch.reset()
        if held and continue_capture:self._begin_batch(gesture,at,continued=True)

    def resume_reading(self):
        if self._reading_failure_id is not None and callable(getattr(self.product,'resume_reading',None)):
            self.product.resume_reading(self._reading_failure_id)

    def resume(self):
        if self.closed or self.busy:return
        self._set_busy(True,preserve_capture=self.observer is not None);self.last_error=None
        self.hide_dialog()
        self.status.configure(text="正在恢复互动记录…")
        self._enqueue({"kind":"resume"})

    def _work(self):
        if getattr(self.product,'is_isolated',False):
            return self.product.run_window(results=self.results,executor=self.view,mouse=self.mouse,
                is_closed=lambda:self.closed,speech_generation=lambda:self._speech_generation)
        def eligible():self.results.put(("typing",self._speech_generation))
        try:
            while True:
                if self.closed:return
                timeout=None if self.observer is None else self.observer.wait_seconds()
                try:job=self.jobs.get(timeout=timeout)
                except queue.Empty:
                    self.product.semantic.gateway.last_failure=None
                    try:
                        self.results.put(("sampling",None))
                        result=self.observer.sample(executor=self.view,is_cancelled=lambda:self.closed,on_reply_ready=eligible)
                        self.product.on_settled_presentation()
                        self.results.put(("sampled",result))
                    except Exception as error:self.results.put(("error",format_product_error(error,self.product.semantic.gateway)))
                    continue
                if job is None or self.closed:return
                self.product.semantic.gateway.last_failure=None
                try:
                    if job["kind"]=="gesture_begin":
                        result=self.observer.start(job["gesture_id"]);kind="armed"
                    elif job["kind"]=="gesture_end":
                        result=self.observer.finish(job["gesture_id"],executor=self.view,is_cancelled=lambda:self.closed);kind="done"
                    elif job['kind']=='web_read':
                        result=self.product.read_page(event_id=job['event_id'],url=job['url'],executor=self.view,is_cancelled=lambda:self.closed);kind='done'
                    elif job["kind"]=="resume":
                        result=(self.observer.resume(executor=self.view,is_cancelled=lambda:self.closed,on_reply_ready=eligible)
                            if self.observer is not None else self.product.resume(executor=self.view,is_cancelled=lambda:self.closed));kind="done"
                    else:
                        from model_packages.desktop_pet_v1.mouse_batch import live_gesture_id
                        live_gesture=live_gesture_id(job['contents'])
                        result=self.product.interact(event_id=job["event_id"],contents=job["contents"],coordinate=job["coordinate"],
                            response_policy=job["response_policy"],executor=self.view,
                            is_cancelled=lambda gesture=live_gesture:self.closed or (gesture is not None and not self.mouse.is_active(gesture)));kind="done"
                    self.product.on_settled_presentation()
                    self.results.put((kind,result))
                except Exception as error:self.results.put(("job_error",format_product_error(error,self.product.semantic.gateway)))
        finally:
            if self.observer is not None:self.observer.stop()
            self.product.close()

    def _schedule_observation(self,plan):
        if self.closed:return
        if self._tick_plan is not None and self._tick_plan['fingerprint']==plan['fingerprint']:return
        if self._tick_alarm is not None:self.root.after_cancel(self._tick_alarm)
        self._tick_plan=dict(plan)
        def due():
            self._tick_alarm=None
            if self.closed or self._tick_plan['fingerprint']!=plan['fingerprint']:return
            if self._batch is not None and self._batch.batch_id is not None:
                return # The input batch owns its independently scheduled sampling deadline.
            remaining=plan['at_ms']-wall_clock_ms()
            if remaining>0:
                self._tick_alarm=self.root.after(remaining,due);return
            # An isolated actor can queue one frozen interaction boundary while
            # Writer waits. Its FIFO still owns all settlement and due proofs.
            # Idle sends and non-isolated work retain the existing queue guard.
            freeze_while_busy=(getattr(self.product,'is_isolated',False) and plan.get('kind')!='idle')
            if self.busy and not freeze_while_busy:
                self._tick_alarm=self.root.after(50,due);return
            job=self.product.make_tick_job(plan,self.mouse)
            if job is not None:
                self._set_busy(True,preserve_capture=True);self._enqueue(job)
        self._tick_alarm=self.root.after(max(0,plan['at_ms']-wall_clock_ms()),due)

    def _show_input_feedback(self,value):
        # A queued acknowledgement must not steal focus back from fresh contact.
        if (self.closed or self._drag_origin is not None or self._pending_ui_jobs>0
                or value.get('input_generation')!=getattr(self.product,'_input_generation',None)
                or value.get('activity_generation')!=getattr(self.product,'_activity_generation',None)):
            return False
        acknowledged=self.view.acknowledge_input(value)
        if acknowledged:self.speech_bubble.show_typing()
        return acknowledged

    def _poll(self):
        self._poll_job=None
        if self.closed:return
        guard=getattr(self.view,"current_speech_guard",None)
        if guard is not None and guard():self.hide_speech()
        try:kind,result=self.results.get_nowait()
        except queue.Empty:pass
        else:
            if kind=='actor_exited':
                self._actor_unavailable=True
                self._pending_ui_jobs=0;self._sampling=False;self._multi_processing=False
                # Clear only frontend pointer capture. The dead process's
                # durable captures/effects remain untouched for host recovery.
                self.mouse.cancel();self._drag_origin=None;self._press_body_target=None;self._drag_moved=False
                self.view.finish_visual_feedback(cancelled=True)
                self._set_busy(False,preserve_capture=True);self.speech_bubble.stop_typing()
                self._show_failure(result)
            elif kind=="multi_processing":
                self._multi_processing=bool(result)
                self._set_busy(self._pending_ui_jobs>0 or self._sampling or self._multi_processing,preserve_capture=True)
                if not result:self.speech_bubble.stop_typing()
            elif kind=="capture":
                self.product.complete_capture(result,self.mouse)
            elif kind=='observation_plan':
                self._schedule_observation(result)
            elif kind=='batch_advice':
                self._batch_advice=result
                if self._batch is not None:self._batch.interval_ms=result['interval_ms']
            elif kind=='phone_status':
                from .web_result import paused
                self._reading_failure_id=result['event_id'] if paused(result) else None
                self.context_menu.entryconfigure('恢复联网阅读',state='normal' if self._reading_failure_id is not None else 'disabled')
                active=result['status'] in {'fetching','delivering'}
                if active:
                    if (self._drag_origin is None and result.get('input_generation')==getattr(self.product,'_input_generation',None)
                            and result.get('activity_generation')==getattr(self.product,'_activity_generation',0)):
                        self.view.set_phone_activity(result['event_id'])
                elif getattr(self.view,'phone_activity_id',None)==result['event_id']:self.view.set_phone_activity(None)
            elif kind=='input_feedback':
                self._show_input_feedback(result)
            elif kind=='settled_presentation':self.view.apply_settled_presentation(result)
            elif kind=="sampling":
                self._sampling=True;self._set_busy(True,preserve_capture=True)
            elif kind=="typing":
                if isinstance(result,dict):
                    current=(result.get('input_generation')==getattr(self.product,'_input_generation',None)
                        and result.get('speech_generation')==self._speech_generation)
                else:current=result==self._speech_generation
                if current:self.speech_bubble.show_typing()
            else:
                if kind in {"armed","done","tick_done","job_error"}:self._pending_ui_jobs=max(0,self._pending_ui_jobs-1)
                if kind in {"sampled","error"}:self._sampling=False
                self._set_busy(self._pending_ui_jobs>0 or self._sampling or getattr(self,'_multi_processing',False),preserve_capture=True);self.speech_bubble.stop_typing()
                if kind=="armed":pass
                elif kind=='tick_done':self.observed_jobs+=bool(result and result.get('observed'))
                elif kind=="sampled":self.observed_jobs+=1
                elif kind=="done":
                    self.completed_jobs+=1
                    self.status.configure(text="已保存本次互动" if result is not None else "没有待继续的发送")
                else:
                    self._show_failure(result)
        if (self._batch is not None and not self.closed and not self._pending_ui_jobs
                and not self._sampling and self.last_error is None):
            # Completion only releases backpressure on an already-due batch.
            # It cannot create a Tick or grant response eligibility.
            reason=self._batch.ready_reason(wall_clock_ms())
            if reason is not None:self._flush_batch(reason)
        if not self.closed:self._poll_job=self.root.after(30,self._poll)

    def open_diagnostics(self):
        if self.closed:return
        if self.diagnostics is not None and not self.diagnostics.closed:
            self.diagnostics.window.deiconify();self.diagnostics.window.lift();return
        from .diagnostics_view import DiagnosticsWindow
        trace_dir=getattr(self.product,'trace_dir',None)
        if trace_dir is None:trace_dir=self.product.semantic.gateway.trace_dir
        self.diagnostics=DiagnosticsWindow(self.root,trace_dir=trace_dir,playback_dir=self.view.trace_dir,
            capabilities=self.view.presentation_capabilities(),secrets=(self.configuration.transport.api_key,))

    def close(self):
        if self.closed:return
        for name in ("_poll_job","_recovery_prompt_job"):
            job=getattr(self,name,None)
            if job is not None:
                self.root.after_cancel(job);setattr(self,name,None)
        if self._batch_alarm is not None:
            self.root.after_cancel(self._batch_alarm);self._batch_alarm=None
        if self._tick_alarm is not None:
            self.root.after_cancel(self._tick_alarm);self._tick_alarm=None
        if self._follow_job is not None:
            self.root.after_cancel(self._follow_job);self._follow_job=None
        self._cancel_drag(queue_end=False)
        self.closed=True
        if self.diagnostics is not None:self.diagnostics.close()
        self.jobs.put(None)
        self.speech_bubble.close()
        self.view.close()


def _remembered_launch_paths(path):
    from .setup_dialog import read_launch_paths
    try:return read_launch_paths(path) or {}
    except (ValueError,OSError):
        print("启动路径记录无法读取，请在设置中选择原配置和记忆文件。",flush=True)
        return {}


def _dual_launch_requested(args,remembered,*,explicit_session=False):
    if args.dual or args.workspace is not None:return True
    if args.single or explicit_session:return False
    # Old launcher records name one exact saved session. Never silently open a
    # different pair instead of the pet the user previously configured.
    return 'multi_workspace' in remembered if remembered else True


def _run_dual(root,*,configuration,workspace_root,asset_dir):
    from .multi_desktop import MultiDesktop
    if root is None:root=tk.Tk();root.withdraw()
    host=None
    try:
        host=MultiDesktop(root,configuration=configuration,workspace_root=workspace_root,asset_dir=asset_dir)
        print('双桌宠已启动。右键可对当前角色公开交谈、同时对两只说或私聊。',flush=True)
        root.mainloop()
    finally:
        if host is not None:host.close();host.join()
        else:
            try:root.destroy()
            except tk.TclError:pass


def main():
    import argparse
    from .product import PetProduct
    from .providers import ProviderGateway
    parser=argparse.ArgumentParser(description="Start the default whale pair, or restore an existing TIDE pet.")
    parser.add_argument("--config",type=Path)
    parser.add_argument("--session",type=Path)
    base=Path(__file__).resolve().parents[1]
    parser.add_argument("--assets",type=Path,help="Figure directory; overrides the remembered selection.")
    parser.add_argument("--traces",type=Path)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--dual',action='store_true',help='Start the whale pair using shared API settings and separate memories.')
    mode.add_argument('--single',action='store_true',help='Use the original single-pet session entry.')
    parser.add_argument('--workspace',type=Path,help='The pair workspace; existing identities are restored unchanged.')
    parser.add_argument("--setup",action="store_true",help="Open the explicit pre-launch configuration window.")
    parser.add_argument("--resume",action="store_true",help="Restore the last configured pet, or open setup when unavailable.")
    parser.add_argument("--validate",action="store_true",help="Validate configuration without creating a session or calling a provider.")
    args=parser.parse_args()
    if args.single and args.workspace is not None:parser.error('--workspace is for the dual-pet entry')
    explicit_session=args.session is not None
    if args.validate:
        if args.config is None or args.setup:parser.error("--validate requires --config and cannot be combined with --setup")
        from .setup_dialog import read_configuration_record
        from .asset_inspection import inspect_asset_sources
        selected=configuration_from_record(read_configuration_record(args.config),require_api_key=False)
        report=inspect_asset_sources(args.assets or base/"assets/pets/deepseek-maid-v1")
        if selected.expression_codes:
            from model_packages.desktop_pet_v1.face_expression import CODES
            if set(report['expressions'])!=set(CODES):raise ValueError('素材包未提供完整的五种表情')
        print("Configuration valid: "+selected.definition.name+"; Assets sources checked: "+str(len(report['sources']))+"; provider not contacted.");return
    preferences=base/"output/user-settings/startup.json"
    root=None;remembered=(_remembered_launch_paths(preferences) if args.resume or args.setup or args.config is None else {})
    if args.resume and not args.setup and args.config is None:
        from .setup_dialog import read_launch_paths
        try:
            if remembered:
                load_configuration(remembered['config_path'])
                args.config=remembered['config_path']
                args.session=args.session or remembered['session_path']
                args.traces=args.traces or remembered['trace_dir']
                args.assets=args.assets or remembered.get('asset_dir')
        except (ValueError,OSError):
            # Setup remains the recovery path; never print key/config contents.
            print("上次启动配置暂不可用，请在设置中确认。",flush=True)
    use_dual=_dual_launch_requested(args,remembered,explicit_session=explicit_session)
    if use_dual:args.workspace=args.workspace or remembered.get('multi_workspace',base/'output/multi-pet')
    if args.setup or args.config is None:
        from .setup_dialog import read_launch_paths,select_launch
        root,choice=select_launch(config_path=args.config or remembered.get("config_path",base/"output/user-settings/config.json"),
            session_path=args.session or remembered.get("session_path",base/"output/single-pet/session.json"),
            trace_dir=args.traces or remembered.get("trace_dir",base/"output/single-pet/traces"),preferences_path=preferences,
            asset_dir=args.assets or remembered.get('asset_dir',base/"assets/pets/deepseek-maid-v1"),
            **({'multi_workspace':args.workspace} if use_dual else {}))
        if choice is None:return
        selected=choice.configuration;args.session=choice.session_path;args.traces=choice.trace_dir
        args.assets=choice.asset_dir or args.assets
        use_dual=choice.multi_workspace is not None
        args.workspace=choice.multi_workspace
    else:
        selected=load_configuration(args.config)
        args.session=args.session or base/"output/single-pet/session.json"
        args.traces=args.traces or base/"output/desktop-product"
    args.assets=args.assets or base/"assets/pets/deepseek-maid-v1"
    if use_dual:
        return _run_dual(root,configuration=selected,workspace_root=args.workspace,asset_dir=args.assets)
    product=None;window=None;preloaded=None;owner_lease=None
    try:
        from .launch_lease import acquire
        try:owner_lease=acquire(args.session)
        except RuntimeError as error:
            if str(error)!="session file is already in use":raise
            print("这个角色已在运行，或上一后台仍在完成退出；本次不会重复启动。",flush=True)
            return
        if root is None:root=tk.Tk();root.withdraw()
        print("正在加载角色并恢复本地记忆，请稍候；历史较多时启动会更久。",flush=True)
        from .asset_inspection import preflight_visual_capabilities
        declared=preflight_visual_capabilities(args.assets,native=sys.platform=='win32') if selected.expression_codes else None
        from .process_actor import ProcessDesktopActor
        product=ProcessDesktopActor(configuration=selected,session_path=args.session,trace_dir=args.traces/"providers",
            checkpoint_storage_mode="delta-v1",visual_capabilities=declared,defer_startup=True)
        # Tk and native HWND operations stay on this main thread. The restored
        # backend remains behind its activation barrier until validation ends.
        preloaded=DesktopExecutor(root,asset_dir=args.assets,trace_dir=args.traces/"playback",speech_presenter=lambda text:None)
        if selected.expression_codes:
            preloaded.set_expression('normal')
            if preloaded.presentation_capabilities()!=declared:
                raise ValueError('loaded visual capabilities differ from the startup declaration')
        product.wait_until_ready()
        window=DesktopPetWindow(root,product=product,configuration=selected,asset_dir=args.assets,
            trace_dir=args.traces/"playback",view=preloaded)
        product.activate()
        print("桌宠已启动。右键角色可交谈、查看设置与恢复互动。",flush=True)
        root.deiconify();root.mainloop()
    finally:
        try:
            if window is not None:
                window.close();window.worker.join()
            else:
                if product is not None:product.close()
                if preloaded is not None:preloaded.close()
                elif root is not None:root.destroy()
        finally:
            if owner_lease is not None:owner_lease.close()


if __name__=="__main__":main()
