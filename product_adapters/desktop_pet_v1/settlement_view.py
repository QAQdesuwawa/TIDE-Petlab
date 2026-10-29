"""A current accepted settlement for read-only expression replanning.

This adapter archives existing products. It does not settle, measure, advance
time, select an action, authorize a reply, or invoke a provider.
"""
from dataclasses import replace
from ops.settlement_validation import verify_settlement
from framework.model_api import ModelStageExecution, freeze_model_payload, model_data_fingerprint, thaw_model_data
from framework.runtime_kernel import RuntimeStateCommitAuthority, RuntimeStateCommitAcceptance
from ops.runtime_product_expression_continuation import _context, _stage_receipt
from ops.model_expression_projection_shadow import produce_expression_projection_shadow
from ops.runtime_expression_state import restore_runtime_expression_state

KEY='desktop_pet_current_settlement_v1'


def attach(session, *, previous_state, previous_position, context, execution, acceptance, source_kind, source_ref, reaction_results=None):
    if source_kind not in {'event','progress'} or not isinstance(source_ref,str) or not source_ref:
        raise ValueError('current settlement source is invalid')
    verify_settlement(acceptance,session_binding=session.session_binding,
        previous_state=previous_state,previous_position=previous_position)
    if acceptance.state!=session.state or acceptance.position!=session.position:
        raise ValueError('current settlement differs from accepted session')
    if acceptance.stage_receipt_fingerprint!=model_data_fingerprint(execution.receipt.to_record()):
        raise ValueError('current settlement stage differs from acceptance')
    row={'version':KEY,'session_binding_fingerprint':session.session_binding.fingerprint,
        'parameters_fingerprint':session.parameters.fingerprint,'source_kind':source_kind,'source_ref':source_ref,
        'previous_state':previous_state.to_record(),'previous_position':previous_position.to_record(),
        'context':context.to_record(),'execution':{'payload':thaw_model_data(execution.payload),'receipt':execution.receipt.to_record()},
        'acceptance':acceptance.to_record()}
    from model_packages.desktop_pet_v1.reaction import build
    from model_packages.desktop_pet_v1 import relation_presentation as relations
    from model_packages.desktop_pet_v1.relation_package import META
    peer=relations.target(acceptance.state.state)
    entries={}
    previous=session.profile.get(KEY,{}).get('reaction')
    if peer is not None:
        entries=relations.read(session.profile,session_binding=session.session_binding.fingerprint,
            parameters=session.parameters.fingerprint,owner_ref=acceptance.state.state[META]['owner_ref'])
        # Seed only the provable latest old result on upgrade; never fabricate
        # a result for another relation or copy the active target's feedback.
        old=session.profile.get(KEY)
        if old is not None and 'reaction' in old:
            old_peer=relations.target(old['acceptance']['state']['state'])
            if old_peer is not None and old_peer not in entries:
                if old['fingerprint']!=model_data_fingerprint({k:v for k,v in old.items() if k!='fingerprint'}):
                    raise ValueError('previous settlement source integrity differs')
                entries[old_peer]=relations.entry(old,session_binding=session.session_binding.fingerprint,
                    parameters=session.parameters.fingerprint)
        previous=entries.get(peer,{}).get('reaction')
    from model_packages.desktop_pet_v1.relation_package import numerical_pair
    before,after=numerical_pair(previous_state.state,acceptance.state.state,execution.payload['settlement'])
    row['reaction']=build(previous=previous,before=before,after=after,
        source=acceptance.fingerprint,ordinal=session.position.runtime_ordinal,results=reaction_results,
        constant_binding=session.parameters.parameters["constant_binding"])
    row['fingerprint']=model_data_fingerprint(row)
    profile=dict(session.profile);profile[KEY]=row
    if peer is not None:
        entries[peer]=relations.entry(row,session_binding=session.session_binding.fingerprint,
            parameters=session.parameters.fingerprint)
        display=(peer if source_kind=='event' else session.profile.get(relations.KEY,{}).get('display_target_ref',peer))
        profile[relations.KEY]=relations.stored(entries,display)
    return replace(session,profile=profile)


