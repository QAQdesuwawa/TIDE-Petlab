"""One product phase uses typed pet cues and the original reference parser."""
from __future__ import annotations

from framework.model_api import model_data_fingerprint,thaw_model_data
from framework.provider_codec import ProviderCodecParseFailureSignal
from model_packages.desktop_pet_v1.semantic import DesktopPetSemanticCodec,DesktopPetInteractionSemanticCodec,DesktopPetBodySemanticCodec
from model_packages.desktop_pet_v1.interaction_delivery import has_observations,has_body_targets
from model_packages.cx_six_seven_v1.semantic_projection import coerce_projection
from petlab_app.providers import completion_text


def _readonly_scope(method):
    from functools import wraps
    from contextlib import nullcontext
    @wraps(method)
    def scoped(self,*args,**kwargs):
        with self.gateway.readonly_views_scope() if self.defer_audit_views else nullcontext():
            return method(self,*args,**kwargs)
    return scoped


class PetSemanticService:
    query_tree_enabled=False  # Explicit legacy contract for retained consumers.
    def __init__(self,*,application,gateway,memory_recall_version=None,defer_audit_views=False,segmented_measurement=False,compact_history_measurement=False):
        self.application=application
        self.gateway=gateway
        if type(defer_audit_views) is not bool:raise ValueError('audit view mode must be boolean')
        self.defer_audit_views=defer_audit_views
        if type(segmented_measurement) is not bool or (segmented_measurement and memory_recall_version!='desktop-pet-property-address-recall-v8'):
            raise ValueError('segmented measurement requires the v8 address contract')
        self.segmented_measurement=segmented_measurement
        if type(compact_history_measurement) is not bool or (compact_history_measurement and memory_recall_version!='desktop-pet-property-address-recall-v8'):
            raise ValueError('compact historical measurement requires the v8 address contract')
        self.compact_history_measurement=compact_history_measurement
        from model_packages.desktop_pet_v1.semantic import DesktopPetConstrainedSemanticCodec
        self.codec=DesktopPetConstrainedSemanticCodec()
        from model_packages.desktop_pet_v1.grounded_recall import VERSION
        if memory_recall_version not in (None,VERSION,'desktop-pet-declared-form-recall-v4','desktop-pet-source-bound-reference-recall-v5','desktop-pet-owner-bound-reference-recall-v6','desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8'):raise ValueError('unsupported pet memory recall configuration')
        self.memory_recall_version=memory_recall_version
        if memory_recall_version:
            from model_packages.desktop_pet_v1.semantic import DesktopPetAtomicGroundedSemanticCodec
            self.codec=DesktopPetAtomicGroundedSemanticCodec()
        if memory_recall_version=='desktop-pet-property-address-recall-v8':
            from model_packages.desktop_pet_v1.address_semantics import QueryAddressPropertyCodec
            self.codec=QueryAddressPropertyCodec(DesktopPetConstrainedSemanticCodec())
        from .temporal_measurement import verify_saved
        verify_saved(application,gateway)
        from .continuation import verify_saved as verify_continuations
        verify_continuations(application)

    def retry_measure(self,event):
        """Explicit host recovery only; unknown dispatch is never retried."""
        return self.measure(event,retry_failed=True)

    def measure(self,event,*,retry_failed=False):
        from petlab_app.measurement_pipeline import measure as pipeline_measure,rebase
        source=self.application._product()['sources'][event.event_id]
        if 'prefetched_payload' in source:
            if model_data_fingerprint(source['prefetched_payload'])!=source['prefetched_payload_fingerprint']:
                raise ValueError('prefetched semantic payload differs')
            getattr(self,'_prefetched',{}).pop(event.event_id,None)
            return rebase(self,event,thaw_model_data(source['prefetched_payload']))
        cached=getattr(self,'_prefetched',{}).get(event.event_id)
        if cached is not None:
            work,future=cached
            future.result()
            from petlab_app.measurement_pipeline import stash
            original=work.finish()
            stash(self.application,event.event_id,original)
            value=rebase(self,event,original)
            del self._prefetched[event.event_id]
            return value
        work=self.prepare(event,retry_failed=retry_failed)
        return rebase(self,event,pipeline_measure(self,event,work))

    @_readonly_scope
    def prepare(self,event,*,retry_failed=False):
        from .prepared_measurement import PreparedMeasurement
        return PreparedMeasurement(self.application,event,self._steps(event,retry_failed=retry_failed))

    def _steps(self,event,*,retry_failed=False):
        app=self.application
        from model_packages.desktop_pet_v1.address_semantics import AddressPropertyCodec,QueryAddressPropertyCodec
        from model_packages.desktop_pet_v1.hierarchy_semantics import HierarchyReferentCodec
        hierarchy_codec_type=HierarchyReferentCodec
        # A source already received or executed is required before measuring it.
        app._verify_source(event)
        rag=getattr(app,'rag_memory',None)
        if rag is not None:rag.prefetch(event)
        from model_packages.desktop_pet_v1.semantic import DesktopPetContactSemanticCodec
        is_contact=any(c.kind=='interaction' and c.parameters.get('capture_version')=='petlab-mouse-contact-v4' for c in event.contents)
        address_mode=self.memory_recall_version=='desktop-pet-property-address-recall-v8'
        if address_mode:hierarchy_codec_type=QueryAddressPropertyCodec
        hierarchy_mode=address_mode or self.memory_recall_version=='desktop-pet-hierarchical-property-recall-v7'
        owner_mode=self.memory_recall_version=='desktop-pet-owner-bound-reference-recall-v6'
        source_bound_mode=owner_mode or self.memory_recall_version=='desktop-pet-source-bound-reference-recall-v5'
        declared_mode=source_bound_mode or self.memory_recall_version=='desktop-pet-declared-form-recall-v4'
        from petlab_app.participant_input import VISIBLE_VERSION
        visible_receipt=app._product()['sources'][event.event_id].get('evidence',{}).get('input_source',{})
        is_visible=visible_receipt.get('version')==VISIBLE_VERSION
        from .public_disclosure import enabled as public_disclosure_enabled
        literal_parent_mode=is_visible and public_disclosure_enabled(app.runtime)
        tree_mode=(self.query_tree_enabled and self.memory_recall_version=='desktop-pet-grounded-retention-recall-v2'
            and event.event_kind=='peer_input' and ({c.kind for c in event.contents}=={'speech'} or is_visible))
        if hierarchy_mode or tree_mode:
            from model_packages.desktop_pet_v1.semantic import DesktopPetConstrainedSemanticCodec
            codec=DesktopPetConstrainedSemanticCodec()
        else:codec=self.codec  # new events use the clean contract; retained intervals select their frozen codec below
        if has_observations(event.contents):
            from model_packages.desktop_pet_v1.semantic import DesktopPetNaturalInteractionSemanticCodec,DesktopPetReferenceInteractionSemanticCodec,DesktopPetEvidenceInteractionSemanticCodec,DesktopPetCleanEvidenceInteractionSemanticCodec,DesktopPetMagnitudeInteractionSemanticCodec,DesktopPetCalibratedInteractionSemanticCodec,DesktopPetConstrainedInteractionSemanticCodec,DesktopPetGroundedInteractionSemanticCodec,DesktopPetAtomicGroundedInteractionSemanticCodec
            from .temporal_measurement import KEY
            saved=app.runtime.session.profile.get(KEY,{}).get(event.event_id)
            if saved is not None and saved['codec_contract'].endswith((':participant-source-v1',':participant-pair-source-v2',':structured-participants-v1',':participant-labels-v1',':information-holder-v1',':query-parent-fallback-v1')):
                # Restore the sealed result, not a new reconstruction of its prompt.
                from .temporal_measurement import load,verify_capture_chain,publish
                verify_capture_chain(app,event)
                completed,payload=load(app,self.gateway,event,saved,allow_failed=retry_failed)
                if payload is not None:
                    if saved['status']!='parsed':publish(app,event.event_id,completed)
                    return payload
                saved=None  # Explicit recovery of a proven terminal failure only.
            elif saved is not None and retry_failed and saved['status']=='reserved':
                # Only a verified terminal failure permits a fresh current contract.
                # Successful sealed data and uncertain dispatch keep their old path.
                from .temporal_measurement import load
                _completed,prior_payload=load(app,self.gateway,event,saved,allow_failed=True)
                if prior_payload is None:saved=None
            versions={c.request_version:c for c in (DesktopPetInteractionSemanticCodec,DesktopPetBodySemanticCodec,DesktopPetContactSemanticCodec,DesktopPetNaturalInteractionSemanticCodec,DesktopPetReferenceInteractionSemanticCodec,DesktopPetEvidenceInteractionSemanticCodec,DesktopPetCleanEvidenceInteractionSemanticCodec,DesktopPetMagnitudeInteractionSemanticCodec,DesktopPetCalibratedInteractionSemanticCodec,DesktopPetConstrainedInteractionSemanticCodec,DesktopPetGroundedInteractionSemanticCodec,DesktopPetAtomicGroundedInteractionSemanticCodec)}
            if saved is not None:
                contract=saved['codec_contract']
                from model_packages.desktop_pet_v1.hierarchy_semantics import SUFFIX,EVIDENCE_SUFFIX,SEPARATED_SUFFIX,REFERENT_SUFFIX,HierarchyCodec,HierarchyEvidenceCodec,HierarchySeparatedEvidenceCodec,HierarchyReferentCodec
                from model_packages.desktop_pet_v1.address_semantics import SUFFIX as ADDRESS_SUFFIX,QUERY_SUFFIX,AddressPropertyCodec,QueryAddressPropertyCodec
                hierarchy_suffix=next((suffix for suffix in (QUERY_SUFFIX,ADDRESS_SUFFIX,REFERENT_SUFFIX,SEPARATED_SUFFIX,EVIDENCE_SUFFIX,SUFFIX) if contract.endswith(suffix)),None)
                hierarchy_mode=hierarchy_suffix is not None
                if hierarchy_mode:
                    contract=contract[:-len(hierarchy_suffix)]
                    hierarchy_codec_type={QUERY_SUFFIX:QueryAddressPropertyCodec,ADDRESS_SUFFIX:AddressPropertyCodec,SUFFIX:HierarchyCodec,EVIDENCE_SUFFIX:HierarchyEvidenceCodec,SEPARATED_SUFFIX:HierarchySeparatedEvidenceCodec,REFERENT_SUFFIX:HierarchyReferentCodec}[hierarchy_suffix]
                owner_suffix=':reference-property-owners-v1'
                owner_mode=contract.endswith(owner_suffix)
                contract=contract[:-len(owner_suffix)] if owner_mode else contract
                source_suffix=':source-bound-reference-v1'
                source_bound_mode=contract.endswith(source_suffix)
                contract=contract[:-len(source_suffix)] if source_bound_mode else contract
                suffix=':current-form-declaration-v1'
                declared_mode=contract.endswith(suffix)
                contract=contract[:-len(suffix)] if declared_mode else contract
                if contract not in versions:raise ValueError('retained semantic codec version unsupported')
                codec=versions[contract]()
            else:codec=DesktopPetAtomicGroundedInteractionSemanticCodec() if self.memory_recall_version and not hierarchy_mode else DesktopPetConstrainedInteractionSemanticCodec()
        if is_visible:
            import json
            from model_packages.desktop_pet_v1.visible_semantic import ScopedRecipientVisibleSemanticCodec,AtomicGroundedScopedRecipientVisibleSemanticCodec,HierarchicalRecipientVisibleSemanticCodec
            artifact=json.loads((app.store.path.with_suffix('.resources')/visible_receipt['artifact']).read_text(encoding='utf8'))
            codec_type=AtomicGroundedScopedRecipientVisibleSemanticCodec if self.memory_recall_version and not hierarchy_mode and not tree_mode else ScopedRecipientVisibleSemanticCodec
            if hierarchy_mode:codec_type=HierarchicalRecipientVisibleSemanticCodec
            codec=codec_type(target_refs=artifact['visible']['target_refs'])
        elif any(c.kind=='expression' or c.parameters.get('composite_version') in {'desktop-pet-composite-v1','desktop-pet-composite-v4-sequence'} for c in event.contents):
            from model_packages.desktop_pet_v1.composite_semantic import ConstrainedCompositeSemanticCodec,AtomicGroundedCompositeSemanticCodec
            codec=AtomicGroundedCompositeSemanticCodec() if self.memory_recall_version and not hierarchy_mode else ConstrainedCompositeSemanticCodec()
        from .calibration import reference,KEY as CALIBRATION_KEY
        local_reference=reference(app.runtime,event,calibration_version=getattr(codec,'calibration_version','desktop-pet-joint-axis-calibration-v3')) if getattr(codec,"local_calibration",False) else None
        from .measurement_context import build as continuity_reference
        continuity=continuity_reference(app.runtime,event,local_reference)
        context_refs=()
        if continuity is not None:
            self.gateway.archive_readonly(self.gateway.trace_dir/'measurement-contexts'/(continuity['fingerprint']+'.json'),continuity)
            context_refs=(continuity['previous_fact_fingerprint'],'pet-measurement-context:'+continuity['fingerprint'])
            if continuity['status']=='delivered':
                from model_packages.desktop_pet_v1.measurement_context import ContinuitySemanticCodec
                codec=ContinuitySemanticCodec(codec)
        from .input_reference_window import build as input_reference
        input_context=input_reference(app.runtime,event) if self.memory_recall_version and not hierarchy_mode else None
        if input_context is not None:
            self.gateway.archive_readonly(self.gateway.trace_dir/'measurement-contexts'/(input_context['fingerprint']+'.json'),input_context)
            context_refs=(*context_refs,*(x['source']['fact_fingerprint'] for x in input_context['sources']),'pet-input-reference:'+input_context['fingerprint'])
            if input_context['status']=='delivered' and not tree_mode:
                from model_packages.desktop_pet_v1.reference_window import WindowSemanticCodec
                codec=WindowSemanticCodec(codec)
        if tree_mode:
            from model_packages.desktop_pet_v1.query_tree import GroundedTreeQuerySemanticCodec as TreeQuerySemanticCodec
            codec=TreeQuerySemanticCodec(codec)
        if declared_mode:
            from model_packages.desktop_pet_v1.declared_forms import DeclaredFormCodec
            codec=DeclaredFormCodec(codec)
        if source_bound_mode:
            from model_packages.desktop_pet_v1.source_bound_recall import SourceBoundCodec
            codec=SourceBoundCodec(codec)
        if owner_mode:
            from model_packages.desktop_pet_v1.reference_owners import OwnerReferenceCodec
            codec=OwnerReferenceCodec(codec)
        compact=self.compact_history_measurement and address_mode and hierarchy_mode and not has_observations(event.contents) and any(c.kind=='speech' for c in event.contents)
        referents=None
        predecessors=()
        if address_mode and hierarchy_mode and not is_visible:
            from .predecessor_reference import build as predecessor_sources,build_public
            predecessors,predecessor_text=(build_public(app,event) if visible_receipt.get('version') else predecessor_sources(app,event))
        if predecessors:
            from model_packages.desktop_pet_v1.predecessor_reference import PredecessorCodec,PublicPredecessorCodec
            predecessor_codec=PublicPredecessorCodec if visible_receipt.get("version") else PredecessorCodec
            codec=predecessor_codec(hierarchy_codec_type(codec),predecessor_text)
            context_refs=(*context_refs,*(ref['fact_fingerprint'] for _,ref in predecessors))
        elif hierarchy_mode and not (compact and event.event_kind=='npc_output') and getattr(hierarchy_codec_type,'referent_context_enabled',False):
            from .referent_context import build as referent_context
            referents=referent_context(app,event)
            if referents is not None:
                self.gateway.archive_readonly(self.gateway.trace_dir/'measurement-contexts'/(referents['fingerprint']+'.json'),referents)
                context_refs=(*context_refs,*(x['fact_fingerprint'] for x in referents['sources']),'pet-measurement-referents:'+referents['fingerprint'])
            codec=hierarchy_codec_type(codec,referent_text=referents['text'] if referents is not None and referents['status']=='delivered' else '')
        elif hierarchy_mode:
            codec=hierarchy_codec_type(codec)
        hint=input_context['text'] if input_context is not None and input_context['status']=='delivered' else continuity['text'] if continuity is not None else ''
        from model_packages.desktop_pet_v1.web_observation import is_observation,ObservationSemanticCodec
        if is_observation(event):codec=ObservationSemanticCodec(codec,interval=any(c.parameters.get('capture_version')=='pet-external-observation-v2' for c in event.contents))
        if event.event_kind=='npc_output':
            from .referent_context import build as causal_parent
            parent=causal_parent(app,event)
            parent_text=''
            if parent is not None:
                self.gateway.archive_readonly(self.gateway.trace_dir/'self-effect-contexts'/(parent['fingerprint']+'.json'),parent)
                context_refs=(*context_refs,'pet-self-parent:'+parent['fingerprint'])
                if parent['status']=='delivered':parent_text=parent['text']
            from model_packages.desktop_pet_v1.self_effect import SelfEffectCodec
            codec=SelfEffectCodec(codec,parent_text,private_thought=any(c.kind=='thought' for c in event.contents))
        prior_visible=None
        if is_visible:
            from .visible_context import build as visible_context
            prior_visible=visible_context(app,event)
            if prior_visible is not None:
                self.gateway.archive_readonly(self.gateway.trace_dir/'measurement-contexts'/(prior_visible['fingerprint']+'.json'),prior_visible)
                context_refs=(*context_refs,*(x['fact_fingerprint'] for x in prior_visible['sources']),'pet-visible-context:'+prior_visible['fingerprint'])
                if prior_visible['status']=='delivered':
                    from model_packages.desktop_pet_v1.visible_context import CurrentVisibleConversationContextCodec as VisibleConversationContextCodec
                    codec=VisibleConversationContextCodec(codec,prior_visible['text'])
        from model_packages.desktop_pet_v1.relation_package import META
        from petlab_app.participant_input import SPEECH_VERSIONS
        if visible_receipt.get('version') in SPEECH_VERSIONS:
            from petlab_app.visible_delivery import source_context
            from model_packages.desktop_pet_v1.speech_address import SpeechAddressCodec
            codec=SpeechAddressCodec(codec,source_context(app,event))
        if META in app.runtime.session.state.state:
            from model_packages.desktop_pet_v1.participant_semantic import ParticipantSemanticCodec,StructuredParticipantSemanticCodec
            if public_disclosure_enabled(app.runtime):
                from .public_disclosure import counterparty
                codec=StructuredParticipantSemanticCodec(codec,owner_ref=app.owner_ref,human_ref=app.user_ref,counterparty_ref=counterparty(app.runtime,event))
            else:codec=ParticipantSemanticCodec(codec,owner_ref=app.owner_ref,human_ref=app.user_ref)
            from .participant_names import public_names
            names=public_names(app,event)
            if names:
                from model_packages.desktop_pet_v1.participant_semantic import NamedParticipantSemanticCodec
                codec=NamedParticipantSemanticCodec(codec,names=names)
        if literal_parent_mode:
            from model_packages.desktop_pet_v1.visible_context import LiteralParentGroundingCodec
            codec=LiteralParentGroundingCodec(codec)
        from model_packages.desktop_pet_v1.information_holder import enabled as holder_enabled,HolderSemanticCodec,coerce_holder_projection
        holder_mode=holder_enabled(app.runtime)
        if holder_mode:codec=HolderSemanticCodec(codec)
        if tree_mode:
            from model_packages.desktop_pet_v1.query_tree import ResilientTreeQueryCodec
            codec=ResilientTreeQueryCodec(codec)
        if app.runtime.session.profile['desktop_pet_product_v1']['sources'][event.event_id]['origin']=='cx67_private_intention':
            from model_packages.cx_six_seven_v1.intention_semantics import IntentionSemanticCodec
            codec=IntentionSemanticCodec(codec)
        segmented=self.segmented_measurement and address_mode and hierarchy_mode and not has_observations(event.contents) and any(c.kind=='speech' for c in event.contents)
        if segmented:
            from model_packages.desktop_pet_v1.semantic_segments import SegmentCodec
            codec=SegmentCodec(codec)
        locations={}
        if compact:
            from model_packages.desktop_pet_v1.compact_history import CompactHistoryCodec,anchors
            locations=anchors(event)
            codec=CompactHistoryCodec(codec,locations)
        graded=address_mode and hierarchy_mode and not has_observations(event.contents) and getattr(codec,'normalize_query_candidates',False)
        graded_suffix=':shared-address-strength-v1' if graded else ''
        system,user=codec.build_request(event=event,recall_hint=hint)
        from petlab_app.participant_input import NPC_VERSIONS
        from model_packages.desktop_pet_v1 import peer_opening
        measure_speech_use=(visible_receipt.get('version') in NPC_VERSIONS and any(
            c.kind=='speech' and not c.private and event.owner_ref in c.recipient_refs for c in event.contents))
        speech_use_suffix=peer_opening.REQUEST_SUFFIX if measure_speech_use else ''
        if measure_speech_use:
            system,user=peer_opening.request(system,user,event)
        def parse(wire):
            measured_text=completion_text(wire)
            rejected_locations=[]
            if compact:
                from model_packages.desktop_pet_v1.compact_history import restore_properties
                try:
                    measured_text,rejected_locations=restore_properties(measured_text,locations)
                except ValueError:
                    raise ProviderCodecParseFailureSignal(failure_kind='invalid_hierarchical_properties',
                        failure_source_ref=model_data_fingerprint(wire)) from None
            projection=(codec.parse_event_response(measured_text,event=event)
                if hasattr(codec,'parse_event_response') else
                codec.parse_response(measured_text,event_kind=event.event_kind))
            if projection is None:
                raise ProviderCodecParseFailureSignal(failure_kind="invalid_semantic_projection",
                    failure_source_ref=model_data_fingerprint(wire))
            if holder_mode:
                raw=coerce_holder_projection(projection,turn_id=event.event_id,event_kind=event.event_kind).as_record()
            elif getattr(codec,'located_reference_v2',False):
                from model_packages.desktop_pet_v1.semantic import coerce_located_projection
                raw=coerce_located_projection(projection,turn_id=event.event_id,event_kind=event.event_kind).as_record()
            else:raw=coerce_projection(projection,turn_id=event.event_id,event_kind=event.event_kind).as_record()
            raw["source"]="provider-wire:"+model_data_fingerprint(wire)
            actual_model=wire.get("model")
            if not isinstance(actual_model,str) or not actual_model:
                raise ProviderCodecParseFailureSignal(failure_kind="missing_response_model",
                    failure_source_ref=model_data_fingerprint(wire))
            raw["model"]=actual_model
            recall={}
            if getattr(codec,'memory_recall_version',None):
                from model_packages.desktop_pet_v1.grounded_recall import parse_cues,KEY as RECALL_KEY
                if hierarchy_mode:
                    from model_packages.desktop_pet_v1.hierarchy_semantics import parse_response as parse_properties
                    if getattr(codec,'memory_recall_version',None)=='desktop-pet-property-address-recall-v8':
                        from model_packages.desktop_pet_v1.address_semantics import parse_response as parse_properties
                        if getattr(codec,'normalize_query_candidates',False):
                            from model_packages.desktop_pet_v1.address_semantics import parse_available_response as parse_properties
                    try:
                        parsed_cues=parse_properties(measured_text,event)
                        if compact:
                            parsed_cues['rejected_property_locations']=rejected_locations
                        if graded:
                            from model_packages.desktop_pet_v1.address_recall import STRENGTH_VERSION
                            parsed_cues['address_strength_version']=STRENGTH_VERSION
                        if predecessors:
                            from model_packages.desktop_pet_v1.predecessor_reference import parse as parse_predecessors
                            parsed_cues.update(parse_predecessors(completion_text(wire),event,predecessors,public=bool(visible_receipt.get("version"))))
                        if segmented:
                            from model_packages.desktop_pet_v1.semantic_segments import bind as bind_segments
                            parsed_cues.update(bind_segments(completion_text(wire),event,parsed_cues))
                    except ValueError:
                        raise ProviderCodecParseFailureSignal(failure_kind="invalid_hierarchical_properties",
                            failure_source_ref=model_data_fingerprint(wire)) from None
                elif tree_mode:
                    from model_packages.desktop_pet_v1.query_tree import parse_resilient_response
                    try:
                        parsed_cues=parse_resilient_response(completion_text(wire),event,input_context,prior_visible)
                    except ValueError:
                        raise ProviderCodecParseFailureSignal(failure_kind="invalid_memory_query_nodes",
                            failure_source_ref=model_data_fingerprint(wire)) from None
                else:
                    parsed_cues=parse_cues(completion_text(wire),event)
                    if input_context is not None and input_context['status']=='delivered':
                        from model_packages.desktop_pet_v1.reference_window import parse_response as parse_reference_cues
                        if owner_mode:
                            from model_packages.desktop_pet_v1.reference_owners import parse_response as parse_reference_cues
                        parsed_cues.update(parse_reference_cues(completion_text(wire),event,input_context,available=16-len(parsed_cues['cues']),source_scope=codec.reference_source_scope))
                    if owner_mode:
                        from model_packages.desktop_pet_v1.reference_owners import VERSION as OWNER_VERSION
                        parsed_cues['reference_owner_version']=OWNER_VERSION
                    if declared_mode:
                        from model_packages.desktop_pet_v1.declared_forms import parse_declarations,RECALL_VERSION
                        parsed_cues.update(parse_declarations(completion_text(wire),event))
                        if source_bound_mode:
                            from model_packages.desktop_pet_v1.source_bound_recall import VERSION as RECALL_VERSION
                        if owner_mode:
                            from model_packages.desktop_pet_v1.owner_bound_recall import VERSION as RECALL_VERSION
                        parsed_cues['version']=RECALL_VERSION
                recall={RECALL_KEY:parsed_cues}
            speech_use=peer_opening.parse_current(completion_text(wire),event) if measure_speech_use else None
            return {**recall,**({peer_opening.KEY:speech_use} if speech_use is not None else {}),
                **({CALIBRATION_KEY:local_reference} if local_reference is not None else {}),"event_kind":event.event_kind,"committed_measurement":raw,
                "measurement_source":{"version":"cx67-committed-measurement-source-v1",
                    "source_event_id":event.event_id,"local_event_id":event.event_id,
                    "projection_fingerprint":model_data_fingerprint(raw)}}
        from .temporal_measurement import observations,measurement_steps
        if observations(event):
            if retry_failed:
                from petlab_app.provider_reparse import reparse_failed
                from .temporal_measurement import KEY,load,publish
                saved=app.runtime.session.profile.get(KEY,{}).get(event.event_id)
                if saved is not None and saved['status']=='reserved':
                    restored=reparse_failed(self.gateway,session_binding=app.runtime.session.session_binding,
                        current_position=app.runtime.session.position,operation_kind='semantic_measurement',
                        source_fact_ref=event.event_id,codec_contract=codec.request_version,
                        messages=[{'role':'system','content':system},{'role':'user','content':user}],
                        temperature=codec.temperature,require_json=codec.require_json,parse=parse,
                        source_receipt_refs=(event.source_receipt_ref,),invocation_id=saved['invocation_id'])
                    if restored is not None:
                        if restored.status!='parsed':raise RuntimeError('received semantic response still fails local parsing')
                        import json,time
                        trace=json.loads((self.gateway.trace_dir/(saved['invocation_id']+'.json')).read_text('utf8'))
                        data={k:thaw_model_data(v) for k,v in saved.items() if k!='fingerprint'}
                        data['local_parse']={'receipt':restored.parse_receipt.to_record(),
                            'original_parse_fingerprint':trace['codec_parse']['fingerprint'],
                            'completed_at_ms':time.time_ns()//1_000_000}
                        publish(app,event.event_id,data)
                        completed,payload=load(app,self.gateway,event,app.runtime.session.profile[KEY][event.event_id])
                        publish(app,event.event_id,completed)
                        return payload
            return (yield from measurement_steps(app,self.gateway,event,codec_contract=codec.request_version,
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                temperature=codec.temperature,require_json=codec.require_json,parse=parse,retry_failed=retry_failed))
        run=self.gateway.prepare
        if retry_failed:
            from petlab_app.provider_reparse import reparse_failed
            def run(**kwargs):
                restored=reparse_failed(self.gateway,**kwargs)
                return restored if restored is not None else self.gateway.prepare(**kwargs)
        result=run(session_binding=app.runtime.session.session_binding,
            current_position=app.runtime.session.position,operation_kind="semantic_measurement",
            source_fact_ref=event.event_id,codec_contract=codec.request_version+graded_suffix+speech_use_suffix,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=codec.temperature,require_json=codec.require_json,parse=parse,
            source_receipt_refs=tuple(dict.fromkeys((event.source_receipt_ref, *context_refs))))
        if hasattr(result,"transport"):
            result=yield result
        if result.status!="parsed":
            error=(result.execution.failure.failure_kind if result.execution.failure is not None
                else result.parse_receipt.failure_kind)
            raise RuntimeError("semantic provider failed: "+error)
        payload=thaw_model_data(result.parsed_payload)
        if rag is not None:rag.prefetch(event,measurement=payload)
        return payload


class TreePetSemanticService(PetSemanticService):
    """Current product speech contract; old service and sealed data stay intact."""
    query_tree_enabled=True
