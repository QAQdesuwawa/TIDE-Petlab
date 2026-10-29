"""Consume published package mapping metadata; commit only actual language delivery; visual-only facts do not spend it.

No root projection, new allocation math, action selection, or provider call lives
here. Both allocation and remainder arithmetic belong to the original package.
"""
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint, thaw_model_data
from model_packages.cx_six_seven_v1.expression_emission import (
    MAPPING_DESCRIPTOR_VERSION, compile_expression_emission_plan,
    commit_expression_emission_plan,
)
from ops.runtime_expression_state import (
    RUNTIME_EXPRESSION_STATE_KEY, restore_runtime_expression_state,
    initialize_runtime_expression_state,
)
from .expression import PetExpressionService

DELIVERY_VERSION = 'desktop-pet-expression-delivery-v1'
COMMIT_VERSION = 'desktop-pet-expression-commit-v1'
VISUAL_COMMIT_VERSION = 'desktop-pet-expression-commit-v2-visual-only'


def _signed(material):
    return dict(material, fingerprint=model_data_fingerprint(material))


def _verify_signed(record, version):
    if not isinstance(record, Mapping) or record.get('version') != version:
        raise ValueError('expression delivery version is invalid')
    material = {k:v for k,v in record.items() if k != 'fingerprint'}
    if model_data_fingerprint(material) != record.get('fingerprint'):
        raise ValueError('expression delivery fingerprint is invalid')


def _plan(delivery):
    payload = delivery['semantic_payload']
    batch = payload['expression_resource_batch']
    mapping = payload['mapping_receipt']
    if (model_data_fingerprint(payload) != delivery['semantic_output_fingerprint']
            or batch['source_shared_projection_output_fingerprint'] != delivery['shared_output_fingerprint']
            or mapping['source_shared_projection_output_fingerprint'] != delivery['shared_output_fingerprint']):
        raise ValueError('expression mapping differs from published stage')
    descriptors = []
    selection = batch['selection']
    for resource in batch['resources']:
        key = resource['resource_id']
        evidence = selection['resource_evidence'][key]
        source, axis = evidence['package_key'].split('::')
        if (source != 'shared_projection' or axis != resource['descriptor']['axis']
                or resource['source_receipt_refs'] != [delivery['shared_output_fingerprint']]):
            raise ValueError('expression resource provenance differs')
        descriptors.append(dict(version=MAPPING_DESCRIPTOR_VERSION, token_key=key,
            source=source, axis=axis, score=resource['score'], domain=resource['domain'],
            neutral=resource['neutral'], input_receipt={
                'source_shared_projection_output_fingerprint':delivery['shared_output_fingerprint'],
                'shared_projection_fingerprint':delivery['shared_projection_fingerprint']}))
    plan = compile_expression_emission_plan(descriptors,
        committed_remainders=delivery['before_state']['committed_remainders'],
        emission_unit=mapping['emission_unit'])
    selected = plan['selected_token_keys']
    if selected != selection['selected_resource_ids'] or selected != mapping['selected_resource_ids']:
        raise ValueError('expression plan differs from original selection')
    candidate_offsets = {item['token_key']:item['current_offset'] for item in plan['candidates']}
    for package in plan['packages']:
        deferred = {item['token_key']:item['reason'] for item in package['deferred']}
        for key in list(package['emitted_token_keys']) + list(deferred):
            expected = dict(selected=key in selected, current_deviation=candidate_offsets[key],
                package_key=package['package_key'], continuous_quota=package['continuous_quota'],
                requested_slots=package['requested_slots'], decision='selected' if key in selected else deferred[key])
            if selection['resource_evidence'][key] != expected:
                raise ValueError('expression plan differs from original package evidence')
    return plan


