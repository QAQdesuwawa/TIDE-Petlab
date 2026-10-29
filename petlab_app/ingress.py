"""Capture completed physical facts; the package owns their semantic measure."""
from __future__ import annotations

from collections.abc import Mapping
import uuid

from framework.model_api import freeze_model_payload,thaw_model_data
from model_packages.desktop_pet_v1.events import Interaction
from .body_regions import validate_body_target


class MouseInputAdapter:
    """One left-button gesture on a pet surface, without model or History access.

    Tk integration passes screen coordinates and actual wall-clock milliseconds.
    A callback receives one completed Content and its actual release coordinate;
    it uses the same application.receive seam as every other received input.
    """
    def __init__(self,*,recipient_ref,definitions,submit,capture_version="petlab-mouse-gesture-v1"):
        if not isinstance(recipient_ref,str) or not recipient_ref.strip():
            raise ValueError("mouse input recipient is missing")
        if not isinstance(definitions,Mapping) or set(definitions)-{"drag","press_release"}:
            raise ValueError("mouse gesture definitions are invalid")
        for item in definitions.values():
            if (not isinstance(item,Mapping) or set(item)!={"semantic_key","description"}
                    or any(not isinstance(item[key],str) or not item[key].strip() for key in item)):
                raise ValueError("mouse gesture definition must provide its key and description")
        if not callable(submit):raise TypeError("mouse input submit callback is required")
        if capture_version not in {"petlab-mouse-gesture-v1","petlab-mouse-observation-v3"}:raise ValueError("unsupported completed capture version")
        self.capture_version=capture_version
        self.recipient_ref=recipient_ref
        self.definitions=freeze_model_payload(definitions)
        self.submit=submit
        self._trajectory=None
        self._body_target=None

    @staticmethod
    def _point(x,y,coordinate):
        if any(isinstance(value,bool) or not isinstance(value,int) for value in (x,y,coordinate)):
            raise ValueError("mouse coordinates and wall time must be integers")
        return (x,y,coordinate)

    def press(self,*,x,y,coordinate,body_target=None,source_kind="tk-pointer-events",drag_slop_px=(0,0)):
        point=self._point(x,y,coordinate)
        if self._trajectory is not None:raise ValueError("mouse gesture is already active")
        if not isinstance(source_kind,str) or not source_kind:raise ValueError("mouse capture source missing")
        if not isinstance(drag_slop_px,(list,tuple)) or len(drag_slop_px)!=2 or any(type(v) is not int or v<0 for v in drag_slop_px):raise ValueError("mouse drag slop invalid")
        self._drag_slop_px=tuple(drag_slop_px)
        self._source_kind=source_kind
        self._body_target=None if body_target is None else freeze_model_payload(validate_body_target(body_target))
        self._trajectory=[point]

    def _append(self,*,x,y,coordinate):
        point=self._point(x,y,coordinate)
        if self._trajectory is None:return False
        if coordinate<self._trajectory[-1][2]:raise ValueError("mouse capture time moved backwards")
        self._trajectory.append(point)
        return True

    def move(self,*,x,y,coordinate):
        self._append(x=x,y=y,coordinate=coordinate)

    def cancel(self):
        self._trajectory=None;self._body_target=None

    def release(self,*,x,y,coordinate):
        if not self._append(x=x,y=y,coordinate=coordinate):return None
        trajectory=tuple(self._trajectory)
        body_target=self._body_target;self._body_target=None
        self._trajectory=None
        first,last=trajectory[0],trajectory[-1]
        moved=(any(abs(point[0]-first[0])>self._drag_slop_px[0] or abs(point[1]-first[1])>self._drag_slop_px[1] for point in trajectory)
            if self.capture_version=="petlab-mouse-observation-v3" else any(point[:2]!=first[:2] for point in trajectory))
        gesture="drag" if moved else "press_release"
        definition=self.definitions.get(gesture)
        if definition is None:return None
        duration=last[2]-first[2]
        description=(definition["description"]+f"；左键，屏幕坐标从({first[0]}, {first[1]})"
            +f"到({last[0]}, {last[1]})，持续{duration}毫秒。")
        if body_target is not None:description+="起始接触部位："+body_target["label"]+"。"
        parameters={"capture_version":self.capture_version,"gesture":gesture,"button":"left",
            "coordinate_system":"screen_pixels","trajectory":trajectory,"duration_ms":duration,
            "gesture_definition":thaw_model_data(definition),**({"body_target":thaw_model_data(body_target)} if body_target is not None else {})}
        if self.capture_version=="petlab-mouse-observation-v3":
            from model_packages.desktop_pet_v1.trajectory_features import summarize_features
            parameters.update(capture_mode="release-only",source_kind=self._source_kind,gesture_id="gesture:"+uuid.uuid4().hex,sequence=1,
                phase="ended",interval_duration_ms=duration,total_duration_ms=duration,drag_slop_px=list(self._drag_slop_px),
                geometry_summary=summarize_features(trajectory),
                measurement_scope="only this completed interval; actual press through release")
            description=f"用户已松开角色，交互结束；本次观测持续{duration}毫秒。"
            if body_target is not None:description+="起始接触部位："+body_target["label"]+"。"
        content=Interaction(content_id="mouse:"+uuid.uuid4().hex,description=description,
            recipient_refs=(self.recipient_ref,),semantic_key=definition["semantic_key"],parameters=parameters)
        return self.submit(content,coordinate)


