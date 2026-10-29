"""Durable dispatch wrapper; UI playback never performs filesystem writes here."""
import time
from .composite_journal import ParentJournal
from .execution import ExecutionOutcome,ExecutionCancelled,ExecutionUncertain
from framework.model_api import model_data_fingerprint
from model_packages.desktop_pet_v1.composite import validate_outcome


def execute(*,root,parent_id,plan,source,contents,executor,is_cancelled):
    plan.validate_contents(contents)
    journal=ParentJournal(root,parent_id)
    state=journal.state()
    if 'plan' in state:
        if state['plan'].fingerprint!=plan.fingerprint or model_data_fingerprint(state['source'])!=model_data_fingerprint(source):
            raise ValueError('parent execution source changed')
        if 'outcome' in state:
            outcome=ExecutionOutcome(**state['outcome'])
            validate_outcome(plan,outcome)
            _finish_journal(journal,outcome)
            return outcome
        if state['closed'] and all(row['phase']=='finished' and row['result']['status']=='cancelled'
                                   for row in state['children'].values()):
            raise ExecutionCancelled('sealed parent confirms no effects')
        raise ExecutionUncertain('parent dispatch has no sealed outcome; reconcile before replay')
    if is_cancelled():raise ExecutionCancelled('source expired before parent dispatch')
    journal.begin_dispatch(plan,source,contents)
    try:
        outcome=executor.execute_composite(parent_id,contents,plan=plan,is_cancelled=is_cancelled)
    except ExecutionCancelled:
        selected={item.content_id for item in contents}
        cancelled=[]
        for key in plan.children:
            cancelled.append(('finish',{'child_id':key,'result':{'status':'cancelled','started_at_ms':None,
                'ended_at_ms':time.time_ns()//1_000_000,'trace_ref':str(journal.directory),
                'reason':'source_expired' if key in selected else 'no_content_selected',
                'cause_ref':parent_id+'#dispatch-cancel' if key in selected else None}}))
        journal.append_many(cancelled+[('close',{})])
        raise
    validate_outcome(plan,outcome)
    if 'occurred_at_wall_ms' not in outcome.details:
        outcome=ExecutionOutcome(outcome.completed_content_ids,outcome.trace_ref,
            {**dict(outcome.details),'occurred_at_wall_ms':time.time_ns()//1_000_000})
    # Publish complete outcome first: a crash while appending child summaries
    # can be recovered from this immutable result without another UI dispatch.
    journal.seal_outcome(outcome)
    _finish_journal(journal,outcome)
    return outcome


def _finish_journal(journal,outcome):
    state=journal.state()
    if state['closed']:return
    completed=[]
    for key,row in outcome.details['child_results'].items():
        result={**dict(row),'trace_ref':outcome.trace_ref}
        old=state['children'][key]
        if old['phase']=='finished':
            if model_data_fingerprint(old['result'])!=model_data_fingerprint(result):
                raise ValueError('sealed child differs from parent outcome')
        else:completed.append(('finish',{'child_id':key,'result':result}))
    journal.append_many(completed+[('close',{})])


def reconcile(product,parent_id):
    """Reconcile only a sealed outcome, never invoke an external executor."""
    journal=ParentJournal(product.gateway.trace_dir/'parents',parent_id)
    state=journal.state()
    if 'outcome' not in state:raise ExecutionUncertain('pending execution has no sealed outcome')
    source=state['source']
    event_id=source.get('source_event_id')
    if not event_id:raise ExecutionUncertain('parent has no reconstructible source')
    # Current local realization reconstructs its original immutable request and
    # sealed candidate; normal verifier checks Tick and settled snapshot again.
    from model_packages.desktop_pet_v1.behavior import VERSION as LOCAL_VERSION
    frozen_control=source.get('request',{}).get('control',{})
    if frozen_control.get('version')==LOCAL_VERSION:
        from .behavior import prepare
        prepared=prepare(product,event_id,private_thought=frozen_control.get('allow_thought',False))
    else:
        prepared=product.realizers.prepare(event_id)
    if prepared.fingerprint!=source.get('prepared_fingerprint') or prepared.request.to_record()!=source.get('request'):
        raise ValueError('recovered candidate differs from sealed parent source')
    plan,_=prepared.validate(binding=product.application.runtime.session.session_binding,
        owner_ref=product.application.owner_ref,user_ref=prepared.authorization.target_ref)
    if plan.action_id!=parent_id:raise ValueError('recovered parent identity differs')
    outcome=ExecutionOutcome(**state['outcome'])
    validate_outcome(state['plan'],outcome)
    _finish_journal(journal,outcome)
    coordinate=outcome.details.get('occurred_at_wall_ms')
    if type(coordinate) is not int:raise ExecutionUncertain('sealed outcome lacks actual completion coordinate')
    result=product.application.execute(prepared,executor=None,coordinate=coordinate,
        measure=product.semantic.measure,_recovered_outcome=outcome)
    acknowledge_idle(product.application,event_id)
    return result


def acknowledge_idle(app,event_id):
    from petlab_app.idle_opening import journal,publish
    data=journal(app)
    if data.get('pending_tick')==event_id:
        data['pending_tick']=None
        data['outcomes'][event_id]={'status':'executed'}
        publish(app,data)
