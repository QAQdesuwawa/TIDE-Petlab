"""Local calibration references bound to actual committed sources; no prompt access."""
from framework.model_api import thaw_model_data,model_data_fingerprint
from model_packages.cx_six_seven_v1.root_dynamics import flow_root_time
from model_packages.desktop_pet_v1.measurement_calibration import VERSION as CALIBRATION_VERSION
KEY='local_calibration_reference'
CONTEXT_KEY='desktop_pet_local_calibration_source'
VERSION='desktop-pet-local-calibration-reference-v1'

def reference(runtime,event,*,calibration_version=CALIBRATION_VERSION):
    from . import EVENT_JOURNAL_KEY
    from petlab_app.session import PRODUCT_KEY
    session=runtime.session;binding=session.parameters.parameters['constant_binding']
    params=runtime.model_runtime.manifest.parameter_package
    from model_packages.desktop_pet_v1.information_holder import enabled as holder_enabled
    holder_mode=holder_enabled(runtime)
    elapsed=event.position.coordinate-session.position.position.coordinate
    if elapsed<0:raise ValueError('calibration source cannot be in the future')
    roots=session.state.state
    from model_packages.desktop_pet_v1.relation_package import META,view
    if META in roots:
        from .public_disclosure import counterparty
        peer=counterparty(runtime,event)
        saved_roots=roots[META]['roots_by_peer'].get(peer)
        roots=(dict(runtime.model_runtime.manifest.parameter_package['initial_state_default']) if saved_roots is None else dict(saved_roots))
        if saved_roots is None:elapsed=0
    state=flow_root_time(binding['effective_constants'],roots,elapsed,
        effective_constants_version=binding['effective_constants_version'],params=params['progress_flow']).x_after
    from model_packages.desktop_pet_v1.measurement_constraint import VERSION as V4
    if calibration_version not in (CALIBRATION_VERSION,V4):raise ValueError('unsupported calibration reference policy')
    previous_id=next(reversed(session.profile.get(EVENT_JOURNAL_KEY,{})),None)
    if calibration_version==V4:
        kinds=sorted({c.kind for c in event.contents});previous_id=None
        sources=session.profile[PRODUCT_KEY]['sources']
        for identity in reversed(session.profile.get(EVENT_JOURNAL_KEY,{})):
            candidate=sources[identity]['event']
            if (candidate['event_kind']==event.event_kind and candidate['actor_ref']==event.actor_ref
                    and sorted({c['kind'] for c in candidate['contents']})==kinds
                    and (META not in session.state.state or (tuple(candidate['target_refs'])==tuple(event.target_refs) if holder_mode else candidate['target_refs']==list(event.target_refs)))):
                previous_id=identity;break
    previous_raw=None;previous_delta=None;fact_fp=None
    if previous_id is not None:
        memory=runtime.memory_instance(previous_id)
        node=memory.fact.model_payload['runtime_full_memory_node']['node_projection']
        saved=session.profile[PRODUCT_KEY]['sources'][previous_id].get('measurement',{})
        if 'committed_measurement' in saved:previous_raw=saved['committed_measurement']['variable_deltas']
        elif 'raw_measurement' in saved:
            from model_packages.cx_six_seven_v1.semantic_projection import coerce_projection
            previous_raw=coerce_projection(thaw_model_data(saved['raw_measurement']),event_kind=memory.event.event_kind).variable_deltas
        else:previous_raw=node['stimulus']
        age=event.position.coordinate-node['event']['committed_at_ms']
        evolution=memory.fact.model_payload['runtime_full_memory_node']['node_evolution_binding']
        before=flow_root_time(node['constant_binding']['effective_constants'],node['x_before'],age,effective_constants_version=node['constant_binding']['effective_constants_version'],params=evolution['root_time_parameters']).x_after
        after=flow_root_time(node['constant_binding']['effective_constants'],node['x_after'],age,effective_constants_version=node['constant_binding']['effective_constants_version'],params=evolution['root_time_parameters']).x_after
        previous_delta={k:after[k]-before[k] for k in before};fact_fp=memory.fact.fingerprint
    row={'version':VERSION,'calibration_version':calibration_version,'event_fingerprint':event.fingerprint,
        'session_binding_fingerprint':session.session_binding.fingerprint,'runtime_position_fingerprint':session.position.fingerprint,
        'state_fingerprint':session.state.fingerprint,'state':dict(state),
        'constants':thaw_model_data(binding['effective_constants']),'constants_version':binding['effective_constants_version'],
        'response_parameters':thaw_model_data(params['event_response']),
        'previous_event_id':previous_id,'previous_fact_fingerprint':fact_fp,
        'previous_stimulus':None if previous_raw is None else dict(previous_raw),
        'previous_delta':None if previous_delta is None else dict(previous_delta)}
    if calibration_version==V4:row['reference_policy']='same-actor-direction-content-kinds-v2-canonical-targets' if holder_mode else 'same-actor-direction-content-kinds-v1'
    row['fingerprint']=model_data_fingerprint(row)
    return row

def verify_runtime(runtime,event,measurement):
    if KEY not in measurement:return {}
    actual=reference(runtime,event,calibration_version=measurement[KEY].get('calibration_version',CALIBRATION_VERSION))
    # Earliest v18/v19/v20 drafts bind v3 implicitly; retain their sealed format.
    if 'calibration_version' not in measurement[KEY]:
        actual.pop('calibration_version',None)
        actual['fingerprint']=model_data_fingerprint({k:v for k,v in actual.items() if k!='fingerprint'})
    if model_data_fingerprint(measurement[KEY])!=model_data_fingerprint(actual):
        raise ValueError('local calibration reference differs from actual committed source')
    return {CONTEXT_KEY:actual['fingerprint']}