# Continuous observation is opt-in; the original release-only adapter stays intact.
from dataclasses import dataclass
import threading
import time
from model_packages.desktop_pet_v1.trajectory_features import summarize_features

@dataclass(frozen=True)
class MouseObservation:
    gesture_id:str
    sequence:int
    end_index:int
    start_point:tuple
    end_point:tuple
    content:Interaction

    def temporal_coordinates(self, *, query_started_ms, time_domain):
        """Bind an actual capture interval; caller supplies actual query evidence.

        This does not accept the interval or change the capture cursor.
        """
        from model_packages.desktop_pet_v1.three_time import ObservationTime
        from framework.model_api import model_data_fingerprint
        params=self.content.parameters
        if (params['gesture_id']!=self.gesture_id or params['sequence']!=self.sequence
                or tuple(params['trajectory'][0])!=self.start_point
                or tuple(params['trajectory'][-1])!=self.end_point
                or params['interval_duration_ms']!=self.end_point[2]-self.start_point[2]):
            raise ValueError('observation coordinates differ from captured source')
        origin=self.end_point[2]-params['total_duration_ms']
        return ObservationTime(interaction_id=self.gesture_id,
            observation_id=self.content.content_id,sequence=self.sequence,time_domain=time_domain,
            origin_ms=origin,world_start_ms=self.start_point[2],world_end_ms=self.end_point[2],
            local_start_ms=self.start_point[2]-origin,local_end_ms=self.end_point[2]-origin,
            query_started_ms=query_started_ms,source_ref=model_data_fingerprint(self.content.to_record()))

