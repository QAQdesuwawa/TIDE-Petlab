"""Durable dispatch wrapper; UI playback never performs filesystem writes here."""
import time
from .composite_journal import ParentJournal
from .execution import ExecutionOutcome,ExecutionCancelled,ExecutionUncertain
from framework.model_api import model_data_fingerprint
from model_packages.desktop_pet_v1.composite import validate_outcome


def source_for(prepared):
    source={'prepared_fingerprint':prepared.fingerprint,'request':prepared.request.to_record(),
        'source_event_id':prepared.authorization.source_fact_ref}
    from model_packages.desktop_pet_v1.behavior import VERSION
    if prepared.request.control.get('version')==VERSION:
        # Writer candidates already have a sealed authorization. Local candidates
        # keep just this missing binding beside their existing behavior trace.
        source['local_authorization']=prepared.authorization.to_record()
        source['scope_ref']=prepared.scope_ref
    return source


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
            observation=journal.directory/'observations'/'obstruction.json'
            if observation.exists():
                import json
                from .execution import ExecutionBlocked
                raise ExecutionBlocked(json.loads(observation.read_text('utf8')))
            raise ExecutionCancelled('sealed parent confirms no effects')
        raise ExecutionUncertain('parent dispatch has no sealed outcome; reconcile before replay')
    if is_cancelled():raise ExecutionCancelled('source expired before parent dispatch')
    journal.begin_dispatch(plan,source,contents)
    try:
        outcome=executor.execute_composite(parent_id,contents,plan=plan,is_cancelled=is_cancelled)
    except ExecutionCancelled as error:
        from .execution import ExecutionBlocked
        blocked=error.observation if isinstance(error,ExecutionBlocked) else None
        if blocked:
            from petlab_app.multi_pet import _immutable
            _immutable(journal.directory/'observations'/'obstruction.json',blocked)
        selected={item.content_id for item in contents}
        cancelled=[]
        for key in plan.children:
            cancelled.append(('finish',{'child_id':key,'result':{'status':'cancelled','started_at_ms':None,
                'ended_at_ms':time.time_ns()//1_000_000,'trace_ref':str(journal.directory),
                'reason':('destination_occupied' if blocked else 'source_expired') if key in selected else 'no_content_selected',
                'cause_ref':(blocked['blocker_ref'] if blocked else parent_id+'#dispatch-cancel') if key in selected else None}}))
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
    app=product.application
    entry=app._product()['executions'].get(parent_id,{})
    if frozen_control.get('version')==LOCAL_VERSION:
        from .behavior import prepare,restore_admitted
        if 'admitted_position' in entry and 'local_authorization' in source:
            from petlab_app.live_execution import admitted_position
            prepared=restore_admitted(product,source,position=admitted_position(app,entry))
        else:prepared=prepare(product,event_id,private_thought=frozen_control.get('allow_thought',False))
    elif 'admitted_position' in entry:
        from petlab_app.live_execution import admitted_position
        request_id=source['request'].get('readonly_facts',{}).get('writer_packet',{}).get('writer_request_id')
        if not request_id:raise ExecutionUncertain('admitted Writer lacks its exact attempt identity')
        prepared=product.writer._restore_work(event_id,position=admitted_position(app,entry),
            only_request_id=request_id,ignore_retired=True)
        from .execution import PreparedExecution
        if not isinstance(prepared,PreparedExecution):
            raise ExecutionUncertain('admitted execution lacks a sealed Writer candidate')
    else:
        prepared=product.realizers.prepare(event_id)
    if prepared.fingerprint!=source.get('prepared_fingerprint') or prepared.request.to_record()!=source.get('request'):
        raise ValueError('recovered candidate differs from sealed parent source')
    plan,_=prepared.validate(binding=product.application.runtime.session.session_binding,
        owner_ref=product.application.owner_ref,user_ref=prepared.authorization.target_ref)
    if plan.action_id!=parent_id:raise ValueError('recovered parent identity differs')
    outcome=ExecutionOutcome(**state['outcome'])
    validate_outcome(state['plan'],outcome)
    if 'admitted_position' in entry:
        from petlab_app.live_execution import verify_recovery
        refs=verify_recovery(app,prepared,entry,allow_pending=True)
        from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
        pending=set(app._product()['sources'])-set(app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}))
        if pending-set(r['event_id'] for r in refs):
            raise ExecutionUncertain('unrelated pending source prevents effect recovery')
        for ref in refs:
            if ref['event_id'] in pending:
                from petlab_app.live_execution import settle_recovered_input
                settle_recovered_input(product,ref['event_id'],parent_id)
        from petlab_app.live_execution import finish_recovered_inputs
        finish_recovered_inputs(product,parent_id,refs)
    _finish_journal(journal,outcome)
    coordinate=outcome.details.get('occurred_at_wall_ms')
    if type(coordinate) is not int:raise ExecutionUncertain('sealed outcome lacks actual completion coordinate')
    if outcome.details.get('obstruction') is not None:
        from petlab_app.obstruction_input import enqueue
        enqueue(product,outcome.details['obstruction'])
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
