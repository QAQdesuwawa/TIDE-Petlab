"""Original Writer packet, neutral Realizer host, and one provider gateway."""
from __future__ import annotations
from petlab_app.destinations import destination as participant_destination, audience as participant_audience
from model_packages.desktop_pet_v1.relation_package import view as root_view

import json
from pathlib import Path
import uuid
import hashlib
from dataclasses import dataclass

from framework.model_api import model_data_fingerprint,thaw_model_data,freeze_model_payload
from framework.action_protocol import ActionAuthorization
from framework.provider_codec import ProviderCodecParseFailureSignal
from framework.realizer_host import execute_realizer,RealizerInvocationFailure
from framework.realizer_protocol import RealizerRequest,RealizerAttempt,RealizerCandidate
from .writer_archive import PetWriterAttemptJournal
from product_adapters.cx_six_seven_v1.realizer_packet import restore_writer_packet
from model_packages.cx_six_seven_v1.realizer_packet import WriterCard,WriterOutputIdentity
from model_packages.cx_six_seven_v1.variant_realizer_delivery import assemble_variant_writer_packet
from model_packages.desktop_pet_v1.events import Speech,Thought
from model_packages.desktop_pet_v1.interaction_delivery import has_observations,has_body_targets
from model_packages.desktop_pet_v1 import writer as codec
from model_packages.desktop_pet_v1.presentation import build_guidance
from model_packages.desktop_pet_v1.expression_profile import validate_expression_profile,natural_expression_policy
from petlab_app.providers import completion_text,_save_trace
from petlab_app.composition import DEFINITION_KEY,PetDefinition
from .expression import PetExpressionService
from .execution import PreparedExecution,ExecutionUncertain
from .emission import prepare_delivery
from .memory_delivery import memory_cards
from .prompt_delivery import compile_delivery
from model_packages.desktop_pet_v1.content_view import DEFAULT_VERSION as CONTENT_VIEW_VERSION, content_view


_CURRENT_VISUAL=object()
_INLINE_SEALED="desktop-pet-writer-sealed-v2-inline-archive"


@dataclass(frozen=True)
class _LivePrepared:
    prepared: object
    source_id: str
    artifact_bytes: bytes
    definition_fingerprint: str
    binding: object
    position: object
    source_fingerprint: str
    record: object
    trace_path: Path
    fingerprint: str
    state: object
    parameters: object