class ContinuousMouseInputAdapter(MouseInputAdapter):
    def __init__(self,*,recipient_ref,definitions):
        super().__init__(recipient_ref=recipient_ref,definitions=definitions,submit=lambda *args:None)
        self._capture_lock=threading.RLock();self._captures={};self._current_id=None
        self._observation_lock=threading.RLock()
        self._boundaries={}

    @staticmethod
    def _now(coordinate):return time.time_ns()//1_000_000 if coordinate is None else coordinate

    def press(self,*,x,y,coordinate=None,source_kind="tk-pointer-events",body_target=None,gesture_id=None,drag_slop_px=(0,0)):
        with self._capture_lock:
            if self._current_id is not None and self.is_active(self._current_id):raise ValueError("mouse gesture already active")
            if source_kind not in {"tk-pointer-events","tk-synthetic-test-events"}:raise ValueError("mouse capture source kind is invalid")
            if not isinstance(drag_slop_px,(tuple,list)) or len(drag_slop_px)!=2 or any(type(v) is not int or v<0 for v in drag_slop_px):raise ValueError('drag slop invalid')
            target=None if body_target is None else freeze_model_payload(validate_body_target(body_target))
            if gesture_id is not None and (not isinstance(gesture_id,str) or not gesture_id.startswith('gesture:') or gesture_id in self._captures):raise ValueError('mouse gesture identity is invalid or duplicated')
            point=self._point(x,y,self._now(coordinate));gesture=gesture_id or "gesture:"+uuid.uuid4().hex
            self._captures[gesture]={"points":[point],"phase":"ongoing","cursor":0,"sequence":0,"pending":None,"source_kind":source_kind,"body_target":target,"drag_slop_px":tuple(drag_slop_px)}
            self._current_id=gesture
            return gesture

    def _capture_point(self,x,y,coordinate):
        state=self._captures.get(self._current_id)
        if state is None or state["phase"]!="ongoing":return None
        point=self._point(x,y,self._now(coordinate))
        if point[2]<state["points"][-1][2]:raise ValueError("mouse capture time moved backwards")
        state["points"].append(point);return state

    def move(self,*,x,y,coordinate=None):
        with self._capture_lock:self._capture_point(x,y,coordinate)

    def release(self,*,x,y,coordinate=None):
        with self._capture_lock:
            state=self._capture_point(x,y,coordinate)
            if state is not None:state["phase"]="ended"
            return self._current_id if state is not None else None

    def cancel(self):
        with self._capture_lock:
            state=self._captures.get(self._current_id)
            if state is not None and state["phase"]=="ongoing":state["phase"]="cancelled"

    @property
    def current_gesture_id(self):return self._current_id

    def is_active(self,gesture_id):
        with self._capture_lock:return self._captures.get(gesture_id,{}).get("phase")=="ongoing"

    def capture_info(self,gesture_id):
        with self._capture_lock:
            state=self._captures.get(gesture_id)
            if state is None:return None
            return {"gesture_id":gesture_id,"phase":state["phase"],"start_point":state["points"][0],"last_point":state["points"][-1]}

    def _snapshot(self,gesture_id,state,phase):
        if state["pending"] is not None:return state["pending"]
        start,end=state["cursor"],len(state["points"])-1
        if phase=='started':start=end=0
        elif start==end:return None
        points=tuple(state["points"][start:end+1]);first,last=points[0],points[-1]
        gesture="drag" if phase!='started' and any(abs(point[0]-state["points"][0][0])>state.get("drag_slop_px",(0,0))[0] or abs(point[1]-state["points"][0][1])>state.get("drag_slop_px",(0,0))[1] for point in state["points"]) else "press_release"
        definition=self.definitions.get(gesture)
        if definition is None:return None
        total=last[2]-state["points"][0][2];duration=last[2]-first[2];sequence=state["sequence"]+1
        status=("用户正按住角色拖拽，尚未松开" if gesture=="drag" else "用户正按住角色，尚未松开") if phase=="ongoing" else "用户已松开角色，交互结束"
        if phase=='started':status='用户开始接触角色：这是左键按下时刻的事实，后续保持、移动或松开尚未包含在本次观测中'
        description=f"{status}；本次新增观测区间持续{duration}毫秒，整次交互到此已持续{total}毫秒。"
        body_target=state["body_target"]
        if body_target is not None:description+="起始接触部位："+body_target["label"]+"。"
        content=Interaction(content_id=f"{gesture_id}:observation:{sequence}",description=description,
            recipient_refs=(self.recipient_ref,),semantic_key=definition["semantic_key"],parameters={
                "capture_version":"petlab-mouse-contact-v4" if phase=='started' else "petlab-mouse-observation-v3","source_kind":state["source_kind"],"gesture_id":gesture_id,"sequence":sequence,
                "phase":phase,"gesture":gesture,"button":"left","coordinate_system":"screen_pixels",
                "trajectory":points,"interval_duration_ms":duration,"total_duration_ms":total,
                "geometry_summary":summarize_features(points,allow_point=phase=='started'),**({"drag_slop_px":list(state["drag_slop_px"])} if state.get("drag_slop_px")!=(0,0) else {}),"gesture_definition":thaw_model_data(definition),
                "measurement_scope":'only the actual initial contact point; no later motion, hold or release' if phase=='started' else "only this new interval; first point is a continuity anchor; total duration is context",
                **({"body_target":thaw_model_data(body_target)} if body_target is not None else {})})
        result=MouseObservation(gesture_id,sequence,end,first,last,content);state["pending"]=result
        return result

    def observe(self,*,coordinate=None,gesture_id=None):
        return self._build_observation(gesture_id,"ongoing",coordinate)

    def observe_finished(self,gesture_id=None):
        return self._build_observation(gesture_id,"ended",None)

    def observe_started(self,gesture_id=None):
        return self._build_observation(gesture_id,'started',None)

    def freeze_boundary(self,gesture_id):
        """O(1) local capture marker; no geometry, model call or cursor advance."""
        with self._capture_lock:
            state=self._captures.get(gesture_id)
            if state is None or state['phase']!='ongoing':return None
            identity='boundary:'+uuid.uuid4().hex
            self._boundaries[identity]=(gesture_id,len(state['points'])-1,state['points'][0],state['points'][-1])
            return identity

    def observe_boundary(self,boundary_id):
        if not isinstance(boundary_id,str):raise ValueError('mouse boundary identity must be a string')
        with self._observation_lock:
            with self._capture_lock:
                boundary=self._boundaries.get(boundary_id)
                if boundary is None:raise ValueError('mouse boundary is unknown or already consumed')
            return self._build_observation(boundary[0],'ongoing',None,append_sample=False,boundary_id=boundary_id)

    def boundary_info(self,boundary_id):
        with self._capture_lock:
            boundary=self._boundaries.get(boundary_id)
            if boundary is None:raise ValueError('mouse boundary is unknown or already consumed')
            return {'gesture_id':boundary[0],'end_point':boundary[3]}

    def discard_boundary(self,boundary_id):
        # A superseded plan releases its marker, never consumes pointer data.
        with self._capture_lock:self._boundaries.pop(boundary_id,None)

    def _build_observation(self,gesture_id,phase,coordinate,*,append_sample=True,boundary_id=None):
        # Serialize builders, never pointer capture. The copied endpoint fixes
        # this interval while move/release callbacks continue appending.
        with self._observation_lock:
            with self._capture_lock:
                gesture_id=gesture_id or self._current_id;state=self._captures.get(gesture_id)
                if state is None:return None
                boundary=None if boundary_id is None else self._boundaries.get(boundary_id)
                if boundary_id is not None:
                    if (boundary is None or boundary[0]!=gesture_id or phase!='ongoing'
                            or boundary[1]>=len(state['points']) or boundary[2]!=state['points'][0]
                            or boundary[3]!=state['points'][boundary[1]] or boundary[1]<state['cursor']):
                        raise ValueError('mouse boundary differs from capture or accepted cursor')
                if phase=='started':
                    if state['sequence']!=0:return None
                    if state['pending'] is not None and state['pending'].content.parameters['phase']!='started':
                        raise ValueError('cannot insert contact before an already captured interval')
                elif boundary is None and state["phase"]!=phase:return None
                if state["pending"] is not None:
                    if boundary is not None and (state['pending'].end_index!=boundary[1]
                            or state['pending'].content.parameters['phase']!='ongoing'):
                        raise ValueError('pending interval must be accepted before this boundary')
                    return state["pending"]
                if boundary is not None and boundary[1]==state['cursor']:
                    self._boundaries.pop(boundary_id);return None
                if phase=="ongoing" and append_sample:
                    last=state["points"][-1]
                    point=self._point(last[0],last[1],self._now(coordinate))
                    if point[2]<last[2]:raise ValueError("mouse capture time moved backwards")
                    state["points"].append(point)
                end=1 if phase=='started' else len(state['points']) if boundary is None else boundary[1]+1
                copied={**state,"points":tuple(state['points'][:end])}
            result=self._snapshot(gesture_id,copied,phase)
            with self._capture_lock:
                if state["cursor"]!=copied["cursor"] or state["sequence"]!=copied["sequence"]:
                    raise ValueError("mouse observation cursor changed during construction")
                state["pending"]=result
            return result

    def accept(self,observation):
        with self._observation_lock,self._capture_lock:
            state=self._captures.get(observation.gesture_id)
            if state is None or state["pending"]!=observation or observation.sequence!=state["sequence"]+1:
                raise ValueError("mouse observation is stale or already accepted")
            if state["points"][state["cursor"]]!=observation.start_point or state["points"][observation.end_index]!=observation.end_point:
                raise ValueError("mouse observation differs from its physical capture")
            state["cursor"]=observation.end_index;state["sequence"]=observation.sequence;state["pending"]=None
            for identity,boundary in tuple(self._boundaries.items()):
                if boundary[0]==observation.gesture_id and boundary[1]<=observation.end_index:
                    self._boundaries.pop(identity)