def prepare_delivery(application, event_id, packet, *, prompt_delivery=None, _expression=None):
    expression = (PetExpressionService(application=application).project(event_id).expression
        if _expression is None else _expression)
    if expression.context.event_ref != event_id:
        raise ValueError("expression belongs to a different input")
    session = application.runtime.session
    state = restore_runtime_expression_state(session.profile, session_binding=session.session_binding)
    if expression.receipt['committed_remainders_fingerprint'] != model_data_fingerprint(state.committed_remainders):
        raise ValueError('expression continuation uses a stale remainder anchor')
    source = application.runtime.memory_instance(event_id)
    delivery = dict(version=DELIVERY_VERSION, event_id=event_id,
        source_binding_ref=source.event.source_binding_ref,
        source_acceptance_fingerprint=source.fact.settlement_ref['acceptance_fingerprint'],
        before_state=state.to_record(),
        shared_output_fingerprint=expression.shared_projection_execution.receipt.output_fingerprint,
        shared_projection_fingerprint=expression.shared_projection_execution.payload['shared_projection']['fingerprint'],
        semantic_output_fingerprint=expression.semantic_resource_execution.receipt.output_fingerprint,
        semantic_payload=thaw_model_data(expression.semantic_resource_execution.payload),
        writer_packet_fingerprint=packet.packet.packet_fingerprint,
        writer_delivery_fingerprint=packet.receipt['fingerprint'],
        delivered_token_keys=list(packet.receipt['lane_delivery']['expression']['delivered_ids']))
    delivery['plan'] = _plan(delivery)
    supplied = packet.receipt['lane_delivery']['expression']['supplied_ids']
    selected = delivery['plan']['selected_token_keys']
    # The original resource reader preserves resource declaration order; the
    # original planner preserves package order. Identity, not order, is shared.
    if (len(supplied) != len(selected) or set(supplied) != set(selected)
            or [card.source_id for card in packet.packet.sections['expression']] != delivery['delivered_token_keys']):
        raise ValueError('expression plan differs from actual Writer packet delivery')
    if prompt_delivery is not None:
        if prompt_delivery['packet_fingerprint'] != packet.packet.packet_fingerprint:
            raise ValueError('prompt delivery belongs to a different packet')
        actual = list(prompt_delivery['expression_ids'])
        if len(actual) != len(set(actual)) or not set(actual).issubset(delivery['delivered_token_keys']):
            raise ValueError('actual prompt expression differs from supplied resources')
        delivery['delivered_token_keys'] = actual
        delivery['prompt_delivery_fingerprint'] = prompt_delivery['fingerprint']
    return _signed(delivery)


def _after(delivery, binding, action_id):
    _verify_signed(delivery, DELIVERY_VERSION)
    before = restore_runtime_expression_state({RUNTIME_EXPRESSION_STATE_KEY:delivery['before_state']},
        session_binding=binding)
    plan = _plan(delivery)
    if plan != delivery['plan'] or not set(delivery['delivered_token_keys']).issubset(plan['selected_token_keys']):
        raise ValueError('expression delivery differs from its original plan')
    result = commit_expression_emission_plan(plan, f16_committed=True,
        delivered_token_keys=delivery['delivered_token_keys'], committed_remainders=before.committed_remainders)
    material = {k:v for k,v in before.to_record().items() if k != 'fingerprint'}
    material.update(revision=before.revision+1, committed_remainders=result['remainder_updates'],
        last_committed_action_id=action_id)
    return _signed(material), result


