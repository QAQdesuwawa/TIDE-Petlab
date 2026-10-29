"""Generic host composition: scoped source reference -> signed selected read.

Packages own all scoring. This layer verifies the stage, authorizes exact IDs,
and records coverage; it never interprets dimensions, thresholds or emotions.
"""
from framework.history import HistoryAuthority,FRAMEWORK_HISTORY_PROFILE_KEY
from framework.model_api import model_data_fingerprint
from interaction_extensions.local_memory_source import register
from ops.runtime_stage_verification import verify_runtime_stage_execution
from ops.runtime_full_memory_candidate_authorization import authorize_runtime_full_memory_candidates
from ops.runtime_full_memory_input_product import _invoke_preparation


def prepare_indexed_input(*,session,model_runtime,context,current_readout,budget_ms):
    raw=session.profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
    history=(HistoryAuthority.from_record(raw) if raw is not None else
             HistoryAuthority(session_binding_fingerprint=session.session_binding.fingerprint))
    if history.session_binding_fingerprint!=session.session_binding.fingerprint:
        raise ValueError('indexed memory history belongs to another session')
    view=register(history,session_id=context.session_id,owner_ref=context.subject_ref)
    payload={'memory_source_view':view.reference,'query':{'parameters':session.parameters.parameters,
             **current_readout,'budget_ms':budget_ms}}
    execution=model_runtime.invoke('prepare_memory_inputs',payload=payload,context=context)
    verify_runtime_stage_execution(execution,stage='prepare_memory_inputs',runtime=model_runtime,
                                   context=context,invocation_payload=payload)
    coverage=execution.payload.get('memory_coverage')
    if (coverage is None or dict(coverage['snapshot_ref'])!=dict(view.reference)
            or coverage['examined_count']+coverage['unexamined_count']!=view.count
            or coverage['unexamined_is_negative'] is not False):
        raise ValueError('indexed memory coverage differs from authorized snapshot')
    candidates=tuple(execution.payload['memory_inputs']['candidate_event_ids'])
    if (any(type(coverage.get(key)) is not int or coverage[key]<0
            for key in ('examined_count','unexamined_count','pruned_count','candidate_count'))
            or coverage['candidate_count']!=len(candidates)
            or coverage['examined_count']!=coverage['pruned_count']+len(candidates)):
        raise ValueError('indexed memory inspection coverage is inconsistent')
    for identity in candidates:view.node(identity)
    evidence={'snapshot_ref':view.reference,'coverage':coverage,
              'preparation_stage_fingerprint':model_data_fingerprint(execution.receipt.to_record())}
    authorization=authorize_runtime_full_memory_candidates(profile=session.profile,
        session_binding_fingerprint=session.session_binding.fingerprint,context=context,
        requested_candidate_ids=candidates,source_evidence=evidence)
    product=_invoke_preparation(model_runtime=model_runtime,context=context,candidate_ids=candidates,
        invocation_payload=payload,candidate_authorization_fingerprint=authorization.receipt['fingerprint'],
        history_read=authorization.history_read,prepared_execution=execution)
    return authorization,product
