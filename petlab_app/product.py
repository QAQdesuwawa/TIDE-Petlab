"""A production consumer of the existing one pet event and execution contract."""
from __future__ import annotations

import time

from model_packages.desktop_pet_v1.events import PetEvent
from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
from product_adapters.desktop_pet_v1.execution import ExecutionCancelled, ExecutionUncertain
from product_adapters.desktop_pet_v1.realization import PetRealizationService
from product_adapters.desktop_pet_v1.semantic import TreePetSemanticService as PetSemanticService
from .composition import open_application
from .ordered_checkpoint import product_scope


def wall_clock_ms():
    return time.time_ns()//1_000_000


class PetProduct:
    def __init__(self,*,application,gateway,writer_temperature,numbered_memory=False,memory_links=False,writer_budget_chars=None,expression_profile=None,expression_codes=False,visual_capabilities=None,writer_enabled=True,memory_recall_version=None,private_thought=False):
        if type(writer_enabled) is not bool:raise ValueError('writer_enabled must be boolean')
        self.application=application
        self.gateway=gateway
        self.reply_cancellations=None  # Optional read-only host work disposition, never memory/state.
        application.memory_recall_version=memory_recall_version
        self.semantic=PetSemanticService(application=application,gateway=gateway,memory_recall_version=memory_recall_version,defer_audit_views=True)
        self.writer=PetRealizationService(application=application,gateway=gateway,temperature=writer_temperature,
            numbered_memory=numbered_memory,memory_links=memory_links,budget_chars=writer_budget_chars,
            expression_profile=expression_profile,expression_codes=expression_codes,visual_capabilities=visual_capabilities,private_thought=private_thought) if writer_enabled else None
        from pathlib import Path
        import os
        from .local_retrieval import LocalMemoryRetrieval
        from product_adapters.desktop_pet_v1.rag_memory import RagMemoryService
        pack=Path(os.environ.get('TIDE_PET_LOCAL_MODELS',str(Path(__file__).resolve().parents[1]/'local_models')))
        provider=LocalMemoryRetrieval(model_root=pack,index_path=application.store.path.with_suffix('.rag.sqlite3'))
        self.rag_memory=RagMemoryService(application=application,provider=provider,trace_dir=gateway.trace_dir/'local-rag',reply_source_key=self._reply_source_key)
        application.rag_memory=self.rag_memory
        self.rag_memory.warm_sources()
        from product_adapters.desktop_pet_v1.realizers import PetRealizers
        self.realizers=PetRealizers(self)


    @classmethod
    def open(cls,*,definition,session_path,gateway,writer_temperature,initial_coordinate=None,numbered_memory=False,memory_links=False,writer_budget_chars=None,expression_profile=None,expression_codes=False,visual_capabilities=None,writer_enabled=True,memory_recall_version=None,model_package=None,private_thought=False):
        holder={}
        def verify(prepared,source):
            return holder["product"].realizers.verify_prepared(prepared,source)
        app=open_application(definition=definition,session_path=session_path,action_verifier=verify,
            initial_coordinate=initial_coordinate,model_package=model_package)
        try:
            product=cls(application=app,gateway=gateway,writer_temperature=writer_temperature,
                numbered_memory=numbered_memory,memory_links=memory_links,writer_budget_chars=writer_budget_chars,
                expression_profile=expression_profile,expression_codes=expression_codes,visual_capabilities=visual_capabilities,writer_enabled=writer_enabled,memory_recall_version=memory_recall_version,private_thought=private_thought)
            holder["product"]=product
            return product
        except Exception:
            app.close()
            raise

    @product_scope
    def _reply(self,event_id,*,executor,completion_coordinate,is_cancelled,
            continuation=None,on_reply_ready=lambda:None,on_prepared=lambda prepared:None):
        # All opening contexts consume the same expression/execution path.
        # Only sustained input supplies an additional live-source guard.
        publish=getattr(self,'on_settled_presentation',None)
        if publish is not None:publish()
        if continuation is not None:
            if not continuation.check('before_writer'):return None
        elif is_cancelled():return None
        self.last_quiet_realization=None
        prepared=self.realizers.prepare(event_id,on_language_ready=on_reply_ready)
        from product_adapters.desktop_pet_v1.quiet_realization import QuietRealization
        if isinstance(prepared,QuietRealization):
            self.last_quiet_realization=prepared
            if self.writer is not None:self.writer.publish_readonly_archives()
            return None
        if 'late_speech' in prepared.candidate.candidate_payload and self.writer is not None:
            self.writer.prepare(event_id,seal_only=True)
        on_prepared(prepared)
        if continuation is not None and not continuation.check('before_execution',prepared):return None
        if is_cancelled():return None
        def execute_guarded(action_id,contents):
            self.application.store.flush_pending()
            if is_cancelled():
                raise ExecutionCancelled('opening cancelled before any effect')
            admit=getattr(continuation,'admit_effects',None)
            if admit is not None and not admit():
                raise ExecutionCancelled('newer received facts superseded unshown reply')
            from .execution_context import bind
            from .visible_execution import observe
            with observe(self,prepared,action_id,contents,executor) as effect_sink:
                with bind(executor,action_id,target_ref=prepared.authorization.target_ref,
                        session_binding=self.application.runtime.session.session_binding.fingerprint,
                        authorization_ref=prepared.authorization.fingerprint,observer=effect_sink):
                    composite=prepared.composite_plan()
                    if composite is not None:
                        if 'late_speech' in prepared.candidate.candidate_payload:
                            from product_adapters.desktop_pet_v1.staged_execution import execute_staged
                            return execute_staged(self,event_id,prepared,action_id,contents,executor,is_cancelled)
                        composite_execute=getattr(executor,'execute_composite',None)
                        if composite_execute is None:raise ExecutionCancelled('executor lacks composite capability; no effects dispatched')
                        from product_adapters.desktop_pet_v1.composite_execution import execute
                        return execute(root=self.gateway.trace_dir/'parents',parent_id=action_id,contents=contents,
                            plan=composite,
                            source={'prepared_fingerprint':prepared.fingerprint,'request':prepared.request.to_record(),
                                    'source_event_id':prepared.authorization.source_fact_ref},
                            executor=executor,is_cancelled=is_cancelled)
                    guarded=getattr(executor,'execute_guarded',None)
                    if guarded is not None:
                        return guarded(action_id,contents,is_cancelled=is_cancelled)
                    return executor(action_id,contents)
        executor_cancelled=False
        try:
            return self.application.execute(prepared,executor=execute_guarded,coordinate=completion_coordinate,
                measure=self.semantic.measure)
        except ExecutionCancelled:
            executor_cancelled=True
            # The application records confirmed zero effect. Keep the original
            # candidate/source for explicit recovery; uncertainty still raises.
            return None
        finally:
            if continuation is not None:
                continuation.publish('execution_guard',prepared=prepared,executor_cancelled=executor_cancelled)
            if self.writer is not None:self.writer.publish_readonly_archives()
            self.rag_memory.prime()

    @product_scope
    def interact(self,*,event_id,contents,coordinate,executor,completion_coordinate=wall_clock_ms,
            response_policy="required",is_cancelled=lambda:False,retry_failed=False,on_input_settled=lambda:None,on_reply_ready=lambda:None,admission_coordinate=None,admission_version=None,input_source=None):
        app=self.application
        with app._lock:
            result=app.receive(event_id=event_id,scope_ref=app.definition.scope_ref,actor_ref=(input_source['actor_ref'] if input_source is not None else app.user_ref),
                contents=contents,coordinate=coordinate,
                measure=self.semantic.retry_measure if retry_failed else self.semantic.measure,response_policy=response_policy,admission_coordinate=admission_coordinate,input_source=input_source,
                **({} if admission_version is None else {"admission_version":admission_version}))
            on_input_settled()
            if response_policy!="required":return result
            if result.replayed:
                fact=result.memory.fact
                replies=[]
                for source in app._product()["sources"].values():
                    if source["origin"]!="execution":continue
                    if self._reply_source_key(source)==(fact.accepted_runtime_ordinal,fact.temporal_position_fingerprint):
                        replies.append(PetEvent.from_record(source["event"]))
                if len(replies)>1:raise ValueError("one input has multiple actual direct replies")
                if replies:
                    app._verify_source(replies[0])
                    return app._settle(replies[0],self.semantic.measure)
            return self._reply(event_id,executor=executor,completion_coordinate=completion_coordinate,
                is_cancelled=is_cancelled,on_reply_ready=on_reply_ready)

    def _reply_source_key(self,source):
        receipt=source['evidence']['commit_receipt']
        delivery=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('expression_delivery',{})
        if delivery.get('source_kind')=='deferred_reply':
            from product_adapters.desktop_pet_v1.deferred_reply import verify_delivery
            ordinal,position=verify_delivery(self.application,delivery)
            if (receipt['source_runtime_ordinal'],receipt['source_position_fingerprint'])!=(ordinal,position):
                raise ValueError('deferred output differs from its planning position')
            fact=self.application.runtime.memory_instance(delivery['event_id']).fact
            return fact.accepted_runtime_ordinal,fact.temporal_position_fingerprint
        return receipt['source_runtime_ordinal'],receipt['source_position_fingerprint']

    def pending_reply_ids(self):
        """Derive unanswered direct inputs from formal sources, not drafts.

        A confirmed execution counts even if its output measurement is pending.
        This is work bookkeeping, not memory selection or send authorization.
        """
        app=self.application
        with app._lock:
            sources=app._product()['sources'];answered=set();required=[]
            for source in sources.values():
                if source['origin']!='execution':continue
                event=PetEvent.from_record(source['event']);app._verify_source(event)
                key=self._reply_source_key(source)
                if key in answered:raise ValueError('one input has multiple actual replies')
                answered.add(key)
            for event in app.runtime.events:
                source=sources[event.event_id]
                if event.event_kind!='peer_input' or source['evidence'].get('response_policy')!='required':continue
                app._verify_source(event)
                # Legacy ongoing observations recover only through live capture.
                if any(content.parameters.get('capture_version') in {'petlab-mouse-observation-v2','petlab-mouse-observation-v3','petlab-mouse-contact-v4','petlab-mouse-batch-v5'}
                        and content.parameters.get('phase') in {'ongoing','started'} for content in event.contents):continue
                fact=app.runtime.memory_instance(event.event_id).fact
                if (fact.accepted_runtime_ordinal,fact.temporal_position_fingerprint) not in answered:
                    required.append((fact.accepted_runtime_ordinal,event.event_id))
            cancelled=set(self.reply_cancellations()) if self.reply_cancellations is not None else set()
            return tuple(event_id for _,event_id in sorted(required) if event_id not in cancelled)

    def resume(self,*,executor,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False):
        """Explicit recovery, with no replay of an uncertain external effect."""
        app=self.application
        with app._lock:
            product=app._product()
            unfinished=[key for key,entry in product['executions'].items() if entry['status']!='executed']
            if unfinished:
                if len(unfinished)!=1:raise ExecutionUncertain('multiple pending executions need reconciliation')
                from product_adapters.desktop_pet_v1.composite_execution import reconcile
                return reconcile(self,unfinished[0])
            journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
            pending=[source for event_id,source in product["sources"].items() if event_id not in journal]
            if len(pending)>1:raise ValueError("single pet has more than one pending source")
            if pending:
                source=pending[0];event=PetEvent.from_record(source["event"])
                if source["origin"]=="execution":
                    app._verify_source(event)
                    result=app._settle(event,self.semantic.measure)
                    if any(c.kind=='expression' or c.parameters.get('composite_version') for c in event.contents):
                        from product_adapters.desktop_pet_v1.composite_journal import ParentJournal
                        from product_adapters.desktop_pet_v1.composite_execution import acknowledge_idle
                        state=ParentJournal(self.gateway.trace_dir/'parents',event.event_id).state()
                        if 'source' in state:acknowledge_idle(app,state['source'].get('source_event_id'))
                    return result
                app.receive(event_id=event.event_id,scope_ref=event.scope_ref,actor_ref=event.actor_ref,
                    contents=event.contents,coordinate=source['evidence'].get('capture_position',event.position.to_record())['coordinate'],measure=self.semantic.retry_measure,
                    response_policy=source["evidence"].get("response_policy"))
            outstanding=self.pending_reply_ids()
            if not outstanding:return None
            return self._reply(outstanding[0],executor=executor,completion_coordinate=completion_coordinate,
                is_cancelled=is_cancelled)

    def read_page(self,**kwargs):
        from .web_reading import read_page
        return read_page(self,**kwargs)

    def latest_speech(self):
        app=self.application
        with app._lock:
            events=sorted(app.runtime.events,
                key=lambda item:app.runtime.memory_instance(item.event_id).fact.accepted_runtime_ordinal,reverse=True)
            for event in events:
                if event.event_kind!="npc_output":continue
                for content in reversed(event.visible_contents(app.user_ref)):
                    if content.kind=="speech":return content.description
        return None

    def close(self):
        try:self.rag_memory.close()
        finally:
            try:
                if self.writer is not None:self.writer.close()
            finally:
                try:self.gateway.close()
                finally:self.application.close()
