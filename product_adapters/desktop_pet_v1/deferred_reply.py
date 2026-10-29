"""Original required-input authority with a separately bound planning state."""
from framework.action_protocol import ActionAuthorization
from framework.model_api import thaw_model_data
from ops.runtime_product_action_authorization import authorize_required_product_reply
from .expression import PetExpressionService
from .settlement_view import KEY,current_projection

ORIGIN='deferred_direct_reply_v1'


def needed(app,event_id):
    fact=app.runtime.memory_instance(event_id).fact
    return (fact.accepted_runtime_ordinal!=app.runtime.session.position.runtime_ordinal
        or fact.temporal_position_fingerprint!=app.runtime.session.position.position.fingerprint)


def authorize(app,event_id):
    lifecycle=PetExpressionService(application=app).project(event_id,historical=True)
    proof=authorize_required_product_reply(lifecycle)
    memory=app.runtime.memory_instance(event_id)
    for output in app._product()['sources'].values():
        if output['origin']!='execution':continue
        from model_packages.desktop_pet_v1.events import PetEvent
        app._verify_source(PetEvent.from_record(output['event']))
        receipt=output['evidence']['commit_receipt']
        delivery=output['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('expression_delivery',{})
        if delivery.get('source_kind')=='deferred_reply':
            verify_delivery(app,delivery)
            answered=delivery.get('event_id')==event_id
        else:
            answered=(receipt['source_runtime_ordinal'],receipt['source_position_fingerprint'])==(
                memory.fact.accepted_runtime_ordinal,memory.fact.temporal_position_fingerprint)
        if answered:
            raise ValueError('required input already has an actual reply')
    if not needed(app,event_id):raise ValueError('deferred reply needs a later planning state')
    if any(c.parameters.get('capture_version') in {'petlab-mouse-observation-v2','petlab-mouse-observation-v3','petlab-mouse-contact-v4'}
            and c.parameters.get('phase') in {'started','ongoing'} for c in memory.event.contents):
        raise ValueError('ongoing capture cannot become a deferred direct reply')
    projection=current_projection(app)
    view=app.runtime.session.profile[KEY]
    return ActionAuthorization.create(session_binding=app.runtime.session.session_binding,
        current_position=app.runtime.session.position,action_kind='direct_reply',subject_ref=app.owner_ref,
        target_ref=app.user_ref,source_fact_ref=event_id,
        source_settlement_fingerprint=memory.fact.settlement_ref['acceptance_fingerprint'],
        package_eligibility_evidence={'version':ORIGIN,'response_policy':'required',
            'source_fact_fingerprint':memory.fact.fingerprint,'source_binding_ref':memory.event.source_binding_ref},
        host_authorization_evidence={'origin':ORIGIN,'required_proof_fingerprint':proof.fingerprint,
            'lifecycle_receipt_fingerprint':lifecycle.receipt['fingerprint'],
            'planning_view_fingerprint':view['fingerprint'],
            'planning_acceptance_fingerprint':view['acceptance']['fingerprint'],
            'planning_event_ref':projection.context.event_ref})


def verify_delivery(app,delivery):
    """Validate a historical planning view without making it current again."""
    delivery=thaw_model_data(delivery)
    memory=app.runtime.memory_instance(delivery['event_id']);app._verify_source(memory.event)
    source=app._product(source_id=memory.event.event_id)['sources'][memory.event.event_id]
    if (source['origin']!='input' or source['evidence'].get('response_policy')!='required'
            or delivery['source_binding_ref']!=memory.event.source_binding_ref
            or delivery['source_acceptance_fingerprint']!=memory.fact.settlement_ref['acceptance_fingerprint']):
        raise ValueError('deferred delivery differs from original required input')
    view=delivery['planning_view']
    projection=current_projection(app,record=view,historical=True,
        committed_remainders=delivery['before_state']['committed_remainders'])
    if (projection.shared_projection_execution.receipt.output_fingerprint!=delivery['shared_output_fingerprint']
            or projection.semantic_resource_execution.receipt.output_fingerprint!=delivery['semantic_output_fingerprint']
            or thaw_model_data(projection.semantic_resource_execution.payload)!=delivery['semantic_payload']):
        raise ValueError('deferred delivery differs from accepted planning projection')
    position=view['acceptance']['position']
    if position['runtime_ordinal']<=memory.fact.accepted_runtime_ordinal:
        raise ValueError('deferred planning does not follow original input')
    return position['runtime_ordinal'],position['position']['fingerprint']