def read_settlement(application,*,record=None,historical=False):
    app=application
    with app._lock:
        if not historical:app._require_ready()
        session=app.runtime.session;binding=session.session_binding
        row=session.profile.get(KEY) if record is None else record
        if row is None:raise ValueError('current settlement source is unavailable in this archive')
        row=thaw_model_data(row)
        if (row.get('version')!=KEY or row.get('session_binding_fingerprint')!=binding.fingerprint
                or row.get('parameters_fingerprint')!=session.parameters.fingerprint
                or row.get('fingerprint')!=model_data_fingerprint({k:v for k,v in row.items() if k!='fingerprint'})):
            raise ValueError('current settlement source integrity differs')
        authority=RuntimeStateCommitAuthority(session_binding=binding)
        previous_state=authority.restore_state(row['previous_state'])
        previous_position=authority.restore_position_state(row['previous_position'])
        record=row['acceptance']
        acceptance=RuntimeStateCommitAcceptance(**{**record,
            'state':authority.restore_state(record['state']),
            'position':authority.restore_position_state(record['position'])})
        verify_settlement(acceptance,session_binding=binding,previous_state=previous_state,previous_position=previous_position)
        if historical:
            session=replace(session,state=acceptance.state,position=acceptance.position)
        elif acceptance.state!=session.state or acceptance.position!=session.position:
            raise ValueError('current settlement source is stale')
        if row['source_kind']=='event':
            from . import EVENT_JOURNAL_KEY
            if row['source_ref'] not in session.profile.get(EVENT_JOURNAL_KEY,{}):
                raise ValueError('current settlement event source is missing')
            memory=app.runtime.memory_instance(row['source_ref']);app._verify_source(memory.event)
            if (memory.fact.settlement_ref['acceptance_fingerprint']!=acceptance.fingerprint
                    or memory.fact.accepted_runtime_ordinal!=session.position.runtime_ordinal
                    or memory.fact.temporal_position_fingerprint!=session.position.position.fingerprint):
                raise ValueError('current settlement differs from formal event source')
        elif row['source_kind']=='progress':
            if row['source_ref']!=acceptance.fingerprint or not any(
                    item['settlement_acceptance_fingerprint']==acceptance.fingerprint
                    and item['final_position_fingerprint']==session.position.fingerprint
                    for item in session.profile.get('runtime_progress_transactions',())):
                raise ValueError('current settlement lacks formal progress source')
        else:raise ValueError('current settlement source kind is invalid')
        context=_context(row['context'],session_binding=binding)
        execution=ModelStageExecution(payload=freeze_model_payload(row['execution']['payload']),
            receipt=_stage_receipt(row['execution']['receipt'],session_binding=binding))
        return session,previous_state,previous_position,context,execution,acceptance


def current_projection(application,*,record=None,historical=False,committed_remainders=None):
    app=application
    with app._lock:
        session,previous_state,previous_position,context,execution,acceptance=read_settlement(app,record=record,historical=historical)
        binding=session.session_binding
        state=restore_runtime_expression_state(session.profile,session_binding=binding)
        from .projection_cache import reuse
        remainders=state.committed_remainders if committed_remainders is None else committed_remainders
        return reuse(app,previous_state=previous_state,previous_position=previous_position,
            parameters=session.parameters,context=context,
            execution={'payload':execution.payload,'receipt':execution.receipt.to_record()},
            acceptance=acceptance,remainders=remainders,
            factory=lambda:produce_expression_projection_shadow(runtime=app.runtime.model_runtime,session_binding=binding,
                previous_state=previous_state,previous_position=previous_position,model_parameters=session.parameters,
                settlement_context=context,settlement_execution=execution,settlement_acceptance=acceptance,
                committed_remainders=remainders,
                expression_scope_ref=app.definition.scope_ref if context.scope_ref is None else None))