def prepare_idle_delivery(application,event_id,packet,expression,prompt_delivery):
    from petlab_app.idle_opening import source
    tick,_grant=source(application,event_id,current=True)
    session=application.runtime.session
    state=restore_runtime_expression_state(session.profile,session_binding=session.session_binding)
    expected={'shared_projection':expression.shared_projection_execution.payload['shared_projection'],
        'source_shared_projection_output_fingerprint':expression.shared_projection_execution.receipt.output_fingerprint,
        'committed_remainders':state.committed_remainders}
    if model_data_fingerprint(expected)!=expression.semantic_resource_execution.receipt.input_fingerprint:
        raise ValueError('idle expression uses a stale remainder source')
    delivery=dict(version=DELIVERY_VERSION,source_kind='idle_tick',event_id=event_id,
        source_binding_ref=tick.row['fingerprint'],source_acceptance_fingerprint=tick.row['source_acceptance_fingerprint'],
        before_state=state.to_record(),shared_output_fingerprint=expression.shared_projection_execution.receipt.output_fingerprint,
        shared_projection_fingerprint=expression.shared_projection_execution.payload['shared_projection']['fingerprint'],
        semantic_output_fingerprint=expression.semantic_resource_execution.receipt.output_fingerprint,
        semantic_payload=thaw_model_data(expression.semantic_resource_execution.payload),
        writer_packet_fingerprint=packet.packet.packet_fingerprint,writer_delivery_fingerprint=packet.receipt['fingerprint'],
        delivered_token_keys=list(prompt_delivery['expression_ids']),prompt_delivery_fingerprint=prompt_delivery['fingerprint'])
    delivery['plan']=_plan(delivery)
    if not set(delivery['delivered_token_keys']).issubset(delivery['plan']['selected_token_keys']):raise ValueError('idle Writer delivery differs from selected resources')
    return _signed(delivery)


def prepare_deferred_delivery(application,event_id,packet,prompt_delivery):
    from .settlement_view import KEY,current_projection
    expression=current_projection(application);session=application.runtime.session
    memory=application.runtime.memory_instance(event_id)
    state=restore_runtime_expression_state(session.profile,session_binding=session.session_binding)
    delivery=dict(version=DELIVERY_VERSION,source_kind='deferred_reply',event_id=event_id,
        source_binding_ref=memory.event.source_binding_ref,
        source_acceptance_fingerprint=memory.fact.settlement_ref['acceptance_fingerprint'],
        planning_view=thaw_model_data(session.profile[KEY]),before_state=state.to_record(),
        shared_output_fingerprint=expression.shared_projection_execution.receipt.output_fingerprint,
        shared_projection_fingerprint=expression.shared_projection_execution.payload['shared_projection']['fingerprint'],
        semantic_output_fingerprint=expression.semantic_resource_execution.receipt.output_fingerprint,
        semantic_payload=thaw_model_data(expression.semantic_resource_execution.payload),
        writer_packet_fingerprint=packet.packet.packet_fingerprint,writer_delivery_fingerprint=packet.receipt['fingerprint'],
        delivered_token_keys=list(prompt_delivery['expression_ids']),prompt_delivery_fingerprint=prompt_delivery['fingerprint'])
    if prompt_delivery['packet_fingerprint']!=packet.packet.packet_fingerprint:
        raise ValueError('deferred prompt differs from packet')
    delivery['plan']=_plan(delivery)
    if not set(delivery['delivered_token_keys']).issubset(delivery['plan']['selected_token_keys']):
        raise ValueError('deferred delivery differs from selected resources')
    return _signed(delivery)


def _visual_only(evidence, contents):
    """A realized look is a fact, but is not delivery of spoken token resources."""
    from model_packages.desktop_pet_v1 import writer, spatial
    version=evidence.get('expression_realizer_version',evidence.get('version',''))
    if version.removesuffix(writer.MEMORY_MARKER_SUFFIX) not in {
            writer.VERSION,writer.CONTINUOUS_VERSION,writer.BODY_VERSION,writer.SUMMARY_VERSION,
            writer.IDLE_DISPLAY_VERSION,writer.OPTIONAL_IDLE_VERSION,writer.PRIVATE_EXPRESSION_VERSION}:
        return False
    rows=[item.to_record() if hasattr(item,'to_record') else item for item in contents]
    from model_packages.desktop_pet_v1.action_sequence import VERSION as SEQUENCE_VERSION
    if evidence.get('action_sequence_contract')==SEQUENCE_VERSION:
        return tuple(evidence.get('sequence_actual_channels',('missing',)))==() and bool(rows) and all(r.get('kind') in {'action','expression'} for r in rows)
    if evidence.get('speech_decision')!='silent' or evidence.get('reported_used_memory_ids'):return False
    return bool(rows) and all(item.get('kind')=='expression'
        and item.get('parameters',{}).get('presentation',{}).get('version')==spatial.PRESENTATION
        and item['parameters']['presentation'].get('gaze',{}).get('version')==spatial.GAZE
        and item['parameters']['presentation']['gaze'].get('status') in {'shown','unavailable'} for item in rows)


