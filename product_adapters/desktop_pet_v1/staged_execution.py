"""Single Actor prepares language while an independent UI executor plays locals."""
import threading
import time
from concurrent.futures import Future
from model_packages.desktop_pet_v1.composite import CompositePlan,validate_outcome
from .composite_journal import ParentJournal
from .composite_execution import _finish_journal
from .composite_writer import prepare_speech_child
from .execution import ExecutionCancelled,ExecutionUncertain,ExecutionOutcome


def execute_staged(product,event_id,prepared,parent_id,contents,executor,is_cancelled):
    if not callable(getattr(executor,'execute_staged',None)):
        raise ExecutionCancelled('executor has no staged playback capability')
    plan=CompositePlan.create(prepared.request.control['composite']['children'])
    slot=prepared.candidate.candidate_payload['late_speech']['content_id']
    private=prepared.request.control.get('staged_version')=='desktop-pet-staged-v2-private-thought'
    slots={'speech':slot}
    if private:slots['thought']=prepared.candidate.candidate_payload['late_speech']['thought_content_id']
    source={'prepared_fingerprint':prepared.fingerprint,'request':prepared.request.to_record(),'source_event_id':event_id}
    journal=ParentJournal(product.gateway.trace_dir/'parents',parent_id)
    state=journal.state()
    if 'plan' in state:raise ExecutionUncertain('staged parent already exists; resume sealed execution')
    if is_cancelled():raise ExecutionCancelled('source expired before staged playback')
    journal.begin_dispatch(plan,source,contents)
    future=Future();outcomes=[];errors=[]
    def play():
        try:outcomes.append(executor.execute_staged(parent_id,contents,plan=plan,late_speech=future,is_cancelled=is_cancelled))
        except BaseException as error:errors.append(error)
    player=threading.Thread(target=play,name='pet-composite-playback')
    player.start()
    child=None;quiet_decision=None
    try:
        if is_cancelled():raise ExecutionCancelled('source expired before language preparation')
        if private:
            from .composite_writer import prepare_expression_children
            child=prepare_expression_children(product,event_id,child_ids=slots)
            dispatch=[]
            for item in child.contents:
                dispatch.extend((('prepare',{'child_id':item.content_id,'evidence':child.evidence}),
                    ('dispatch',{'child_id':item.content_id})))
            if dispatch:journal.append_many(dispatch)
            future.set_result(child.contents)
        else:
            child=prepare_speech_child(product,event_id,child_id=slot)
            journal.append_many((('prepare',{'child_id':slot,'evidence':child.evidence}),('dispatch',{'child_id':slot})))
            future.set_result(child.content)
    except Exception as error:
        from .quiet_realization import SpeechNotSelected
        if isinstance(error,SpeechNotSelected):quiet_decision=error.decision
        future.set_exception(error)
    # Provider transport has its own bounded timeout; UI remains independent.
    player.join()
    if errors:
        if isinstance(errors[0],ExecutionCancelled):
            cancelled=[]
            for key in plan.children:
                cancelled.append(('finish',{'child_id':key,'result':{'status':'cancelled','started_at_ms':None,'ended_at_ms':time.time_ns()//1_000_000,
                    'reason':'source_expired','cause_ref':parent_id+'#dispatch-cancel','trace_ref':str(journal.directory)}}))
            journal.append_many(cancelled+[('close',{})])
        raise errors[0]
    if len(outcomes)!=1:raise ExecutionUncertain('staged playback has no unique outcome')
    outcome=outcomes[0]
    validate_outcome(plan,outcome)
    details=dict(outcome.details)
    if quiet_decision is not None:details['speech_realization_decision']=quiet_decision.evidence
    # Tool parameters are not a spoken effect; preserve them even for a silent child.
    candidate=(child.prepared.candidate.to_record() if child is not None else quiet_decision.evidence['candidate'] if quiet_decision is not None else None)
    if candidate is not None and candidate['realization_evidence'].get('reading_intent') is not None:
        intent=candidate['realization_evidence']['reading_intent']
        if intent['source_tick']!=event_id:raise ValueError('reading parameters belong to another source')
        details['reading_intent']={'intent':intent,'candidate_fingerprint':candidate['fingerprint']}
    if set(slots.values()).intersection(outcome.completed_content_ids):
        if child is None:raise ExecutionUncertain('displayed speech has no sealed Writer candidate')
        details['expression_children_evidence' if private else 'speech_child_evidence']=child.evidence
    if 'occurred_at_wall_ms' not in details:details['occurred_at_wall_ms']=time.time_ns()//1_000_000
    outcome=ExecutionOutcome(outcome.completed_content_ids,outcome.trace_ref,details)
    journal.seal_outcome(outcome)
    _finish_journal(journal,outcome)
    return outcome