class PetRealizationService:
    def _idle(self,event_id):
        from petlab_app.idle_opening import is_idle_tick
        return is_idle_tick(self.application,event_id)

    def _source_fingerprint(self,event_id):
        if self._idle(event_id):
            from petlab_app.idle_opening import source
            return source(self.application,event_id)[0].row['fingerprint']
        return self.application.runtime.memory_instance(event_id).fact.fingerprint

    def _version(self,event_id):
        if self._idle(event_id):return codec.OPTIONAL_IDLE_VERSION
        contents=self.application.runtime.memory_instance(event_id).event.contents
        return codec.SUMMARY_VERSION if has_observations(contents) else codec.BODY_VERSION if has_body_targets(contents) else codec.VERSION

    def __init__(self,*,application,gateway,temperature,numbered_memory=False,memory_links=False,budget_chars=None,expression_profile=None,expression_codes=False,visual_capabilities=None,private_thought=False):
        if not isinstance(expression_codes,bool):raise ValueError("expression_codes must be boolean")
        if not isinstance(private_thought,bool):raise ValueError("private_thought must be boolean")
        self.private_thought=private_thought
        self.expression_codes=expression_codes
        from model_packages.desktop_pet_v1.visual_capabilities import validate
        self.visual_capabilities=validate(visual_capabilities) if expression_codes else None
        self.expression_profile=validate_expression_profile(expression_profile)
        if not isinstance(numbered_memory,bool) or not isinstance(memory_links,bool) or (memory_links and not numbered_memory):
            raise ValueError("memory links require explicitly numbered memory")
        if budget_chars is not None and (isinstance(budget_chars,bool) or not isinstance(budget_chars,int) or budget_chars<=0):
            raise ValueError("writer budget must be a positive integer")
        self.numbered_memory=numbered_memory
        self.memory_links=memory_links
        self.budget_chars=budget_chars
        self.application=application
        self.gateway=gateway
        self.temperature=temperature
        from petlab_app.readonly_archive import ReadOnlyArchives
        self._readonly_archives=ReadOnlyArchives()
        self.attempt_journal=PetWriterAttemptJournal(gateway.trace_dir/application.runtime.session.session_binding.fingerprint)
        self._attempt_source_headers={};self._legacy_headers={}
        # Validate old headers once while opening; new interactions reuse by actual bytes.
        with self.attempt_journal.opening_receipts() as receipts:
            for row in receipts:
                if row['stage']=='allocated':self._attempt_source(row)
        self._open_legacy_headers()
        # A source admitted after this Writer opened cannot have an older
        # untracked paid attempt. All attempts produced here use the journal.
        # Existing/recovered sources keep the original legacy reconciliation.
        self._legacy_source_frontier=(application.runtime.session.session_binding.fingerprint,
            frozenset(application._product()['sources']),
            frozenset(application.runtime.session.profile.get('desktop_pet_idle_opening_v1',{}).get('ticks',{})))


    def _attempt_source(self,row):
        identity=row['writer_request_id']
        raw=self.attempt_journal._readers.read(self.attempt_journal._artifact_path(identity))
        digest=hashlib.sha256(raw).digest()
        cache=getattr(self,'_attempt_source_headers',{})
        cached=cache.get(identity)
        if cached is not None and cached[0]==digest:return cached[1]
        artifact=self.attempt_journal.read_attempt_bytes(identity,raw)
        source=artifact.get('source_evidence',{}).get('event_id',row['parent_event_id'])
        if len(cache)>=4096:cache.pop(next(iter(cache)))
        cache[identity]=(digest,source);self._attempt_source_headers=cache
        return source

    def _open_legacy_headers(self):
        # Only independent byte reads overlap. Keep parsing/cache writes ordered,
        # bound outstanding buffers, and propagate every read/JSON failure.
        from concurrent.futures import ThreadPoolExecutor
        from itertools import islice
        paths=iter(self.gateway.trace_dir.glob('*.json'))
        batch=tuple(islice(paths,8))
        if not batch:return
        with ThreadPoolExecutor(max_workers=4,thread_name_prefix='pet-trace-read') as pool:
            while batch:
                for path,raw in zip(batch,pool.map(Path.read_bytes,batch)):
                    self._legacy_header(path,raw=raw)
                batch=tuple(islice(paths,8))

    def _legacy_header(self,path,*,raw=None):
        if raw is None:raw=path.read_bytes()
        digest=hashlib.sha256(raw).digest()
        cache=getattr(self,'_legacy_headers',{});cached=cache.get(path)
        if cached is not None and cached[0]==digest:return cached[1]
        value=json.loads(raw)
        if value.get('version')=='petlab-provider-trace-v1':
            provider=value.get('provider_request',{})
            header={'version':value['version'],'source_fact_ref':value.get('source_fact_ref'),
                'operation_kind':value.get('operation_kind',provider.get('operation_kind')),
                'provider_request':{'session_binding_fingerprint':provider.get('session_binding_fingerprint')},
                'status':value.get('status')}
        else:header={'version':value.get('version')}
        if len(cache)>=4096:cache.pop(next(iter(cache)))
        cache[path]=(digest,header);self._legacy_headers=cache
        return header

    def _restore_work(self,event_id,*,position=None,only_request_id=None,ignore_retired=False):
        """Reuse common sealed artifacts; never infer a missing response was uncalled."""
        pending=[]
        expected_parent=None
        app=getattr(self,'application',None)
        if app is not None:
            from . import deferred_reply
            if self._idle(event_id) or not deferred_reply.needed(app,event_id):expected_parent=event_id
        receipts=(self.attempt_journal.read_parent_receipts(expected_parent)
            if expected_parent is not None else self.attempt_journal.read_receipts())
        completed={row['writer_request_id'] for row in receipts
            if row['stage']=='commit' or (row['stage']=='terminal' and row.get('terminal_status')=='failed')}
        for row in receipts:
            if row['stage']!='allocated' or row['writer_request_id'] in completed:continue
            if expected_parent is not None and row['parent_event_id']!=expected_parent:continue
            if only_request_id is not None and row['writer_request_id']!=only_request_id:continue
            if self._attempt_source(row)!=event_id:continue
            artifact=self.attempt_journal.read_attempt(row['writer_request_id'])
            if 'commit' in artifact or artifact.get('terminal',{}).get('status')=='failed':continue
            if not ignore_retired:
                from .retired_writer import is_retired
                if is_retired(self,artifact):continue
            pending.append(artifact)
        if not pending:
            frontier=getattr(self,'_legacy_source_frontier',None)
            if (frontier is not None
                    and frontier[0]==self.application.runtime.session.session_binding.fingerprint
                    and event_id not in frontier[1] and event_id not in frontier[2]
                    and (event_id in self.application.runtime.session.profile.get('desktop_pet_product_v1',{}).get('sources',{})
                         or self._idle(event_id))):
                return None
            # Legacy traces did not declare dispatch tracking. Missing response
            # cannot prove that a paid Writer call never entered the network.
            for path in self.gateway.trace_dir.glob('*.json'):
                legacy=self._legacy_header(path)
                if legacy.get('version')!='petlab-provider-trace-v1' or legacy.get('source_fact_ref')!=event_id:continue
                provider=legacy.get('provider_request',{})
                if legacy.get('operation_kind',provider.get('operation_kind'))!='writer':continue
                binding=provider.get('session_binding_fingerprint')
                if binding is not None and binding!=self.application.runtime.session.session_binding.fingerprint:continue
                if legacy.get('status') in {'prepared','started','received'}:
                    raise ExecutionUncertain('legacy pet Writer lacks a sealed response; reconcile before retry')
            return None
        if len(pending)!=1:raise ExecutionUncertain('multiple pet Writer attempts require reconciliation')
        artifact=pending[0]
        sealed=artifact.get('source_evidence',{})
        app=self.application
        position=app.runtime.session.position if position is None else position
        if (artifact.get('provider_dispatch_tracked') is not True or sealed.get('version') not in {'desktop-pet-writer-sealed-v1',_INLINE_SEALED}
                or sealed.get('event_id')!=event_id or sealed.get('position')!=position.to_record()
                or sealed.get('definition_fingerprint')!=app.definition.fingerprint
                or sealed.get('source_fact_fingerprint')!=self._source_fingerprint(event_id)):
            raise ValueError('pet Writer sealed source differs from current formal source')
        request_record=sealed['request']
        if request_record['readonly_facts'].get('memory_display_config')!={'numbered':self.numbered_memory,
                'links':self.memory_links,'budget_chars':self.budget_chars}:
            raise ValueError('writer candidate differs from its frozen display configuration')
        request=RealizerRequest(**{**request_record,'control':freeze_model_payload(request_record['control']),
            'readonly_facts':freeze_model_payload(request_record['readonly_facts'])})
        auth_record=sealed['authorization']
        authorization=ActionAuthorization(**{**auth_record,
            'package_eligibility_evidence':freeze_model_payload(auth_record['package_eligibility_evidence']),
            'host_authorization_evidence':freeze_model_payload(auth_record['host_authorization_evidence'])})
        authorization.verify_integrity(session_binding=app.runtime.session.session_binding,current_position=position)
        request.verify_integrity(session_binding=app.runtime.session.session_binding,current_position=position,authorization=authorization)
        rag=request.readonly_facts.get('rag_context')
        if rag is not None:
            service=getattr(app,'rag_memory',None)
            if service is None:raise ValueError('sealed RAG context lacks source verifier')
            service.validate(rag,event_id)
        packet=restore_writer_packet(artifact['packet'],expected_temporal_position=position.position)
        if (packet.writer_request_id!=artifact['writer_request_id'] or packet.event_id!=authorization.host_authorization_evidence.get('planning_event_ref',event_id)
                or packet.to_record()!=thaw_model_data(request.readonly_facts['writer_packet'])):
            raise ValueError('pet Writer sealed packet differs from request')
        delivery=request.readonly_facts['prompt_delivery']
        expected_prompt={'system':delivery['system'],'user':delivery['user'],'prompt_fingerprint':delivery['prompt_fingerprint']}
        if artifact.get('prepared_prompt')!=expected_prompt or artifact.get('prompt',expected_prompt)!=expected_prompt:
            raise ValueError('pet Writer archived prompt differs from sealed request')
        trace=Path(sealed['realizer_path']).resolve()
        if trace.parent!=self.gateway.trace_dir or not trace.name.startswith('realizer-'):
            raise ValueError('pet Writer sealed trace is outside its bound directory')
        if 'provider' in artifact:
            record=artifact['provider'].get('provider_result',{}).get('realizer_trace')
            if not isinstance(record,dict) or record.get('status')!='candidate_ready':
                raise ExecutionUncertain('pet Writer response requires reconciliation')
            original=dict(record);fingerprint=original.pop('fingerprint',None)
            if model_data_fingerprint(original)!=fingerprint or record['request']!=request.to_record():
                raise ValueError('pet Writer archived result differs from sealed request')
            attempt=RealizerAttempt(**{**record['attempt'],'invocation_evidence':freeze_model_payload(record['attempt']['invocation_evidence'])})
            candidate=RealizerCandidate(**{**record['candidate'],
                'candidate_payload':freeze_model_payload(record['candidate']['candidate_payload']),
                'realization_evidence':freeze_model_payload(record['candidate']['realization_evidence'])})
            context=request.readonly_facts.get('reading_context')
            if context is not None and context.get('result') is not None:
                app._reading_context_offered=(event_id,context['result']['event_id'])
            from .quiet_realization import quiet_candidate
            quiet=quiet_candidate(request,candidate)
            if quiet is not None:
                self._verify_readonly_trace(trace,record,inline=sealed['version']==_INLINE_SEALED)
                return quiet
            prepared=PreparedExecution(position=position,scope_ref=sealed['scope_ref'],
                authorization=authorization,request=request,attempt=attempt,candidate=candidate)
            prepared.validate(binding=app.runtime.session.session_binding,owner_ref=app.owner_ref,user_ref=prepared.authorization.target_ref)
            self._verify_readonly_trace(trace,record,inline=sealed['version']==_INLINE_SEALED)
            if 'terminal' not in artifact:
                self.attempt_journal.record_terminal(artifact['writer_request_id'],status='candidate_ready')
            return prepared
        if 'provider_dispatch' in artifact:
            raise ExecutionUncertain('pet Writer was dispatched without an archived response; do not repeat it')
        if (sealed.get('transport_binding')!=self.gateway.transport_for("writer").binding
                or sealed.get('writer_temperature')!=self.temperature):
            raise ValueError('prepared pet Writer provider binding differs; explicit reconciliation required')
        if 'prompt' not in artifact:self.attempt_journal.record_prompt(artifact['writer_request_id'],**expected_prompt)
        return artifact['writer_request_id'],authorization,request,trace

    def _verify_readonly_trace(self,path,record,*,inline):
        if path.exists():
            if json.loads(path.read_text(encoding='utf8'))!=thaw_model_data(record):
                raise ValueError('pet Writer candidate differs from actual realizer trace')
        elif inline:
            self._readonly_archives.defer(path,record)
        else:
            # Legacy v1 requires its original independent durable file.
            raise ValueError('pet Writer actual realizer trace is missing')

    def publish_readonly_archives(self,*,wait=False):
        self._readonly_archives.publish(wait=wait)

    def close(self):
        self._live_prepared=None
        try:self._readonly_archives.close()
        finally:self.attempt_journal.close()

    def packet(self,event_id,*,writer_request_id,writer_version=None,visual_capabilities=_CURRENT_VISUAL,content_view_version=CONTENT_VIEW_VERSION,rag_context=None,relation_version=_CURRENT_VISUAL,visibility=None,_expression_out=None,conversation_context=False):
        if relation_version is _CURRENT_VISUAL:
            from .memory_relations import VERSION as RELATION_VERSION
            relation_version=RELATION_VERSION if self.memory_links else None
        writer_version=self._version(event_id) if writer_version is None else writer_version
        capabilities=self.visual_capabilities if visual_capabilities is _CURRENT_VISUAL else visual_capabilities
        if writer_version in {codec.IDLE_VERSION,codec.IDLE_DISPLAY_VERSION,codec.OPTIONAL_IDLE_VERSION}:return self._idle_packet(event_id,writer_request_id,writer_version=writer_version,visual_capabilities=capabilities,content_view_version=content_view_version,relation_version=relation_version,visibility=visibility)[:2]
        if writer_version not in {codec.VERSION,codec.LEGACY_VERSION,codec.CONTINUOUS_VERSION,codec.BODY_VERSION,codec.SUMMARY_VERSION}:raise ValueError("unsupported pet writer version")
        app=self.application
        with app._lock:
            expressions=PetExpressionService(application=app)
            from . import deferred_reply
            deferred=deferred_reply.needed(app,event_id)
            lifecycle,required_authorization=expressions.project_for_reply(event_id,historical=deferred)
            if deferred:
                authorization=deferred_reply.authorize(app,event_id)
                from .settlement_view import current_projection
                expression=current_projection(app)
            elif lifecycle.evidence.response_policy=="eligible":
                from petlab_app.peer_reaction import is_peer,authorize as authorize_peer
                if is_peer(app,event_id):authorization=authorize_peer(app,event_id)
                else:
                    from petlab_app.interaction_observer import authorize_interaction_tick
                    authorization=authorize_interaction_tick(app,event_id)
            else:authorization=required_authorization
            if not deferred:expression=lifecycle.expression
            source=app.runtime.memory_instance(event_id)
            history=app.runtime._history()
            recalls=tuple(item for item in history.actual_recalls() if item.trigger_fact_id==event_id)
            if len(recalls)!=1:raise ValueError("writer requires exact formal recall")
            recall=recalls[0]
            read=history.authorize_read(reader_ref=app.owner_ref,trigger_fact_id=event_id,
                before_runtime_ordinal=source.fact.accepted_runtime_ordinal,requested_fact_ids=recall.recalled_fact_ids,
                authorization_ref={"actual_recall_fingerprint":recall.fingerprint,
                    "lifecycle_receipt_fingerprint":lifecycle.receipt["fingerprint"]})
            memories=app.runtime.read_memory(read,event_ids=recall.recalled_fact_ids)
            if visibility is not None:
                from petlab_app.writer_visibility import filter_memories
                from types import SimpleNamespace
                memories=filter_memories(app,memories,visibility,lane='numeric')
                # This is only the delivery view; History's actual recall and
                # its already committed state contribution remain untouched.
                recall=SimpleNamespace(recalled_fact_ids=tuple(m.event.event_id for m in memories),fingerprint=recall.fingerprint)

            role=PetDefinition.from_record(app.runtime.session.profile[DEFINITION_KEY])
            if role.fingerprint!=app.definition.fingerprint:
                raise ValueError("writer definition differs from persisted role")
            persona_background=role.background if self.expression_profile is None else self.expression_profile['description']
            persona_refs=(role.fingerprint,) if self.expression_profile is None else (
                role.fingerprint,model_data_fingerprint(self.expression_profile),self.expression_profile['source_ref'])
            def card(lane,kind,source_id,fields,refs,*,preserve=False,provenance=None):
                return WriterCard(card_id=lane+":"+source_id,lane=lane,kind=kind,source_id=source_id,
                    fields=fields,source_receipt_refs=refs,provenance=provenance,budget_policy="preserve_selection" if preserve else "normal")
            form_fields={"allowed_content_kinds":["speech"],"selected_action_keys":[],
                "output_fields":["speech","reported_used_memory_ids"]}
            if self.expression_codes and writer_version!=codec.LEGACY_VERSION:
                from model_packages.desktop_pet_v1.face_expression import VERSION,CODES,LABELS
                from model_packages.desktop_pet_v1.visual_capabilities import presentation_contract
                form_fields["presentation_contract"]=presentation_contract(capabilities)
                form_fields["output_fields"].append("expression")
            if self.private_thought:
                form_fields['allowed_content_kinds'].append('thought')
                form_fields['output_fields'].append('thought')
                form_fields['private_expression_version']=codec.PRIVATE_EXPRESSION_VERSION
            source_fields={"说话者":source.event.actor_ref,"对象":list(source.event.target_refs),"时间":source.event.position.coordinate,
                "正文":"\n".join(item.as_text() for item in source.event.visible_contents(app.owner_ref)),
                "内容属性":codec.content_attributes(source.event.visible_contents(app.owner_ref),compact=writer_version in {codec.CONTINUOUS_VERSION,codec.BODY_VERSION},natural=writer_version==codec.SUMMARY_VERSION)}
            if writer_version==codec.SUMMARY_VERSION:
                attrs=source_fields['内容属性']
                source_fields['正文'],source_fields['内容属性']=content_view(
                    source.event.visible_contents(app.owner_ref),attrs,version=content_view_version)
            from model_packages.desktop_pet_v1.content_view import RELATION_MEMORY_VERSION,QUERY_MEMORY_VERSION,HIERARCHY_MEMORY_VERSION
            if content_view_version in (RELATION_MEMORY_VERSION,QUERY_MEMORY_VERSION):
                from .relation_annotations import for_memory,FIELD
                annotation=for_memory(app.runtime.session.profile,source,app.owner_ref)
                if annotation is not None:source_fields[FIELD]=annotation
            if content_view_version==QUERY_MEMORY_VERSION:
                from .relation_annotations import query_references
                query=query_references(app.runtime.session.profile,source,memories,app.owner_ref)
                if query:source_fields["本轮指代参照"]=query
            if content_view_version==HIERARCHY_MEMORY_VERSION:
                from .relation_annotations import _measurement
                source_measurement=_measurement(app.runtime.session.profile,source).get('pet_memory_recall',{})
            else:source_measurement={}
            if source_measurement.get('version')=='desktop-pet-property-address-recall-v8':
                from model_packages.desktop_pet_v1.hierarchy_semantics import validate
                source_fields['语义属性']=[{k:thaw_model_data(v) for k,v in p.items() if k!='span'} for p in validate(source.event,source_measurement)]
            from petlab_app.visible_delivery import presentation_fields
            source_fields=presentation_fields(app,source.event,source_fields)
            source_refs=(source.event.source_binding_ref,source.fact.fingerprint)
            from .occurrence_delivery import source_chronology
            chronology,chronology_refs=source_chronology(app,source.event)
            if chronology is not None:
                source_fields['事实时序']=chronology
                source_refs=(*source_refs,*chronology_refs)
            if writer_version!=codec.LEGACY_VERSION:
                # project() verified this original source's accepted continuation.
                # A deferred reply uses later state but keeps this measurement.
                from .measurement_delivery import source_measurement
                measurement_fields,measurement_refs=source_measurement(app,lifecycle,event_id)
                from model_packages.desktop_pet_v1.content_view import CLEAN_VERSION, PUBLIC_VERSION, DECLARED_MEMORY_VERSION, HIERARCHY_MEMORY_VERSION
                if content_view_version in {CLEAN_VERSION, PUBLIC_VERSION, DECLARED_MEMORY_VERSION,HIERARCHY_MEMORY_VERSION}:
                    measurement_fields={key:value for key,value in measurement_fields.items()
                        if key in {'status','variable_deltas','confidence','axis_presence'}}
                    if content_view_version in {PUBLIC_VERSION,DECLARED_MEMORY_VERSION,HIERARCHY_MEMORY_VERSION}:
                        measurement_fields.pop('axis_presence',None)
                source_fields.update(reply_target=True,本轮量测=measurement_fields)
                source_refs=(*source_refs,*measurement_refs)
                form_fields["natural_expression_policy"]=natural_expression_policy(selected_memory_count=len(memories))
            if deferred:
                form_fields['deferred_reply_reference']='回答current_input中的原问题；它尚未得到回复。按当前已结算状态与已选表达资源回应，不把原问题说成刚发生，不虚构等待期间的新对话。'
            if writer_version!=codec.LEGACY_VERSION:
                form_fields["subtype_realization_guidance"]=build_guidance(
                    source_contents=source.event.visible_contents(app.owner_ref),selected_output_kinds=("speech",),
                    resource_path=(Path(__file__).resolve().parents[2]/"model_packages/desktop_pet_v1/resources/presentation-v4-visible.json"
                        if app._product()["sources"][event_id].get("evidence",{}).get("input_source",{}).get("version")=="pet-participant-delivery-input-v3-visible"
                        else Path(__file__).resolve().parents[2]/"model_packages/desktop_pet_v1/resources/presentation-v3-text.json" if writer_version==codec.SUMMARY_VERSION else None))
            if writer_version in {codec.CONTINUOUS_VERSION,codec.BODY_VERSION,codec.SUMMARY_VERSION}:
                form_fields["interaction_expression_reference"]="依据已选方向自然回应这段实际交互；ongoing为仍在拖拽/按住，ended为已松开。像当前人设说话，不向对方播报屏幕坐标、样本索引或后端毫秒参数；不把累计时间当重复事件，不预设必须不满或开心。"
            if writer_version==codec.SUMMARY_VERSION:
                form_fields['interaction_expression_reference']='按实际交互摘要与操作含义回应，不把本段与累计经历重复计算；感受沿已选方向与人物自然表达，不套固定台词。'
            if writer_version==codec.BODY_VERSION:
                form_fields["body_interaction_reference"]="身体部位参照：按body_target中实际起始接触的部位和已给含义自然回应，不播报几何/来源元数据；点按或保持不是擅自升级为疼痛、揉捏或拉扯。感受沿已选方向/状态与实际记忆表达，不按部位固定台词或情绪；素材反馈不是Action选择。"
            lanes={"current_input":[card("current_input","committed_pet_input",event_id,
                source_fields,source_refs)],
                "persona":[card("persona","pet_definition",role.owner_ref,
                    {"name":role.name,"background":persona_background},persona_refs)],
                "conversation":[card("conversation","accepted_state",event_id,
                    {"已结算状态":root_view(app.runtime.session.state.state),"时间":app.runtime.session.position.position.coordinate},
                    (source.fact.fingerprint,authorization.host_authorization_evidence['planning_view_fingerprint']) if deferred else (source.fact.fingerprint,))],
                "memory":memory_cards(memories,recall=recall,read=read,history=history,owner_ref=app.owner_ref,
                    numbered=self.numbered_memory,links=self.memory_links,bounded=writer_version!=codec.LEGACY_VERSION,natural=writer_version in {codec.SUMMARY_VERSION,codec.VERSION},content_view_version=content_view_version,annotation_profile=app.runtime.session.profile,application=app),
                "form":[card("form","speech_output_contract",writer_version,form_fields,(writer_version,))]}
            if conversation_context:
                from .conversation_delivery import card_for
                context_card=card_for(app,source.event,readers=participant_audience(app,event_id),typed=conversation_context=='typed')
                if context_card is not None:lanes['conversation'].append(context_card)
            if rag_context is not None:
                from .rag_memory import RagMemoryService
                service=getattr(app,'rag_memory',None)
                if service is None:raise ValueError('RAG content requires its original source verifier')
                lanes['memory']=service.compose_cards(rag_context,event_id,lanes['memory'],
                    content_view_version=content_view_version,links=self.memory_links,visibility=visibility)
                if 'query_basis' in rag_context and content_view_version==QUERY_MEMORY_VERSION:
                    # The source may have content eligibility through RAG alone.
                    # Bind annotations to the final delivered aliases, not numeric positions.
                    _,_,available=service.validate(rag_context,event_id)
                    marker_map={i:row['记忆标记'] for c in lanes['memory']
                        for i,row in zip(c.provenance['selected_memory_ids'],c.fields.get('相关记忆（按发生顺序）',(c.fields,)))}
                    annotations=query_references(app.runtime.session.profile,source,
                        tuple(available[i] for i in marker_map),app.owner_ref,markers=marker_map)
                    from dataclasses import replace
                    fields=dict(lanes['current_input'][0].fields);fields.pop('本轮指代参照',None)
                    if annotations:fields['本轮指代参照']=annotations
                    lanes['current_input']=[replace(lanes['current_input'][0],fields=fields)]
            from .memory_relations import attach
            lanes['memory']=attach(lanes['memory'],application=app,event_id=event_id,version=relation_version)
            packet=assemble_variant_writer_packet(session_binding=app.runtime.session.session_binding,
                context=expression.context,writer_request_id=writer_request_id,
                output_identity=WriterOutputIdentity(speaker_participant_id=app.owner_ref,speaker_kind="npc",
                    audience_participant_ids=participant_audience(app,event_id),addressed_participant_ids=tuple((participant_destination(app,event_id) or {}).get('addressed',(app.user_ref,)))),
                expression_resources=expression.batch,cards_by_lane=lanes)
            if _expression_out is not None:_expression_out.append(expression)
            return packet,authorization

    def _idle_packet(self,event_id,writer_request_id,*,writer_version=codec.IDLE_DISPLAY_VERSION,visual_capabilities=_CURRENT_VISUAL,content_view_version=CONTENT_VIEW_VERSION,composite_context_version=None,relation_version=_CURRENT_VISUAL,visibility=None):
        if relation_version is _CURRENT_VISUAL:
            from .memory_relations import VERSION as RELATION_VERSION
            relation_version=RELATION_VERSION if self.memory_links else None
        capabilities=self.visual_capabilities if visual_capabilities is _CURRENT_VISUAL else visual_capabilities
        from types import SimpleNamespace
        from petlab_app import idle_opening as idle
        app=self.application
        authorization=idle.authorize(app,event_id)
        role=PetDefinition.from_record(app.runtime.session.profile[DEFINITION_KEY])
        selection,memories=idle.recalled_memories(app,event_id)
        from petlab_app.idle_targets import VERSION as TARGET_VERSION,visible_memories
        target=participant_destination(app,event_id)
        excluded=[]
        if visibility is not None:
            from petlab_app.writer_visibility import filter_memories
            memories=filter_memories(app,memories,visibility,lane='numeric')
            excluded=[x['event_id'] for x in visibility['excluded']]
        elif target is not None and target.get('addressing_policy')==TARGET_VERSION:
            memories,excluded=visible_memories(app,target,memories)
        recall=SimpleNamespace(recalled_fact_ids=tuple(m.event.event_id for m in memories),fingerprint=selection.receipt['fingerprint'])
        def card(lane,kind,key,fields,refs):
            return WriterCard(card_id=lane+':'+key,lane=lane,kind=kind,source_id=key,fields=fields,source_receipt_refs=refs)
        persona_refs=(role.fingerprint,) if self.expression_profile is None else (role.fingerprint,model_data_fingerprint(self.expression_profile),self.expression_profile['source_ref'])
        form={'allowed_content_kinds':['speech'],'selected_action_keys':[],
            'output_fields':['speech','reported_used_memory_ids'],
            'idle_opening_reference':'本轮没有用户新输入。原状态与动态Tick已许可主动话语，依已选方向/资源、角色设定和实际交付记忆自然搭话。不虚构刚收到消息、鼠标触摸或已完成动作，不介绍产品功能，不许诺记忆能力。'}
        if self.expression_codes and writer_version in {codec.IDLE_DISPLAY_VERSION,codec.OPTIONAL_IDLE_VERSION}:
            from model_packages.desktop_pet_v1.face_expression import VERSION,LABELS
            from model_packages.desktop_pet_v1.visual_capabilities import presentation_contract
            form['presentation_contract']=presentation_contract(capabilities)
            form['output_fields'].append('expression')
        if self.private_thought:
            form['allowed_content_kinds'].append('thought')
            form['output_fields'].append('thought')
            form['private_expression_version']=codec.PRIVATE_EXPRESSION_VERSION
        lanes={'current_input':[],
            'persona':[card('persona','pet_definition',role.owner_ref,{'name':role.name,
                'background':role.background if self.expression_profile is None else self.expression_profile['description']},persona_refs)],
            'conversation':[card('conversation','accepted_state',event_id,{'已结算状态':root_view(app.runtime.session.state.state),
                '时间':app.runtime.session.position.position.coordinate},(authorization.source_settlement_fingerprint,))],
            'memory':memory_cards(memories,recall=recall,read=selection.history_read,history=selection.history_source,
                owner_ref=app.owner_ref,numbered=self.numbered_memory,links=self.memory_links,natural=writer_version in {codec.IDLE_DISPLAY_VERSION,codec.OPTIONAL_IDLE_VERSION},content_view_version=content_view_version,annotation_profile=app.runtime.session.profile,application=app),
            'form':[card('form','speech_output_contract',writer_version,form,(writer_version,selection.receipt['fingerprint']))]}
        if composite_context_version is not None:
            if composite_context_version!='desktop-pet-selected-plan-v1':raise ValueError('unknown composite context')
            from model_packages.desktop_pet_v1.behavior import staged_selection,content
            tick,_=idle.source(app,event_id,current=True)
            if not staged_selection(tick.row.get('behavior_selection')):
                raise ValueError('composite context differs from selected behavior')
            if 'meal' not in (tick.row['behavior_selection'].get('definition') or {}):lanes['form'].append(card('form','selected_parent_plan',event_id,
                {'本轮已选行动计划':content(app.user_ref,selection=tick.row['behavior_selection']).description,
                 '计划含义':('这是同一次行动中由包选定的行动，话语与它配合表达。实际完成或中断由播放回执确认。'
                    if tick.row['behavior_selection'].get('catalog_version')=='desktop-pet-spatial-action-catalog-v1'
                    else '这是同一次行动中由包选定的自身活动，话语与它配合表达。实际完成或中断由播放回执确认。')},
                (tick.row['fingerprint'],)))
        if target is not None and target.get('addressing_policy')==TARGET_VERSION:
            from dataclasses import replace
            lanes['conversation'][0]=replace(lanes['conversation'][0],provenance={
                'visibility_delivery_version':TARGET_VERSION,
                'formal_recalled_ids':list(selection.recall.recalled_event_ids),
                'delivered_memory_ids':list(recall.recalled_fact_ids),
                'withheld_private_ids':excluded})
            lanes['conversation'].append(card('conversation','selected_relationship',event_id+':recipient',
                {'交谈对象':target['primary'],'会话可见成员':target['conversation']['active_member_ids']},(target['fingerprint'],)))
        from .memory_relations import attach
        lanes['memory']=attach(lanes['memory'],application=app,event_id=event_id,version=relation_version)
        shadow=idle.projection(app,event_id,writer_request_id=writer_request_id,cards_by_lane=lanes)
        expression=shadow.destination_expression
        packet=assemble_variant_writer_packet(session_binding=app.runtime.session.session_binding,context=expression.context,
            writer_request_id=writer_request_id,output_identity=shadow.packet_shadow.packet.output_identity,
            expression_resources=expression.batch,cards_by_lane=lanes)
        return packet,authorization,expression

    def _request(self,event_id,*,writer_request_id,writer_version=None,visual_capabilities=_CURRENT_VISUAL,content_view_version=_CURRENT_VISUAL,composite_context_version=_CURRENT_VISUAL,prompt_delivery_version=None,rag_context=_CURRENT_VISUAL,relation_version=_CURRENT_VISUAL,visibility_version=_CURRENT_VISUAL,scene_context=_CURRENT_VISUAL,reading_context=_CURRENT_VISUAL,body_context=_CURRENT_VISUAL,meal_scene_context=_CURRENT_VISUAL,body_delivery_version=_CURRENT_VISUAL,action_sequence_context=None):
        if content_view_version is _CURRENT_VISUAL:
            from model_packages.desktop_pet_v1.content_view import DECLARED_MEMORY_VERSION,HIERARCHY_MEMORY_VERSION,RELATION_MEMORY_VERSION,QUERY_MEMORY_VERSION
            content_view_version=(QUERY_MEMORY_VERSION if getattr(self.application,'memory_recall_version',None)=='desktop-pet-grounded-retention-recall-v2' else HIERARCHY_MEMORY_VERSION if getattr(self.application,'memory_recall_version',None) in ('desktop-pet-hierarchical-property-recall-v7','desktop-pet-property-address-recall-v8') else DECLARED_MEMORY_VERSION if getattr(self.application,'memory_recall_version',None) in ('desktop-pet-declared-form-recall-v4','desktop-pet-source-bound-reference-recall-v5','desktop-pet-owner-bound-reference-recall-v6','desktop-pet-hierarchical-property-recall-v7') else CONTENT_VIEW_VERSION)
        marker_default=writer_version is None and bool(getattr(self.application,'memory_recall_version',None))
        writer_version=self._version(event_id) if writer_version is None else writer_version
        marked=marker_default or writer_version.endswith(codec.MEMORY_MARKER_SUFFIX)
        if writer_version.endswith(codec.MEMORY_MARKER_SUFFIX):writer_version=writer_version[:-len(codec.MEMORY_MARKER_SUFFIX)]
        control_version=writer_version+codec.MEMORY_MARKER_SUFFIX if marked else writer_version
        from .prompt_delivery import CONVERSATION_VERSION,CONVERSATION_MARKER_VERSION,METADATA_VERSION,METADATA_MARKER_VERSION
        from .prompt_delivery import CONTINUITY_VERSION,CONTINUITY_MARKER_VERSION
        if prompt_delivery_version is None:prompt_delivery_version=CONTINUITY_MARKER_VERSION if marked else CONTINUITY_VERSION
        conversation_context=prompt_delivery_version in {CONVERSATION_VERSION,CONVERSATION_MARKER_VERSION,METADATA_VERSION,METADATA_MARKER_VERSION}
        if prompt_delivery_version in {CONTINUITY_VERSION,CONTINUITY_MARKER_VERSION}:conversation_context='typed'
        app=self.application
        if action_sequence_context is not None:
            from .sequence_delivery import verify_context
            action_sequence_context=verify_context(app,event_id,action_sequence_context)
            composite_context_version=None;body_context=None;body_delivery_version=None
        from petlab_app.writer_visibility import VERSION as VISIBILITY_VERSION,plan as visibility_plan,receipt as visibility_receipt
        visibility=visibility_plan(app,event_id,version=VISIBILITY_VERSION if visibility_version is _CURRENT_VISUAL else visibility_version)
        if composite_context_version is _CURRENT_VISUAL:
            composite_context_version=None
            if self._idle(event_id):
                from petlab_app.idle_opening import source
                from model_packages.desktop_pet_v1.behavior import staged_selection
                if staged_selection(source(app,event_id,current=True)[0].row.get('behavior_selection')):
                    composite_context_version='desktop-pet-selected-plan-v1'
        if relation_version is _CURRENT_VISUAL:
            from .memory_relations import VERSION as RELATION_VERSION
            relation_version=RELATION_VERSION if self.memory_links else None
        if rag_context is _CURRENT_VISUAL:
            service=getattr(app,'rag_memory',None)
            rag_context=(service.context(event_id) if service is not None and not self._idle(event_id) else None)
        capabilities=self.visual_capabilities if visual_capabilities is _CURRENT_VISUAL else visual_capabilities
        if not self.expression_codes or writer_version in {codec.LEGACY_VERSION,codec.IDLE_VERSION}:capabilities=None
        if writer_version in {codec.IDLE_VERSION,codec.IDLE_DISPLAY_VERSION,codec.OPTIONAL_IDLE_VERSION}:
            packet,authorization,expression=self._idle_packet(event_id,writer_request_id,writer_version=writer_version,visual_capabilities=capabilities,content_view_version=content_view_version,composite_context_version=composite_context_version,relation_version=relation_version,visibility=visibility)
        else:
            projected=[]
            packet,authorization=self.packet(event_id,writer_request_id=writer_request_id,writer_version=writer_version,visual_capabilities=capabilities,content_view_version=content_view_version,rag_context=rag_context,relation_version=relation_version,visibility=visibility,_expression_out=projected,conversation_context=conversation_context)
            expression=projected[0]
        from .spatial_delivery import reserve as scene_reserve,augment as scene_augment
        from model_packages.desktop_pet_v1 import spatial
        if scene_context is _CURRENT_VISUAL:
            supplier=getattr(app,'scene_snapshot',None)
            scene_context=supplier() if callable(supplier) and self.expression_codes else None
        if scene_context is not None:
            scene_context=spatial.validate(scene_context)
            if scene_context['owner_ref']!=app.owner_ref:raise ValueError('scene belongs to another actor')
        from .prompt_delivery import DEFAULT_PET_PROMPT_BUDGET_CHARS
        full_budget=self.budget_chars or DEFAULT_PET_PROMPT_BUDGET_CHARS
        from . import reading_delivery
        if reading_context is _CURRENT_VISUAL:
            reading_context=reading_delivery.context_for(app,event_id,
                selected_ids=tuple(i for card in packet.packet.sections['memory'] for i in card.provenance.get('selected_memory_ids',())),
                readers=participant_audience(app,event_id))
        elif reading_context is not None and reading_context['version']==reading_delivery.RESULTS_VERSION:
            from .activity_result_context import build as linked_results
            expected=linked_results(app,event_id,
                tuple(i for card in packet.packet.sections['memory'] for i in card.provenance.get('selected_memory_ids',())),
                participant_audience(app,event_id))
            if thaw_model_data(reading_context['activity_results'])!=expected:raise ValueError('sealed activity result source differs')
        reading_reserve=reading_delivery.reserve(reading_context)
        from petlab_app.reaction_action import context as selected_body_context
        if body_context is _CURRENT_VISUAL:body_context=selected_body_context(app,event_id)
        elif body_context is not None and thaw_model_data(body_context)!=selected_body_context(app,event_id):raise ValueError('selected body context source differs')
        from .body_delivery import VERSION as BODY_DELIVERY_VERSION
        if body_delivery_version is _CURRENT_VISUAL:body_delivery_version=BODY_DELIVERY_VERSION if body_context is not None else None
        if body_delivery_version not in {None,BODY_DELIVERY_VERSION} or (body_delivery_version is not None and body_context is None):
            raise ValueError('unsupported selected body delivery')
        from . import meal_delivery
        from model_packages.desktop_pet_v1 import shared_meal
        if meal_scene_context is _CURRENT_VISUAL:
            supplier=getattr(app,'meal_snapshot',None)
            meal_scene_context=(body_context['scene'] if body_context is not None else supplier() if callable(supplier) else None)
        if meal_scene_context is not None:
            meal_scene_context=shared_meal.validate(meal_scene_context)
            if app.owner_ref not in meal_scene_context['participants']:raise ValueError('meal scene belongs to other participants')
        meal_reserve=meal_delivery.reserve(meal_scene_context)
        body_reserve=600 if body_context is not None and body_delivery_version is None else 0
        independent_gaze=composite_context_version is None and body_context is None
        scene_budget=full_budget-reading_reserve-body_reserve-meal_reserve-(scene_reserve(scene_context,independent=independent_gaze) if scene_context is not None else 0)
        from .prompt_delivery import VERSION as DEFAULT_DELIVERY,CAPABILITY_VERSION,READABLE_VERSION,MARKER_VERSION,EVIDENCE_VERSION,EVIDENCE_MARKER_VERSION
        prompt_delivery=compile_delivery(packet.packet,budget_chars=scene_budget if scene_context is not None or reading_context is not None or body_context is not None or meal_scene_context is not None else self.budget_chars,
            delivery_version=prompt_delivery_version,body_context=body_context if body_delivery_version is not None else None,action_sequence_context=action_sequence_context)
        if marked != (prompt_delivery['version'] in {MARKER_VERSION,EVIDENCE_MARKER_VERSION,"desktop-pet-prompt-delivery-v12-optional-idle-markers","desktop-pet-prompt-delivery-v14-idle-opportunity-markers","desktop-pet-prompt-delivery-v16-independent-idle-children-markers","desktop-pet-prompt-delivery-v18-timed-idle-opportunity-markers","desktop-pet-prompt-delivery-v20-shared-event-bodies-markers","desktop-pet-prompt-delivery-v22-subtype-references-markers","desktop-pet-prompt-delivery-v24-query-reference-markers","desktop-pet-prompt-delivery-v26-speaker-framed-markers",CONVERSATION_MARKER_VERSION,METADATA_MARKER_VERSION,CONTINUITY_MARKER_VERSION}):raise ValueError("writer marker codec/delivery version mismatch")
        if scene_context is not None:prompt_delivery=scene_augment(prompt_delivery,scene_context,budget_chars=full_budget-reading_reserve,independent=independent_gaze)
        if reading_context is not None:prompt_delivery=reading_delivery.augment(prompt_delivery,reading_context,budget_chars=full_budget)
        if meal_scene_context is not None:prompt_delivery=meal_delivery.augment(prompt_delivery,meal_scene_context,budget_chars=full_budget)
        if body_context is not None and body_delivery_version is None:
            from .body_delivery import augment as body_augment
            prompt_delivery=body_augment(prompt_delivery,body_context,budget_chars=full_budget,include_scene=meal_scene_context is None)
        destination=participant_destination(app,event_id)
        profile_facts={} if self.expression_profile is None else {"expression_profile":thaw_model_data(self.expression_profile)}
        if action_sequence_context is not None:profile_facts['action_sequence_context']=action_sequence_context
        if meal_scene_context is not None:profile_facts['meal_scene_context']=meal_scene_context
        if body_context is not None:profile_facts['body_context']=body_context
        if body_delivery_version is not None:profile_facts['body_delivery_version']=body_delivery_version
        if reading_context is not None:profile_facts['reading_context']=reading_context
        if destination is not None:profile_facts['participant_destination']=destination
        if visibility is not None:profile_facts['memory_visibility']=visibility_receipt(visibility)
        if composite_context_version is not None:profile_facts['composite_context_version']=composite_context_version
        if capabilities is not None:profile_facts['visual_capabilities']=capabilities
        if scene_context is not None:profile_facts['scene_context']=scene_context
        if writer_version in {codec.IDLE_VERSION,codec.IDLE_DISPLAY_VERSION,codec.OPTIONAL_IDLE_VERSION}:
            from .emission import prepare_idle_delivery
            expression_delivery=prepare_idle_delivery(app,event_id,packet,expression,prompt_delivery)
        elif authorization.host_authorization_evidence.get('origin')=='deferred_direct_reply_v1':
            from .emission import prepare_deferred_delivery
            expression_delivery=prepare_deferred_delivery(app,event_id,packet,prompt_delivery)
        else:expression_delivery=prepare_delivery(app,event_id,packet,prompt_delivery=prompt_delivery,_expression=expression)
        private_control={}
        if self.private_thought:
            from model_packages.desktop_pet_v1.composite import THOUGHT_VERSION
            private_control={'allow_thought':True,'composite':{'version':THOUGHT_VERSION,'children':{
                'speech':{'kind':'speech','key':'reply'},'thought':{'kind':'thought','key':'inner_monologue'}}}}
            control_version=codec.PRIVATE_EXPRESSION_VERSION
        if scene_context is not None and independent_gaze:
            from model_packages.desktop_pet_v1.composite import PRESENTATION_VERSION
            children={'speech':{'kind':'speech','key':'reply'},'gaze':{'kind':'expression','key':'selected_presentation'}}
            if self.private_thought:children['thought']={'kind':'thought','key':'inner_monologue'}
            private_control={**private_control,'composite':{'version':PRESENTATION_VERSION,'children':children}}
        if action_sequence_context is not None:
            from model_packages.desktop_pet_v1.action_sequence import VERSION as SEQUENCE_VERSION
            private_control.pop('composite',None)
            private_control['action_sequence_contract']=SEQUENCE_VERSION
        request=RealizerRequest.create(session_binding=app.runtime.session.session_binding,
            current_position=app.runtime.session.position,authorization=authorization,
            control={"version":control_version,"selected_action_keys":[],"allow_speech":True,**private_control,
                **({"gaze_contract":spatial.GAZE} if scene_context is not None else {}),
                **({"presentation_contract":"desktop-pet-face-expression-v1"} if self.expression_codes and writer_version!=codec.LEGACY_VERSION and writer_version!=codec.IDLE_VERSION else {})},
            readonly_facts={**profile_facts,**({"memory_relation_version":relation_version} if relation_version is not None else {}),**({"rag_context":rag_context} if rag_context is not None else {}),**({"content_view_version":content_view_version} if content_view_version is not None else {}),"writer_packet":packet.packet.to_record(),"writer_delivery":thaw_model_data(packet.receipt),
                "prompt_delivery":prompt_delivery,
                "memory_display_config":{"numbered":self.numbered_memory,"links":self.memory_links,"budget_chars":self.budget_chars},
                "expression_delivery":expression_delivery})
        return packet,authorization,request

    def prepare(self,event_id,*,seal_only=False,action_sequence_context=None):
        app=self.application
        with app._lock:
            from . import deferred_reply
            if not self._idle(event_id) and deferred_reply.needed(app,event_id):
                deferred_reply.authorize(app,event_id)
                from .retired_writer import retire_stale
                retire_stale(self,event_id)
            restored=self._restore_work(event_id)
            from .quiet_realization import QuietRealization
            if isinstance(restored,(PreparedExecution,QuietRealization)):return restored
            if restored is None:
                parent_event_id=event_id
                from . import deferred_reply
                if not self._idle(event_id) and deferred_reply.needed(app,event_id):
                    from .settlement_view import current_projection
                    parent_event_id=current_projection(app).context.event_ref
                identity=self.attempt_journal.allocate_request(parent_event_id=parent_event_id)
                packet,authorization,request=self._request(event_id,writer_request_id=identity.writer_request_id,action_sequence_context=action_sequence_context)
                realizer_path=self.gateway.trace_dir/("realizer-"+uuid.uuid4().hex+".json")
                delivery=thaw_model_data(request.readonly_facts['prompt_delivery'])
                prompt={key:delivery[key] for key in ('system','user','prompt_fingerprint')}
                self.attempt_journal.record_packet(identity.writer_request_id,packet=packet.packet,
                    provider_dispatch_tracked=True,prepared_prompt=prompt,source_evidence={
                        'version':_INLINE_SEALED,'event_id':event_id,
                        'source_fact_fingerprint':self._source_fingerprint(event_id),
                        'definition_fingerprint':app.definition.fingerprint,'position':app.runtime.session.position.to_record(),
                        'transport_binding':self.gateway.transport_for("writer").binding,'writer_temperature':self.temperature,
                        'scope_ref':app.definition.scope_ref,'request':request.to_record(),
                        'authorization':authorization.to_record(),'realizer_path':str(realizer_path)})
                self.attempt_journal.record_prompt(identity.writer_request_id,**prompt)
                writer_request_id=identity.writer_request_id
                inline_archive=True
            else:
                writer_request_id,authorization,request,realizer_path=restored
                inline_archive=self.attempt_journal.read_attempt(writer_request_id)['source_evidence']['version']==_INLINE_SEALED
            if seal_only:return request
            delivery=thaw_model_data(request.readonly_facts["prompt_delivery"])
            aliases=delivery["memory_aliases"]
            ids=tuple(aliases.values())
            packet_fingerprint=request.readonly_facts['writer_packet']['packet_fingerprint']
            def parse(wire):
                try:
                    from model_packages.desktop_pet_v1.visual_capabilities import validate
                    frozen_capabilities=validate(request.readonly_facts.get('visual_capabilities'))
                    text=completion_text(wire);gaze=None;reading={}
                    sequence_context=request.readonly_facts.get('action_sequence_context')
                    if sequence_context is not None:
                        from .sequence_delivery import split_output as split_sequence
                        text,keys=split_sequence(text,sequence_context)
                        reading['actions']=keys
                    context=request.readonly_facts.get('reading_context')
                    if context is not None and context.get('site') is not None:
                        from .reading_delivery import split_output as split_reading
                        text,query=split_reading(text,site=context['site'])
                        reading={'search_query':query}
                    if request.readonly_facts.get('scene_context') is not None:
                        from .spatial_delivery import split_output
                        text,gaze=split_output(text,request.readonly_facts['scene_context'],
                            independent=delivery['scene_delivery']['independent'])
                    if request.control.get('allow_thought') is True:
                        value=codec.parse_private_expression(text,delivered_memory_ids=tuple(aliases),
                            expression_codes=request.control.get('presentation_contract')=='desktop-pet-face-expression-v1',
                            allowed_expressions=None if frozen_capabilities is None else tuple(frozen_capabilities['choices']))
                        value['reported_used_memory_ids']={channel:[aliases[key] for key in used] for channel,used in value['reported_used_memory_ids'].items()}
                        if gaze is not None:value['gaze_target']=gaze
                        value.update(reading)
                        return value
                    parse_speech=codec.parse_marked_speech if request.control["version"].endswith(codec.MEMORY_MARKER_SUFFIX) else codec.parse_speech
                    value=parse_speech(text,delivered_memory_ids=tuple(aliases),
                        expression_codes=request.control.get("presentation_contract")=="desktop-pet-face-expression-v1",
                        allowed_expressions=None if frozen_capabilities is None else tuple(frozen_capabilities['choices']),
                        allow_silence=(request.control["version"].removesuffix(codec.MEMORY_MARKER_SUFFIX)==codec.OPTIONAL_IDLE_VERSION
                            or delivery.get("scene_delivery",{}).get("independent",False) or sequence_context is not None))
                    value["reported_used_memory_ids"]=[aliases[key] for key in value["reported_used_memory_ids"]]
                    if gaze is not None:value['gaze_target']=gaze
                    value.update(reading)
                    return value
                except (ValueError,TypeError):
                    raise ProviderCodecParseFailureSignal(failure_kind="invalid_pet_writer_output",
                        failure_source_ref=model_data_fingerprint(wire)) from None
            def invoke(realizer_request):
                self.attempt_journal.record_provider_dispatch(writer_request_id)
                self.attempt_journal.flush_batch()
                self.attempt_journal._readers.release()  # Never hold log handles over network I/O.
                result=self.gateway.run(session_binding=app.runtime.session.session_binding,
                    current_position=app.runtime.session.position,operation_kind="writer",source_fact_ref=event_id,
                    codec_contract=request.control['version']+('+scene-v1' if request.control.get('gaze_contract') else '')+('+reading-parameters-v1' if request.readonly_facts.get('reading_context',{}).get('site') else '')+('+action-sequence-v1' if request.control.get('action_sequence_contract') else ''),messages=[{"role":"system","content":delivery["system"]},
                        {"role":"user","content":delivery["user"]}],
                    temperature=self.temperature,require_json=True,parse=parse,source_receipt_refs=(authorization.fingerprint,))
                if result.status!="parsed":
                    raise RealizerInvocationFailure(failure_kind="pet_writer_failed",failure_source_ref=result.request.fingerprint)
                value=result.parsed_payload
                face={"version":"desktop-pet-face-expression-v1","expression":value["expression"]} if "expression" in value else None
                if "gaze_target" in value:
                    from model_packages.desktop_pet_v1.spatial import presentation
                    face=presentation(value['expression'],request.readonly_facts['scene_context'],value['gaze_target'])
                speech=None if value["speech"] is None else Speech(content_id="speech",description=value["speech"],recipient_refs=tuple(request.readonly_facts.get('participant_destination',{}).get('audience',(app.user_ref,))),
                    parameters={"presentation":face} if face is not None else {})
                contents=[] if speech is None else [speech.to_record()]
                if speech is None and face is not None and (request.control.get('action_sequence_contract') or value.get('gaze_target') is not None and request.readonly_facts['prompt_delivery']['scene_delivery']['independent']):
                    from model_packages.desktop_pet_v1.events import Expression
                    from model_packages.desktop_pet_v1.face_expression import LABELS
                    contents.append(Expression(content_id='gaze',description='呈现'+LABELS[value['expression']]+'的表情',
                        recipient_refs=tuple(request.readonly_facts.get('participant_destination',{}).get('audience',(app.user_ref,))),
                        parameters={'presentation':face}).to_record())
                channel_claims={}
                used=value['reported_used_memory_ids']
                if request.control.get('allow_thought') is True:
                    if value['thought'] is not None:
                        contents.append(Thought(content_id='thought',description=value['thought'],recipient_refs=(app.owner_ref,)).to_record())
                    channel_claims={'reported_used_memory_ids_by_channel':dict(used)}
                    used=list(dict.fromkeys(key for channel in ('speech','thought') for key in used[channel]))
                payload={'contents':contents};sequence_evidence={}
                if request.control.get('action_sequence_contract'):
                    from model_packages.desktop_pet_v1.action_sequence import compose,VERSION as SEQUENCE_VERSION
                    from model_packages.desktop_pet_v1.events import Content
                    items,declaration=compose(value['actions'],request.readonly_facts['action_sequence_context'],
                        tuple(Content.from_record(c) for c in contents),audience=request.readonly_facts.get('participant_destination',{}).get('audience',(app.user_ref,)),allow_thought=request.control.get('allow_thought',False))
                    payload={'contents':[c.to_record() for c in items],'action_sequence':declaration}
                    sequence_evidence={'action_sequence_contract':SEQUENCE_VERSION}
                return {"candidate_payload":payload,
                    "realization_evidence":{"version":request.control["version"],"writer_packet_fingerprint":packet_fingerprint,
                        **sequence_evidence,
                        **({"speech_decision":"silent"} if speech is None else {}),
                        **({"reading_intent":{"version":"pet-reading-parameters-v1","site":request.readonly_facts["reading_context"]["site"],"query":value["search_query"],"source_tick":event_id}} if "search_query" in value else {}),
                        "expression_delivery":thaw_model_data(request.readonly_facts["expression_delivery"]),
                        "prompt_delivery_fingerprint":delivery["fingerprint"],
                        "writer_delivery_fingerprint":request.readonly_facts['writer_delivery']['fingerprint'],"delivered_memory_ids":list(ids),
                        "reported_used_memory_ids":list(used),**channel_claims,
                        "provider_request_fingerprint":result.request.fingerprint,
                        "provider_codec_fingerprint":result.parse_receipt.fingerprint,
                        "realizer_trace_ref":str(realizer_path)}}
            result=execute_realizer(request=request,implementation_ref=request.control["version"],
                invocation_evidence={"writer_packet_fingerprint":packet_fingerprint},invoke=invoke)
            record={"version":"desktop-pet-realizer-trace-v1","status":result.status,
                "request":request.to_record(),"attempt":result.attempt.to_record(),
                "candidate":None if result.candidate is None else result.candidate.to_record(),
                "failure":None if result.failure is None else result.failure.to_record(),"receipt":thaw_model_data(result.receipt)}
            record["fingerprint"]=model_data_fingerprint(record)
            if not inline_archive:_save_trace(realizer_path,record)
            self.attempt_journal.record_provider_result(writer_request_id,provider_result={'realizer_trace':record})
            self.attempt_journal.record_terminal(writer_request_id,status=result.status)
            written=self.attempt_journal.flush_batch()
            if inline_archive:self._readonly_archives.defer(realizer_path,record)
            if result.status!="candidate_ready":raise RuntimeError("pet writer realizer failed")
            from .quiet_realization import quiet_candidate
            context=request.readonly_facts.get('reading_context')
            if context is not None and context.get('result') is not None:
                app._reading_context_offered=(event_id,context['result']['event_id'])
            quiet=quiet_candidate(request,result.candidate)
            if quiet is not None:return quiet
            prepared=PreparedExecution(position=app.runtime.session.position,scope_ref=app.definition.scope_ref,
                authorization=authorization,request=request,attempt=result.attempt,candidate=result.candidate)
            # Keep the exact native result and the bytes of its durable archive.
            # Only a current source uses this path; restart/deferred work still
            # reconstructs and validates every sealed field through _restore_work.
            if inline_archive and authorization.host_authorization_evidence.get('origin')!='deferred_direct_reply_v1':
                self._live_prepared=_LivePrepared(prepared,event_id,
                    written[self.attempt_journal._artifact_path(writer_request_id)],
                    app.definition.fingerprint,app.runtime.session.session_binding,
                    app.runtime.session.position,self._source_fingerprint(event_id),
                    freeze_model_payload(record),realizer_path,prepared.fingerprint,
                    app.runtime.session.state,app.runtime.session.parameters)
            return prepared

    def _verify_live_prepared(self,prepared,source_id):
        cached=getattr(self,'_live_prepared',None);app=self.application
        if (cached is None or cached.prepared is not prepared or cached.source_id!=source_id
                or cached.definition_fingerprint!=app.definition.fingerprint or cached.binding is not app.runtime.session.session_binding
                or cached.position is not app.runtime.session.position or cached.source_fingerprint!=self._source_fingerprint(source_id)
                or cached.fingerprint!=prepared.fingerprint
                or cached.state is not app.runtime.session.state
                or cached.parameters is not app.runtime.session.parameters):return False
        if prepared.request.readonly_facts.get('memory_display_config')!={'numbered':self.numbered_memory,
                'links':self.memory_links,'budget_chars':self.budget_chars}:return False
        request_id=prepared.request.readonly_facts['writer_packet']['writer_request_id']
        rows=self.attempt_journal.read_parent_receipts(source_id)
        complete={r['writer_request_id'] for r in rows if r['stage']=='commit'
            or (r['stage']=='terminal' and r.get('terminal_status')=='failed')}
        pending=[r['writer_request_id'] for r in rows if r['stage']=='allocated' and r['writer_request_id'] not in complete]
        if pending!=[request_id]:return False
        actual=self.attempt_journal._readers.read(self.attempt_journal._artifact_path(request_id))
        if actual!=cached.artifact_bytes:return False
        rag=prepared.request.readonly_facts.get('rag_context')
        if rag is not None:app.rag_memory.validate(rag,source_id)
        self._verify_readonly_trace(cached.trace_path,cached.record,inline=True)
        return True

    def verify_prepared(self,prepared,source):
        app=self.application
        from model_packages.desktop_pet_v1.behavior import VERSION
        if prepared.request.control.get('version')==VERSION:
            from .behavior import verify
            from types import SimpleNamespace
            # Compatibility for callers holding an older Writer verifier handle.
            # New product execution uses the independent realizers entry.
            return verify(SimpleNamespace(application=app,gateway=self.gateway),prepared,source)
        from petlab_app.idle_opening import IdleTickSource
        source_id=source.tick_id if isinstance(source,IdleTickSource) else source.event.event_id
        if self._verify_live_prepared(prepared,source_id):return
        request_id=prepared.request.readonly_facts['writer_packet']['writer_request_id']
        if any(row['stage']=='allocated' and row['writer_request_id']==request_id
                for row in self.attempt_journal.read_receipts()):
            restored=self._restore_work(source_id)
            if not isinstance(restored,PreparedExecution) or restored.fingerprint!=prepared.fingerprint:
                raise ValueError('writer candidate differs from actual archived realizer trace')
            return
        packet,authorization,request=self._request(source_id,
            writer_request_id=prepared.request.readonly_facts["writer_packet"]["writer_request_id"],
            writer_version=prepared.request.control["version"],
            visual_capabilities=prepared.request.readonly_facts.get('visual_capabilities'),
            content_view_version=prepared.request.readonly_facts.get('content_view_version'),
            composite_context_version=prepared.request.readonly_facts.get('composite_context_version'),
            prompt_delivery_version=prepared.request.readonly_facts['prompt_delivery']['version'],
            rag_context=prepared.request.readonly_facts.get('rag_context'),
            relation_version=prepared.request.readonly_facts.get('memory_relation_version'),
            visibility_version=prepared.request.readonly_facts.get('memory_visibility',{}).get('version'),
            scene_context=prepared.request.readonly_facts.get('scene_context'),reading_context=prepared.request.readonly_facts.get('reading_context'),body_context=prepared.request.readonly_facts.get('body_context'),meal_scene_context=prepared.request.readonly_facts.get('meal_scene_context'),body_delivery_version=prepared.request.readonly_facts.get('body_delivery_version'),action_sequence_context=prepared.request.readonly_facts.get('action_sequence_context'))
        if authorization!=prepared.authorization or request!=prepared.request:
            raise ValueError("writer candidate differs from formal source and packet")
        evidence=prepared.candidate.realization_evidence
        delivery=request.readonly_facts["prompt_delivery"]
        ids=tuple(delivery["memory_aliases"].values())
        if (evidence.get("prompt_delivery_fingerprint")!=delivery["fingerprint"]
                or evidence.get("expression_delivery")!=request.readonly_facts["expression_delivery"]
                or evidence["writer_delivery_fingerprint"]!=packet.receipt["fingerprint"]
                or evidence["writer_packet_fingerprint"]!=packet.packet.packet_fingerprint
                or tuple(evidence["delivered_memory_ids"])!=ids
                or not set(evidence["reported_used_memory_ids"]).issubset(ids)):
            raise ValueError("writer candidate memory delivery evidence differs")
        trace=Path(evidence["realizer_trace_ref"]).resolve()
        if trace.parent!=self.gateway.trace_dir or not trace.name.startswith("realizer-"):
            raise ValueError("writer candidate trace is outside its bound source")
        record=json.loads(trace.read_text(encoding="utf-8"))
        fingerprint=record.pop("fingerprint",None)
        if (model_data_fingerprint(record)!=fingerprint or record["status"]!="candidate_ready"
                or record["request"]!=prepared.request.to_record() or record["attempt"]!=prepared.attempt.to_record()
                or record["candidate"]!=prepared.candidate.to_record()):
            raise ValueError("writer candidate differs from actual realizer trace")


def _writer_io_scope(method):
    from functools import wraps
    @wraps(method)
    def scoped(self,*args,**kwargs):
        from contextlib import nullcontext
        batch=self.attempt_journal.batch() if method.__name__=='prepare' else nullcontext()
        with batch:
            with self.attempt_journal._readers.scope():return method(self,*args,**kwargs)
    return scoped


PetRealizationService.prepare=_writer_io_scope(PetRealizationService.prepare)
PetRealizationService.verify_prepared=_writer_io_scope(PetRealizationService.verify_prepared)