def commit_delivery(session, prepared, committed, completed_contents):
    delivery = prepared.candidate.realization_evidence.get('expression_delivery')
    if delivery is None:
        return None, None  # Legacy candidates have no new delivery evidence.
    delivery = thaw_model_data(delivery)
    if prepared.request.readonly_facts.get('expression_delivery') != prepared.candidate.realization_evidence['expression_delivery']:
        raise ValueError('expression delivery differs from verified Realizer request')
    from model_packages.desktop_pet_v1.writer import PRIVATE_EXPRESSION_VERSION
    kinds={'speech','thought'} if prepared.request.control.get('version')==PRIVATE_EXPRESSION_VERSION else {'speech'}
    if not any(item.kind in kinds for item in completed_contents):
        from model_packages.desktop_pet_v1.composite import PRESENTATION_VERSION
        from model_packages.desktop_pet_v1.spatial import GAZE
        from model_packages.desktop_pet_v1.action_sequence import VERSION as SEQUENCE_VERSION
        sequence=prepared.request.control.get('action_sequence_contract')==SEQUENCE_VERSION
        actual_evidence=committed.committed_fact.model_payload['execution_receipt']['realization_evidence']
        if (sequence and _visual_only(actual_evidence,completed_contents) or prepared.request.control.get('composite',{}).get('version')==PRESENTATION_VERSION
                and prepared.request.control.get('gaze_contract')==GAZE
                and _visual_only(prepared.candidate.realization_evidence,completed_contents)):
            state=restore_runtime_expression_state(session.profile,session_binding=session.session_binding)
            if state.to_record()!=delivery['before_state']:
                raise ValueError('visual delivery uses a stale committed remainder')
            receipt=_signed(dict(version=VISUAL_COMMIT_VERSION,
                action_id=committed.commit_plan.action_id,
                action_commit_fingerprint=committed.commit_receipt.fingerprint,
                committed_fact_fingerprint=committed.committed_fact.fingerprint,
                execution_receipt_fingerprint=committed.committed_fact.model_payload['execution_receipt']['fingerprint'],
                delivery=delivery,consumption='none',reason='visual_only',after_state=delivery['before_state']))
            return None, receipt  # Preserve causal provenance without spending language resources.
        raise ValueError('expression delivery has no actual expression completion')
    state = restore_runtime_expression_state(session.profile,session_binding=session.session_binding)
    if state.to_record() != delivery['before_state']:
        raise ValueError('expression delivery uses a stale committed remainder')
    after, arithmetic = _after(delivery, session.session_binding, committed.commit_plan.action_id)
    receipt = _signed(dict(version=COMMIT_VERSION, action_id=committed.commit_plan.action_id,
        action_commit_fingerprint=committed.commit_receipt.fingerprint,
        committed_fact_fingerprint=committed.committed_fact.fingerprint,
        execution_receipt_fingerprint=committed.committed_fact.model_payload['execution_receipt']['fingerprint'],
        delivery=delivery, arithmetic=arithmetic, after_state=after))
    return after, receipt


