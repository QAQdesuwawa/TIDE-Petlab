"""Reuse an existing player for an already selected single silent body action.

Run: python -B -m examples.existing_player
MemoryPlayer records in-memory frame IDs; it does not draw an actual desktop pet.
"""
from dataclasses import dataclass
import json
import time
from framework.model_api import model_data_fingerprint
from model_packages.desktop_pet_v1.action_candidates import choose
from model_packages.desktop_pet_v1.behavior import content,realized_contents
from model_packages.desktop_pet_v1.composite import CompositePlan,validate_outcome
from product_adapters.desktop_pet_v1.execution import ExecutionOutcome,ExecutionCancelled


@dataclass(frozen=True)
class Played:
    rendered_frames:int
    trace_ref:str
    status:str='completed'
    reason:str|None=None
    cause_ref:str|None=None


class ExistingPlayerExecutor:
    """Adapter only. Candidate choice and final acceptance remain in the host."""
    def __init__(self,play_tail):self.play_tail=play_tail

    def execute_guarded(self,action_id,contents,*,is_cancelled=lambda:False):
        plan=CompositePlan.create({c.content_id:{'kind':c.kind,'key':c.semantic_key} for c in contents})
        return self.execute_composite(action_id,contents,plan=plan,is_cancelled=is_cancelled)

    def __call__(self,action_id,contents):return self.execute_guarded(action_id,contents)

    def execute_composite(self,action_id,contents,*,plan,is_cancelled=lambda:False):
        contents=tuple(contents);plan.validate_contents(contents)
        if len(contents)!=1 or contents[0].kind!='action' or contents[0].semantic_key!='play_tail':
            raise ValueError('this small adapter supports one silent play_tail action only')
        if is_cancelled():raise ExecutionCancelled('cancelled before any player effect')
        started=time.time_ns()//1_000_000
        played=self.play_tail(is_cancelled)
        if not isinstance(played,Played):raise TypeError('player must return its actual Played result')
        if type(played.rendered_frames) is not int or played.rendered_frames<0:raise ValueError('invalid actual frame count')
        if played.rendered_frames==0 and played.status=='interrupted':raise ExecutionCancelled('player confirms zero displayed frames')
        if played.rendered_frames==0:raise ValueError('completed playback needs actual frames')
        item=contents[0];ended=time.time_ns()//1_000_000
        result={'status':played.status,'started_at_ms':started,'ended_at_ms':ended,'reason':played.reason,'cause_ref':played.cause_ref}
        children={key:(result if key==item.content_id else {'status':'cancelled','started_at_ms':None,
            'ended_at_ms':ended,'reason':'no_content_selected','cause_ref':None}) for key in plan.children}
        outcome=ExecutionOutcome((item.content_id,),played.trace_ref,{'composite_version':plan.version,
            'child_results':children,'action_results':{item.content_id:{'status':played.status,
                'rendered_frames':played.rendered_frames,'reason':played.reason,'cause_ref':played.cause_ref}}})
        validate_outcome(plan,outcome)
        return outcome


class MemoryPlayer:
    def __init__(self):self.frame_ids=[]
    def play_tail(self,is_cancelled):
        for index in range(3):
            if is_cancelled():
                return Played(len(self.frame_ids),self.trace(),'interrupted','source_expired','example:new-input')
            self.frame_ids.append('tail-frame:'+str(index))
        return Played(len(self.frame_ids),self.trace())
    def trace(self):return 'example:memory-frames:'+model_data_fingerprint(self.frame_ids)


def selection(eligible):
    # In an application these tokens/source come from the committed package
    # projection. Here they are declared fixtures for an adapter smoke test.
    return choose(eligible=eligible,tokens=[{'kind':'self','text':'[行为中等同向]'}],source='example:formal-fixture',
        allow_speech=False,catalog={'version':'example:one-action-v1','candidates':[{
            'key':'play_tail','description':'摆动自己的尾巴','all':[{'kind':'self','text':'[行为中等同向]'}]}]})


def run():
    chosen=selection(True);item=content('user:example',selection=chosen)
    plan=CompositePlan.create({item.content_id:{'kind':'action','key':'play_tail'}})
    player=MemoryPlayer();executor=ExistingPlayerExecutor(player.play_tail)
    outcome=executor.execute_composite('example:action:1',(item,),plan=plan)
    actual=realized_contents((item,),outcome)[0]
    partial=MemoryPlayer();adapter=ExistingPlayerExecutor(partial.play_tail)
    interrupted=adapter.execute_composite('example:action:2',(item,),plan=plan,is_cancelled=lambda:len(partial.frame_ids)>=2)
    cancelled=MemoryPlayer()
    try:ExistingPlayerExecutor(cancelled.play_tail).execute_composite('example:action:3',(item,),plan=plan,is_cancelled=lambda:True)
    except ExecutionCancelled:pass
    else:raise AssertionError('pre-effect cancellation must not dispatch')
    return {'selected_action':item.semantic_key,'completed_frames':len(player.frame_ids),
        'actual_description':actual.description,'partial_frames':len(partial.frame_ids),
        'partial_status':interrupted.details['action_results'][item.content_id]['status'],
        'cancelled_frames':len(cancelled.frame_ids),'ineligible_has_no_selection':selection(False) is None,
        'network_requests':0,'memory_committed':False,'native_animation_tested':False}


if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False,indent=2))
