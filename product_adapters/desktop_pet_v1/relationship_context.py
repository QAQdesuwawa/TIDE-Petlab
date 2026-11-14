"""Read-only per-edge projections through the existing package stages."""
import json
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.relation_package import META
from ops.runtime_progress_transaction import prepare_runtime_progress_for_settlement
from ops.runtime_stage_verification import verify_runtime_stage_execution
LEGACY_VERSION='petlab-relationship-expression-context-v1'
VERSION='petlab-relationship-expression-context-v2-read-only'

def _progress_for_peer(app,session,destination,peer,preparation):
    scope=destination['conversation']['conversation_id']
    metadata={'committed_event_kind':'tick','desktop_pet_counterparty_ref':peer}
    if preparation is None:
        progress=prepare_runtime_progress_for_settlement(session=session,model_runtime=app.runtime.model_runtime,
            temporal_runtime=app.runtime.temporal_runtime,target_coordinate=session.position.position.coordinate,
            scope_ref=scope,subject_ref=app.owner_ref,event_ref='relation-expression:'+peer,action_ref=None,
            context_metadata=metadata)
        return progress.context,progress.contribution,progress.preparation
    # Reuse only this build's already verified time preparation, never a peer's
    # contribution or eligibility. All model stages still run for each peer.
    from ops.runtime_authorities import runtime_authorities
    _,authority=runtime_authorities(session.session_binding,app.runtime.temporal_runtime)
    context=authority.invocation_context_v2(preparation.state,scope_ref=scope,subject_ref=app.owner_ref,
        event_ref='relation-expression:'+peer,action_ref=None,metadata=metadata)
    payload={'parameters':session.parameters.parameters,'state':session.state.state,
        'progress_delta':preparation.progress.to_record()}
    execution=app.runtime.model_runtime.invoke('advance_progress',payload=payload,context=context)
    verify_runtime_stage_execution(execution,stage='advance_progress',runtime=app.runtime.model_runtime,
        context=context,invocation_payload=payload)
    return context,execution.payload['progress_contribution'],preparation


def build(app,destination):
    state=app.runtime.session.state.state
    if META not in state or destination is None:return None
    session=app.runtime.session
    allowed=set(destination['audience'])|{destination['primary']}
    known=state[META]['roots_by_peer'];rows=[];preparation=None
    for peer in sorted(allowed & set(known)):
        context,contribution,preparation=_progress_for_peer(app,session,destination,peer,preparation)
        settlement_input={'state':session.state.state,'progress_contribution':contribution,'event_contribution':None}
        settlement=app.runtime.model_runtime.invoke('settle_state',payload=settlement_input,context=context)
        verify_runtime_stage_execution(settlement,stage='settle_state',runtime=app.runtime.model_runtime,
            context=context,invocation_payload=settlement_input)
        shared=app.runtime.model_runtime.invoke('project_shared_semantics',payload={
            'parameters':session.parameters.parameters,'settlement':settlement.payload['settlement']},context=context)
        resources=app.runtime.model_runtime.invoke('project_semantic_resources',payload={
            'shared_projection':shared.payload['shared_projection'],
            'source_shared_projection_output_fingerprint':shared.receipt.output_fingerprint,
            'committed_remainders':{}},context=context)
        batch=resources.payload['expression_resource_batch']
        # State descriptors are the mapper's output. No new psychological labels,
        # thresholds, cross-edge averaging, or remainder commits are introduced.
        rows.append({'owner_ref':app.owner_ref,'target_ref':peer,
            'tokens':[thaw_model_data(r['descriptor']) for r in batch['resources']],
            'projection_receipt':shared.receipt.to_record(),'mapping_receipt':resources.receipt.to_record()})
    value={'version':VERSION,'owner_ref':app.owner_ref,'active_peer':state[META]['active_peer'],
        'state_fingerprint':session.state.fingerprint,'position_fingerprint':session.position.fingerprint,
        'parameters_fingerprint':session.parameters.fingerprint,'destination_fingerprint':destination['fingerprint'],
        'relations':rows,'unestablished':sorted(allowed-set(known))}
    return {**value,'fingerprint':fp(value)}

def validate(app,value,destination):
    if value is None:
        if META in app.runtime.session.state.state and destination is not None:raise ValueError('relationship context missing')
        return None
    v=thaw_model_data(value);session=app.runtime.session
    if (v.get('version') not in {LEGACY_VERSION,VERSION} or v.get('fingerprint')!=fp({k:x for k,x in v.items() if k!='fingerprint'})
        or v['owner_ref']!=app.owner_ref or v['state_fingerprint']!=session.state.fingerprint
        or v['position_fingerprint']!=session.position.fingerprint or v['parameters_fingerprint']!=session.parameters.fingerprint
        or v['destination_fingerprint']!=destination['fingerprint']):raise ValueError('relationship context source differs')
    return v

def text(value,*,concise=False):
    if value is None:return ''
    rows=[{'主体':r['owner_ref'],'关系对象':r['target_ref'],'心理状态词元':r['tokens']} for r in value['relations']]
    if concise:
        return ('\n【本角色各条关系的当前心理状态】\n本轮表达资源对应：'+value['active_peer']+'。\n'
                +json.dumps(rows,ensure_ascii=False,separators=(',',':'))) if rows else ''
    return ('\n【本角色各条关系的当前心理状态】\n这些词元来自各条关系的正式数值，经原包投影和词元映射得到，是理解角色当前心理状态的主要依据。'
        '每组只属于所标注的主体→关系对象；结合人设与已交付记忆形成关注和表达。'
        '前面的本轮表达资源对应关系对象：'+value['active_peer']+'。\n'
        +json.dumps(rows,ensure_ascii=False,separators=(',',':'))
        +'\n尚未形成独立关系状态的对象：'+json.dumps(value['unestablished'],ensure_ascii=False))

