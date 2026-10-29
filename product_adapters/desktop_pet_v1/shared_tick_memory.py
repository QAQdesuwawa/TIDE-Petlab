"""Read-only package extension for old pet bindings; never a fabricated input.

The base runtime owns accepted state and History input preparation. The existing
opt-in CX67 package owns shared-tick recall, under its own registered binding.
"""
from types import SimpleNamespace
from dataclasses import replace
from framework.model_api import model_data_fingerprint,freeze_model_payload
from framework.model_registry import InteractionModelRegistry
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_api.memory import read_recall_decision
from ops.runtime_stage_verification import verify_runtime_stage_execution
from ops.runtime_full_memory_candidate_authorization import authorize_runtime_full_memory_candidates
from ops.runtime_full_memory_input_product import prepare_runtime_full_memory_input_product_from_profile
from model_packages.cx_six_seven_v1.autonomous_recall_package import CxSixSevenAutonomousRecallPackage
from model_packages.cx_six_seven_v1.reference_parameters import reference_full_parameter_package


def select(app,grant):
    session=grant.session;runtime=app.runtime.model_runtime
    if session.session_binding.model_binding!=runtime.binding or session!=app.runtime.session:
        raise ValueError('shared tick memory requires the current verified base session')
    shared=grant.shared_projection_execution;receipt=shared.receipt
    context=ModelInvocationContextV2(session_id=receipt.session_id,scope_ref=None,
        subject_ref=receipt.subject_ref,event_ref=receipt.event_ref,action_ref=None,
        temporal_position=session.position.position,runtime_ordinal=session.position.runtime_ordinal,
        metadata={'committed_event_kind':'tick'})
    settlement=grant.settlement_execution.payload['settlement']
    if model_data_fingerprint(settlement['state_candidate'])!=model_data_fingerprint(session.state.state):
        raise ValueError('shared tick memory settlement differs from accepted state')
    verify_runtime_stage_execution(shared,stage='project_shared_semantics',runtime=runtime,context=context,
        invocation_payload={'parameters':session.parameters.parameters,'settlement':settlement})
    indexed=app.runtime.memory_query_budget_ms is not None
    if indexed:
        from ops.runtime_indexed_memory_input import prepare_indexed_input
        from model_packages.desktop_pet_v1.tick_recall import DesktopPetTickRecallPackage
        context=replace(context,metadata={**context.metadata,
            'desktop_pet_memory_evidence':'desktop-pet-memory-batch-values-v3'})
        from model_packages.desktop_pet_v1 import grounded_recall
        if getattr(app,'memory_recall_version',None) in (grounded_recall.VERSION,'desktop-pet-declared-form-recall-v4','desktop-pet-source-bound-reference-recall-v5','desktop-pet-owner-bound-reference-recall-v6','desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8'):
            from . import EVENT_JOURNAL_KEY
            from model_packages.desktop_pet_v1.events import PetEvent
            identity=next(reversed(session.profile.get(EVENT_JOURNAL_KEY,{})),None)
            if identity:
                source=app._product()['sources'][identity];parsed=source.get('measurement',{}).get(grounded_recall.KEY)
                # Old events retain their original measurements. A new tick may
                # use their physical signature; absent textual cues stay empty.
                parsed=parsed or {'version':grounded_recall.VERSION,'cues':[]}
                from model_packages.desktop_pet_v1.recall_model import select
                model=select(parsed.get('version'))
                meta=model.metadata(app.runtime,PetEvent.from_record(source['event']),parsed)
                context=replace(context,metadata={**context.metadata,**meta})

        candidates,inputs=prepare_indexed_input(session=session,model_runtime=runtime,context=context,
            current_readout={'settlement':settlement,'shared_projection':shared.payload['shared_projection']},
            budget_ms=app.runtime.memory_query_budget_ms)
        package=DesktopPetTickRecallPackage(parameter_package=reference_full_parameter_package())
    else:
        candidates=authorize_runtime_full_memory_candidates(profile=session.profile,
            session_binding_fingerprint=session.session_binding.fingerprint,context=context)
        inputs=prepare_runtime_full_memory_input_product_from_profile(model_runtime=runtime,context=context,
            profile=session.profile,candidate_authorization=candidates)
        package=CxSixSevenAutonomousRecallPackage(parameter_package=reference_full_parameter_package())
    if 'directed_relation_binding' in runtime.manifest.parameter_package:
        from model_packages.desktop_pet_v1.relation_tick_recall import MultiPetTickRecallPackage
        package=MultiPetTickRecallPackage(parameter_package=runtime.manifest.parameter_package)
    if model_data_fingerprint(package.manifest.parameter_package)!=model_data_fingerprint(runtime.manifest.parameter_package):
        raise ValueError('shared tick extension parameter package differs from base')
    registry=InteractionModelRegistry();manifest=registry.register(package)
    extension=registry.bind(registry.binding_for(manifest.package_id,manifest.package_version),session_id=context.session_id)
    payload={**dict(inputs.memory_recall_payload),'parameters':session.parameters.parameters,
        'settlement':settlement,'shared_projection':shared.payload['shared_projection']}
    execution=extension.invoke('recall_history',payload=payload,context=context)
    verify_runtime_stage_execution(execution,stage='recall_history',runtime=extension,context=context,invocation_payload=payload)
    recall=read_recall_decision(execution,expected_context=context,candidate_event_ids=candidates.candidate_event_ids)
    history=app.runtime._history()
    material={'version':'desktop-pet-shared-tick-memory-extension-v1',
        'base_model_binding':runtime.binding.to_record(),'extension_model_binding':extension.binding.to_record(),
        'session_binding_fingerprint':session.session_binding.fingerprint,
        'observation_fingerprint':grant.receipt['fingerprint'],
        'candidate_authorization_fingerprint':candidates.receipt['fingerprint'],
        'input_product_fingerprint':inputs.receipt['fingerprint'],
        'shared_stage_receipt':shared.receipt.to_record(),'recall_stage_receipt':execution.receipt.to_record(),
        'recall_output_fingerprint':execution.receipt.output_fingerprint,
        'recalled_event_ids':list(recall.recalled_event_ids),
        **({'memory_coverage':inputs.preparation_execution.payload['memory_coverage']} if indexed else {}),
        'state_commit_authority':False,'memory_commit_authority':False,'send_authority':False}
    signed={**material,'fingerprint':model_data_fingerprint(material)}
    read=history.authorize_read(reader_ref=context.subject_ref,trigger_fact_id=context.event_ref,
        before_runtime_ordinal=context.runtime_ordinal+1,requested_fact_ids=recall.recalled_event_ids,
        authorization_ref={'shared_tick_extension_fingerprint':signed['fingerprint']})
    # This verified read belongs to this owner-local, read-only authority.
    # A fresh runtime._history() does not contain it: it was never a state
    # commit. Keep the read and its source together for link projection.
    return SimpleNamespace(recall=recall,history_read=read,history_source=history,receipt=freeze_model_payload(signed))