def verify_commits(application, product):
    """Replay only pure receipt arithmetic; never repeat physical effects or roots."""
    session = application.runtime.session
    expected = initialize_runtime_expression_state({},session_binding=session.session_binding)[RUNTIME_EXPRESSION_STATE_KEY]
    receipts = []
    for source in product['sources'].values():
        if source['origin'] != 'execution':
            continue
        actual = source['evidence']['fact']['model_payload']['execution_receipt']
        delivery = thaw_model_data(actual['realization_evidence'].get('expression_delivery'))
        receipt = thaw_model_data(source.get('expression_commit'))
        if delivery is None:
            if receipt is not None:
                raise ValueError('legacy output cannot acquire invented expression delivery')
            continue
        if receipt is None:
            raise ValueError('executed expression delivery is missing its commit')
        visual=receipt.get('version')==VISUAL_COMMIT_VERSION
        _verify_signed(receipt, VISUAL_COMMIT_VERSION if visual else COMMIT_VERSION)
        if (receipt['delivery'] != delivery or receipt['action_id'] != actual['action_id']
                or receipt['action_commit_fingerprint'] != source['evidence']['commit_receipt']['fingerprint']
                or receipt['committed_fact_fingerprint'] != source['evidence']['fact']['fingerprint']
                or receipt['execution_receipt_fingerprint'] != actual['fingerprint']
                or not (_visual_only(actual['realization_evidence'],actual['completed_contents']) if visual else any(item['kind'] in ({'speech','thought'} if actual['realization_evidence'].get('expression_realizer_version',actual['realization_evidence'].get('version'))=='desktop-pet-expression-v10-private-thought' else {'speech'}) for item in actual['completed_contents']))):
            raise ValueError('expression commit differs from actual output')
        if delivery.get('source_kind')=='idle_tick':
            from petlab_app.idle_opening import source as idle_source
            tick,_grant=idle_source(application,delivery['event_id'])
            expected_ref=tick.row['fingerprint'];acceptance=tick.row['source_acceptance_fingerprint']
            ordinal=tick.row['source_snapshot']['position']['runtime_ordinal'];position=tick.row['source_snapshot']['position']['position']['fingerprint']
        elif delivery.get('source_kind')=='deferred_reply':
            from .deferred_reply import verify_delivery
            ordinal,position=verify_delivery(application,delivery)
            input_memory=application.runtime.memory_instance(delivery['event_id'])
            expected_ref=input_memory.event.source_binding_ref;acceptance=input_memory.fact.settlement_ref['acceptance_fingerprint']
        else:
            input_memory=application.runtime.memory_instance(delivery['event_id'])
            expected_ref=input_memory.event.source_binding_ref;acceptance=input_memory.fact.settlement_ref['acceptance_fingerprint']
            ordinal=input_memory.fact.accepted_runtime_ordinal;position=input_memory.fact.temporal_position_fingerprint
        if (expected_ref != delivery['source_binding_ref'] or acceptance != delivery['source_acceptance_fingerprint']
                or source['evidence']['commit_receipt']['source_runtime_ordinal'] != ordinal
                or source['evidence']['commit_receipt']['source_position_fingerprint'] != position):
            raise ValueError('expression commit differs from its formal input')
        if visual:
            if (receipt.get('consumption')!='none' or receipt.get('reason')!='visual_only'
                    or receipt.get('after_state')!=delivery['before_state']):
                raise ValueError('visual fact cannot consume unspoken expression resources')
            continue
        after, arithmetic = _after(delivery, session.session_binding, actual['action_id'])
        if receipt['after_state'] != after or receipt['arithmetic'] != arithmetic:
            raise ValueError('expression commit differs from original remainder arithmetic')
        receipts.append(receipt)
    for receipt in sorted(receipts,key=lambda row:row['after_state']['revision']):
        if receipt['delivery']['before_state'] != expected:
            raise ValueError('expression remainder commit chain differs')
        expected = receipt['after_state']
    actual = restore_runtime_expression_state(session.profile,session_binding=session.session_binding)
    if actual.to_record() != expected:
        raise ValueError('expression state differs from actual committed deliveries')
