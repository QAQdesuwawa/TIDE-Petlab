"""Durable single-instance composition; all psychology remains package owned."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import threading

from framework.action_commit_lifecycle import execute_action_commit_lifecycle
from framework.history import CommittedFact, HistoryAuthority, FRAMEWORK_HISTORY_PROFILE_KEY
from framework.model_api import model_data_fingerprint, thaw_model_data
from model_packages.desktop_pet_v1.events import PetEvent
from ops.runtime_session_bootstrap import restore_bootstrapped_runtime_session
from product_adapters.desktop_pet_v1 import SinglePetRuntime, IngestResult, EVENT_JOURNAL_KEY
from product_adapters.desktop_pet_v1.execution import PreparedExecution, ExecutionOutcome, ExecutionUncertain, ExecutionCancelled
from product_adapters.desktop_pet_v1.expression import attach_input_continuation
from product_adapters.desktop_pet_v1.emission import commit_delivery,verify_commits
from ops.runtime_expression_state import RUNTIME_EXPRESSION_STATE_KEY

from .owned_journals import seal_session,publish_session

PRODUCT_KEY = "desktop_pet_product_v1"
ADMISSION_VERSION = "desktop-pet-received-input-v3-admission"
POINT_ADMISSION_VERSION = "desktop-pet-received-input-v4-point-admission"
ADMISSION_VERSIONS = {ADMISSION_VERSION, POINT_ADMISSION_VERSION}


class SinglePetApplication:
    def __init__(self, *, session, model_runtime, temporal_runtime, owner_ref, user_ref, store, action_verifier, runtime_variant=None, memory_query_budget_ms=None):
        if not callable(action_verifier):
            raise ValueError("the application requires a package action verifier")
        if not isinstance(user_ref, str) or not user_ref or owner_ref == user_ref:
            raise ValueError("single-instance user identity is invalid")
        if runtime_variant is not None and runtime_variant != session.session_binding.runtime_variant:
            raise ValueError("application variant differs from its explicit session binding")
        self.runtime_variant = session.session_binding.runtime_variant
        self.owner_ref, self.user_ref = owner_ref, user_ref
        self.store, self.action_verifier = store, action_verifier
        self._lock = threading.RLock()
        self.on_execution_committed = lambda event: None
        self.runtime = SinglePetRuntime(session=session, model_runtime=model_runtime, temporal_runtime=temporal_runtime,
                                        owner_ref=owner_ref, source_verifier=self._verify_source,
                                        memory_query_budget_ms=memory_query_budget_ms)
        # Historical output validation can need the owner's expression scope.
        # Bind the saved identity before validation, never a replacement config.
        from .composition import DEFINITION_KEY,PetDefinition
        saved_definition=session.profile.get(DEFINITION_KEY)
        if saved_definition is not None:
            self.definition=PetDefinition.from_record(saved_definition)
            if self.definition.owner_ref!=owner_ref or self.definition.user_ref!=user_ref:
                raise ValueError('saved pet identity differs from application owner')
        self._validate_product()
        history=self.runtime.session.profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
        if history is not None and history.get('version')=='framework-history-snapshot-storage-v2':
            # Reuse the framework's validated factory snapshot and its existing
            # identity cache; never manufacture a replacement History root.
            published=self.runtime._history().to_record(compact=True)
            self.runtime.session=replace(self.runtime.session,profile={
                **self.runtime.session.profile,FRAMEWORK_HISTORY_PROFILE_KEY:published})
        self.runtime.session=seal_session(self.runtime.session)
        # Cold restoration is outside interaction admission. Warm the exact
        # sealed rows once; new/changed rows still pass the original validators.
        from ops.runtime_product_expression_continuation import validated_product_expression_continuations
        from ops.runtime_session_binding import restore_runtime_progress_transactions
        from framework.runtime_kernel import RuntimeProgressAuthority,RuntimeStateCommitAuthority
        current=self.runtime.session
        validated_product_expression_continuations(current.profile,session_binding=current.session_binding)
        restore_runtime_progress_transactions(current.profile,session_binding=current.session_binding,materialize=False,
            progress_authority=RuntimeProgressAuthority(session_binding=current.session_binding,
                temporal_runtime=self.runtime.temporal_runtime),
            state_authority=RuntimeStateCommitAuthority(session_binding=current.session_binding))
        from interaction_extensions.local_memory_source import register as register_memory_source
        register_memory_source(self.runtime._history(),
            session_id=current.session_binding.session_id,owner_ref=self.owner_ref)

    @classmethod
    def create(cls, **kwargs):
        if kwargs["store"].path.exists():
            raise ValueError("existing session must be restored rather than overwritten")
        app = cls(**kwargs)
        app._publish_product(app._product())
        return app

    @classmethod
    def restore(cls, *, store, model_runtime, temporal_runtime, runtime_variant, **kwargs):
        checkpoint = store.read(session_id=model_runtime.session_id)
        from model_packages.desktop_pet_v1.grounded_recall import register_profile
        register_profile(checkpoint["profile"])
        from model_packages.desktop_pet_v1.declaration_sources import restore_scope
        with restore_scope(checkpoint['profile']):
            session = restore_bootstrapped_runtime_session(profile=checkpoint["profile"], model_runtime=model_runtime,
                temporal_runtime=temporal_runtime, runtime_variant=runtime_variant)
            if session.session_binding.fingerprint != checkpoint["session_binding_fingerprint"]:
                raise ValueError("checkpoint runtime binding differs from the restored session")
            if session.position.runtime_ordinal != checkpoint["terminal_runtime_ordinal"]:
                raise ValueError("checkpoint terminal ordinal differs from the restored session")
            return cls(session=session,model_runtime=model_runtime,temporal_runtime=temporal_runtime,store=store,**kwargs)

    def _product(self, *, source_id=None, execution_id=None):
        default = {"version": PRODUCT_KEY,"owner_ref": self.owner_ref,"user_ref": self.user_ref,
                   "sources": {},"executions": {}}
        raw = self.runtime.session.profile.get(PRODUCT_KEY,default)
        # Copy before writing only the requested rows. Without an edit key,
        # callers inspect existing rows read-only; the outer tables stay private.
        product = dict(raw)
        for bucket, key in (("sources", source_id), ("executions", execution_id)):
            product[bucket] = dict(raw[bucket])
            if key is not None and key in raw[bucket]:
                product[bucket][key] = thaw_model_data(raw[bucket][key])
        return product

    def _validate_product(self):
        # Reuse validation only within this locked pass. Every source is fully
        # checked; other journal views must still match its complete event.
        with self._lock:
            product = self._product()
            if (not isinstance(product,dict) or set(product) != {"version","owner_ref","user_ref","sources","executions"}
                    or product["version"] != PRODUCT_KEY or product["owner_ref"] != self.owner_ref
                    or product["user_ref"] != self.user_ref
                    or not isinstance(product["sources"],dict) or not isinstance(product["executions"],dict)):
                raise ValueError("product identity or journal schema is invalid")
            verified_events = {}
            for event_id, source in product["sources"].items():
                event = PetEvent.from_record(source["event"])
                if event_id != event.event_id:
                    raise ValueError("source journal identity differs from event")
                self._verify_source(event)
                verified_events[event_id] = event.to_record()
            for event in self.runtime.events:
                if verified_events.get(event.event_id) != event.to_record():
                    raise ValueError("event source differs from the validated source journal")
            for action_id, execution in product["executions"].items():
                if execution.get("status") not in {"started","uncertain","executed"}:
                    raise ValueError("execution journal contains an invalid status")
                if execution.get("status") == "executed":
                    source = product["sources"].get(action_id)
                    if source is None:
                        raise ValueError("executed action source is missing")
                    if verified_events.get(action_id) != thaw_model_data(source["event"]):
                        raise ValueError("executed action event differs from the validated source")

            verify_commits(self,product)

    def _verify_source(self,event):
        # The final caller still checks the current source. Reuse only the exact
        # factory-owned bytes/objects already verified by this application.
        from framework.model_api.immutable_record import ImmutableModelRecord
        from collections import OrderedDict
        raw=self.runtime.session.profile.get(PRODUCT_KEY,{})
        row=raw.get('sources',{}).get(event.event_id)
        execution=raw.get('executions',{}).get(event.event_id)
        owned=(type(row) is ImmutableModelRecord
            and (execution is None or type(execution) is ImmutableModelRecord))
        cache=getattr(self,'_verified_sources',None)
        if cache is None:cache=OrderedDict();self._verified_sources=cache
        key=(self.owner_ref,self.user_ref,self.runtime.session.session_binding.fingerprint,
            self.runtime.temporal_runtime.binding)
        # Temporal binding contains mappings and is used as a value, not a hash key.
        current=event.to_record()
        prior=cache.get(event.event_id) if owned else None
        if (prior is not None and prior[0] is row and prior[1] is row._data
                and prior[2] is execution and prior[3] is (None if execution is None else execution._data)
                and prior[4]==key and prior[5]==current):
            cache.move_to_end(event.event_id)
            # The immutable receipt certificate is reusable; a referenced disk
            # artifact is not immutable Python data. Verify that external body
            # at its use boundary while retaining the expensive receipt proof.
            receipt=row.get('evidence',{}).get('input_source',{})
            if receipt.get('kind')=='web_page':
                from .external_input import validate
                validate(self,receipt,event.contents,event_id=event.event_id)
            return
        self._verify_source_uncached(event)
        if owned:
            cache[event.event_id]=(row,row._data,execution,
                None if execution is None else execution._data,key,current)
            cache.move_to_end(event.event_id)
            while len(cache)>128:cache.popitem(last=False)

    def _verify_source_uncached(self,event):
        # This verifier consumes one source, not a mutable copy of the entire journal.
        raw = self.runtime.session.profile.get(PRODUCT_KEY,{})
        imported=raw.get('sources',{}).get(event.event_id)
        if imported is not None and imported.get('origin')=='transcript':
            from .imported_source import verify
            verify(self,event,imported)
            return
        product = {"sources": {event.event_id: thaw_model_data(raw.get("sources",{}).get(event.event_id))},
                   "executions": {event.event_id: thaw_model_data(raw.get("executions",{}).get(event.event_id))}}
        source = product["sources"].get(event.event_id)
        if not isinstance(source,Mapping) or source.get("event") != event.to_record():
            raise ValueError("event source was not durably accepted by this product")
        expected_position = self.runtime.temporal_runtime.normalize_position(event.position.coordinate)
        if event.owner_ref != self.owner_ref or expected_position != event.position:
            raise ValueError("event source owner or temporal binding is invalid")
        if source["origin"] == "input":
            raw = source["evidence"]
            version = raw.get("version")
            from .external_input import validate as validate_external,INPUT_VERSION
            if version not in {INPUT_VERSION,"desktop-pet-received-input-v1","desktop-pet-received-input-v2",*ADMISSION_VERSIONS}:
                raise ValueError("received source version is invalid")
            expected_raw = {"version":version,"event_id":event.event_id,
                "scope_ref":event.scope_ref,"actor_ref":event.actor_ref,"owner_ref":event.owner_ref,
                "position":event.position.to_record(),"contents":[item.to_record() for item in event.contents]}
            if version in {INPUT_VERSION,"desktop-pet-received-input-v2",*ADMISSION_VERSIONS}:
                if raw.get("response_policy") not in {"required","eligible"}:
                    raise ValueError("received source reply policy is invalid")
                expected_raw["response_policy"] = raw["response_policy"]
            if version in ADMISSION_VERSIONS:
                captured=self.runtime.temporal_runtime.normalize_position(raw['capture_position']['coordinate'])
                if captured.to_record()!=raw['capture_position']:raise ValueError('capture position binding differs')
                self.runtime.temporal_runtime.prove_order(captured,event.position)
                if (not event.contents or (version==ADMISSION_VERSION and any(c.kind!='interaction' or not c.parameters.get('trajectory') or c.parameters['trajectory'][-1][2]!=captured.coordinate for c in event.contents)) or (version==POINT_ADMISSION_VERSION and any(c.kind=='interaction' for c in event.contents))):
                    raise ValueError('admitted source differs from captured physical end')
                expected_raw['capture_position']=captured.to_record()
            expected_actor=self.user_ref
            if version==INPUT_VERSION:
                external=validate_external(self,raw['input_source'],event.contents,event_id=event.event_id)
                from .external_input import READING_SOURCE_VERSION,allowed_policies
                if raw['response_policy'] not in allowed_policies(external):raise ValueError('external source reply policy differs')
                if external['version']==READING_SOURCE_VERSION and external['reading_checkpoint']['coordinate']!=event.position.coordinate:raise ValueError('reading source capture time differs')
                from .participant_input import VERSIONS as PARTICIPANT_VERSIONS
                if external['version'] in PARTICIPANT_VERSIONS and external['received_at_ms']!=event.position.coordinate:raise ValueError('participant received time differs')
                expected_raw['input_source']=thaw_model_data(external);expected_actor=external['actor_ref']
            if (raw != expected_raw or model_data_fingerprint(raw) != event.source_receipt_ref
                    or event.source_ref != "received:"+event.event_id or event.target_refs != (self.owner_ref,)
                    or event.event_kind != "peer_input" or event.actor_ref != expected_actor):
                raise ValueError("received input source evidence is invalid")
        elif source["origin"] == "execution":
            evidence = source["evidence"]
            fact = CommittedFact.from_record(evidence["fact"])
            receipt = evidence["commit_receipt"]
            material = {key:value for key,value in receipt.items() if key != "fingerprint"}
            if (model_data_fingerprint(material) != receipt["fingerprint"]
                    or receipt["fingerprint"] != event.source_receipt_ref
                    or receipt["committed_fact_ref"] != event.event_id
                    or receipt["committed_fact_fingerprint"] != fact.fingerprint
                    or fact.session_binding_fingerprint != self.runtime.session.session_binding.fingerprint
                    or event.event_kind != "npc_output" or event.actor_ref != self.owner_ref):
                raise ValueError("execution source evidence is invalid")
            entry = product["executions"].get(event.event_id)
            if entry is None or entry.get("status") != "executed":
                raise ValueError("execution source lacks a completed execution receipt")
            actual = fact.model_payload["execution_receipt"]
            actual_material = {key:value for key,value in actual.items() if key != "fingerprint"}
            expected_time = self.runtime.temporal_runtime.normalize_position(entry["coordinate"])
            destination=entry.get('participant_destination')
            if destination is not None:
                from .destinations import validate as validate_destination
                validate_destination(self,destination)
            target=self.user_ref if destination is None else destination['primary']
            audience=(self.user_ref,) if destination is None else tuple(destination['audience'])
            visibility=actual.get('visibility_policy')
            if visibility not in (None,'desktop-pet-owner-private-fact-v1'):raise ValueError('unknown execution visibility policy')
            fact_audience={self.owner_ref} if visibility is not None else {self.owner_ref,*audience}
            if (model_data_fingerprint(actual_material) != actual["fingerprint"]
                    or actual["action_id"] != event.event_id or fact.fact_id != event.event_id
                    or actual["candidate_ref"] != entry["candidate_ref"]
                    or event.source_ref != actual["candidate_ref"] or fact.source_ref != event.source_ref
                    or fact.scope_ref != event.scope_ref or event.target_refs != (target,)
                    or set(fact.visible_to_refs) != fact_audience
                    or event.position != expected_time
                    or thaw_model_data(actual["occurred_at_position"]) != event.position.to_record()
                    or len(actual["outcome"]["completed_content_ids"]) != len(event.contents)
                    or set(actual["outcome"]["completed_content_ids"]) != {item.content_id for item in event.contents}):
                raise ValueError("execution source differs from its actual execution evidence")
            delivered = actual["completed_contents"]
            if delivered != tuple(content.to_record() for content in event.contents):
                # History freezes sequences; compare opaque data through its canonical fingerprint.
                if model_data_fingerprint(delivered) != model_data_fingerprint([item.to_record() for item in event.contents]):
                    raise ValueError("execution event contents differ from actual completion")
        else:
            raise ValueError("source is not a received input or real execution")

    def _publish_product(self,product,*,expression_state=None):
        profile = dict(self.runtime.session.profile)
        profile[PRODUCT_KEY] = product
        if expression_state is not None:
            profile[RUNTIME_EXPRESSION_STATE_KEY] = expression_state
        updated = replace(self.runtime.session,profile=profile)
        publish_session(self,updated)

    def _require_ready(self):
        product = self.runtime.session.profile.get(PRODUCT_KEY,{"sources":{},"executions":{}})
        committed = self.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
        if any(event_id not in committed for event_id in product["sources"]):
            raise RuntimeError("pending source must be settled before accepting another event")
        if any(entry["status"] != "executed" for entry in product["executions"].values()):
            raise ExecutionUncertain("pending external execution needs reconciliation")

    def _settle(self,event,measure):
        if event.event_id in self.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
            return IngestResult(self.runtime.memory_instance(event.event_id),None,True)
        product = self._product(source_id=event.event_id)
        source = product["sources"][event.event_id]
        if event.event_kind == "npc_output" and source['origin']!='transcript':
            # The execution fact is already durable; its later psychological
            # settlement must not delay another Actor hearing the actual speech.
            self._verify_source(event)
            self.on_execution_committed(event)
        measurement = source.get("measurement")
        needs_cache = "measurement" not in source
        if needs_cache:
            measurement = measure(event)
            if not isinstance(measurement,Mapping):
                raise ValueError("semantic provider did not return a measurement mapping")
        shadow = self.runtime.fork()
        policy = source["evidence"].get("response_policy") if source["origin"] == "input" else None
        result = shadow.ingest(event,measurement_payload=measurement,response_policy=policy)
        live_products=[]
        if policy is not None:
            shadow.session = attach_input_continuation(application=self,event=event,
                transaction=result.transaction,session=shadow.session,source=source,_products=live_products)
        from product_adapters.desktop_pet_v1.settlement_view import attach as attach_settlement_view
        from model_packages.desktop_pet_v1.reaction import results_for
        if event.event_kind=='npc_output' and source['origin']!='transcript':
            actual=source['evidence']['fact']['model_payload']['execution_receipt']
            reaction_results=actual.get('reaction_results') or results_for(event.contents,
                actual_ids=actual['outcome']['completed_content_ids'],
                child_results=actual['outcome']['details'].get('child_results'))
        else:reaction_results=results_for((),actual_ids=())
        transaction=result.transaction;settled=transaction.event_transaction
        shadow.session=attach_settlement_view(shadow.session,
            previous_state=transaction.event_input_session.state,previous_position=transaction.settlement_input_position,
            context=settled.context,execution=settled.settlement_execution,acceptance=settled.acceptance,
            source_kind='event',source_ref=event.event_id,reaction_results=reaction_results)
        if needs_cache:
            # Invalid measurements never become permanent retry inputs. This pure
            # transaction has not yet published any live state or checkpoint.
            source["measurement"] = thaw_model_data(measurement)
            self._publish_product(product)
            profile = dict(shadow.session.profile)
            profile[PRODUCT_KEY] = self.runtime.session.profile[PRODUCT_KEY]
            shadow.session = replace(shadow.session,profile=profile)
        # The result accompanies this one formal event; holds are not extra effects.
        from product_adapters.desktop_pet_v1.settlement_view import KEY as SETTLEMENT_KEY
        source['reaction']=thaw_model_data(shadow.session.profile[SETTLEMENT_KEY]['reaction'])
        if event.event_kind=='npc_output' and source['origin']!='transcript':
            from product_adapters.desktop_pet_v1.settlement_audit import build as settlement_audit
            source['settlement_audit']=settlement_audit(event=event,settled=settled,fact=result.memory.fact)
        profile=dict(shadow.session.profile);profile[PRODUCT_KEY]=product
        shadow.session=replace(shadow.session,profile=profile)
        shadow.session=publish_session(self,shadow.session)
        # Publish this read-only shortcut only after the formal commit succeeded.
        self._live_input_products=(event.event_id,live_products[0]) if live_products else None
        from model_packages.desktop_pet_v1.declaration_sources import register_committed
        register_committed(self.runtime,event,measurement)
        return result

    def receive(self, *, event_id,scope_ref,actor_ref,contents,coordinate,measure,response_policy="required",admission_coordinate=None,admission_version=ADMISSION_VERSION,input_source=None):
        with self._lock:
            from .external_input import validate as validate_external,INPUT_VERSION
            saved=self._product(source_id=event_id)['sources'].get(event_id)
            if input_source is None and saved is not None:input_source=saved['evidence'].get('input_source')
            if input_source is not None:
                from .external_input import READING_SOURCE_VERSION,allowed_policies
                if admission_coordinate is not None or response_policy not in allowed_policies(input_source):raise ValueError('external observation requires explicit input policy')
                validate_external(self,input_source,contents,event_id=event_id)
                if input_source.get('version')==READING_SOURCE_VERSION and input_source['reading_checkpoint']['coordinate']!=coordinate:raise ValueError('reading capture time differs')
                from .participant_input import VERSIONS as PARTICIPANT_VERSIONS
                if input_source['version'] in PARTICIPANT_VERSIONS and input_source['received_at_ms']!=coordinate:raise ValueError('participant received time differs')
                if actor_ref!=input_source['actor_ref']:raise ValueError('external input actor differs')
            if actor_ref != self.user_ref and input_source is None:
                raise ValueError("received input actor is not the configured user")
            product = self._product(source_id=event_id)
            existing = product["sources"].get(event_id)
            legacy = response_policy is None
            if legacy:
                if (existing is None or existing.get("origin") != "input"
                        or existing["evidence"].get("version") != "desktop-pet-received-input-v1"):
                    raise ValueError("policy-free receive only resumes an exact legacy source")
            elif response_policy not in {"required","eligible"}:
                raise ValueError("received source reply policy is invalid")
            captured = self.runtime.temporal_runtime.normalize_position(coordinate)
            if existing is not None:
                admission_coordinate=(existing['event']['position']['coordinate'] if existing['evidence'].get('version') in ADMISSION_VERSIONS else None)
                if admission_coordinate is not None:admission_version=existing['evidence']['version']
            if admission_coordinate is not None and legacy:raise ValueError('admission requires explicit input policy')
            position = self.runtime.temporal_runtime.normalize_position(coordinate if admission_coordinate is None else admission_coordinate)
            if admission_coordinate is not None:
                self.runtime.temporal_runtime.prove_order(captured,position)
                if admission_version not in ADMISSION_VERSIONS:raise ValueError('input admission version is invalid')
                if (not contents or (admission_version==ADMISSION_VERSION and any(c.kind!='interaction' or not c.parameters.get('trajectory') or c.parameters['trajectory'][-1][2]!=captured.coordinate for c in contents)) or (admission_version==POINT_ADMISSION_VERSION and any(c.kind=='interaction' for c in contents))):
                    raise ValueError('admission requires an actual captured interval')
                if existing is None:self.runtime.temporal_runtime.prove_order(self.runtime.session.position.position,position)
            raw = {"version":admission_version if admission_coordinate is not None else "desktop-pet-received-input-v1" if legacy else "desktop-pet-received-input-v2","event_id":event_id,"scope_ref":scope_ref,
                "actor_ref":actor_ref,"owner_ref":self.owner_ref,"position":position.to_record(),
                "contents":[item.to_record() for item in contents]}
            if input_source is not None:
                raw['version']=INPUT_VERSION;raw['input_source']=thaw_model_data(input_source)
            if not legacy:
                raw["response_policy"] = response_policy
            if admission_coordinate is not None:raw['capture_position']=captured.to_record()
            event = PetEvent(event_id=event_id,source_ref="received:"+event_id,
                source_receipt_ref=model_data_fingerprint(raw),owner_ref=self.owner_ref,actor_ref=actor_ref,
                target_refs=(self.owner_ref,),scope_ref=scope_ref,position=position,event_kind="peer_input",contents=contents)
            product = self._product(source_id=event_id)
            existing = product["sources"].get(event_id)
            if existing is not None:
                if existing["event"] != event.to_record():
                    raise ValueError("received source identity conflicts with an earlier input")
            else:
                self._require_ready()
                product["sources"][event_id] = {"origin":"input","event":event.to_record(),"evidence":raw}
                self._publish_product(product)
            return self._settle(event,measure)

    def execute(self,prepared:PreparedExecution,*,executor,coordinate,measure,_recovered_outcome=None):
        with self._lock:
            session = self.runtime.session
            plan,contents = prepared.validate(binding=session.session_binding,owner_ref=self.owner_ref,user_ref=prepared.authorization.target_ref)
            product = self._product(source_id=plan.action_id,execution_id=plan.action_id)
            existing = product["executions"].get(plan.action_id)
            if existing is not None:
                if existing["prepared_fingerprint"] != prepared.fingerprint:
                    raise ValueError("execution identity conflicts with a previous candidate")
                if existing["status"] != "executed":
                    if _recovered_outcome is None:raise ExecutionUncertain("execution needs reconciliation before any replay")
                else:
                    event = PetEvent.from_record(product["sources"][plan.action_id]["event"])
                    return self._settle(event,measure)
            if _recovered_outcome is None:self._require_ready()
            else:
                if existing is None or 'composite' not in prepared.request.control:
                    raise ValueError('recovery requires existing composite execution')
                full=self._product();committed_ids=self.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
                if any(key not in committed_ids for key in full['sources']) or any(
                        key!=plan.action_id and row['status']!='executed' for key,row in full['executions'].items()):
                    raise ExecutionUncertain('other pending facts prevent recovery')
            if prepared.position != session.position:
                raise ValueError("candidate uses a stale runtime position")
            destination=prepared.request.readonly_facts.get('participant_destination')
            if destination is not None:
                from .destinations import destination as expected_destination
                if model_data_fingerprint(expected_destination(self,prepared.authorization.source_fact_ref))!=model_data_fingerprint(destination):raise ValueError('execution destination differs from source')
            idle=prepared.authorization.host_authorization_evidence.get('origin')=='dynamic_idle_tick'
            if idle:
                from .idle_opening import authorize,source as idle_source
                source,_grant=idle_source(self,prepared.authorization.source_fact_ref,current=True)
                if authorize(self,source.tick_id)!=prepared.authorization or prepared.scope_ref!=prepared.authorization.host_authorization_evidence['scope_ref']:
                    raise ValueError('idle source lacks its actual Tick grant or destination')
            else:source=self.runtime.memory_instance(prepared.authorization.source_fact_ref)
            deferred=prepared.authorization.host_authorization_evidence.get('origin')=='deferred_direct_reply_v1'
            if deferred:
                from product_adapters.desktop_pet_v1.deferred_reply import authorize
                if authorize(self,source.event.event_id)!=prepared.authorization or prepared.scope_ref!=source.event.scope_ref:
                    raise ValueError('deferred reply differs from its required input or current planning state')
            from .external_input import INPUT_VERSION
            expected_actor=(self._product()['sources'][source.event.event_id]['evidence'].get('input_source',{}).get('actor_ref')
                if self._product()['sources'][source.event.event_id]['evidence'].get('version')==INPUT_VERSION else self.user_ref) if not idle else self.user_ref
            if not idle and not deferred and (source.event.event_kind != "peer_input" or source.event.actor_ref != expected_actor
                    or prepared.scope_ref != source.event.scope_ref
                    or source.fact.accepted_runtime_ordinal != prepared.authorization.source_runtime_ordinal
                    or source.fact.temporal_position_fingerprint != prepared.authorization.source_position_fingerprint
                    or source.fact.settlement_ref["acceptance_fingerprint"] != prepared.authorization.source_settlement_fingerprint):
                raise ValueError("direct reply source differs from its formal input")
            if prepared.authorization.host_authorization_evidence.get("origin")=="eligible_peer_reply_v1":
                from .peer_reaction import authorize as authorize_peer
                if authorize_peer(self,source.event.event_id)!=prepared.authorization:raise ValueError("peer source lacks its current assessment")
            if prepared.authorization.action_kind=="autonomous_opening" and not idle:
                from .interaction_observer import authorize_interaction_tick
                if authorize_interaction_tick(self,source.event.event_id)!=prepared.authorization:raise ValueError("autonomous source lacks its actual Tick grant")
            if self.action_verifier(prepared,source) is not None:
                raise ValueError("action verifier must validate successfully or raise")
            completion_clock = coordinate if callable(coordinate) else None
            actual_coordinate = None if completion_clock is not None else coordinate
            if completion_clock is None:
                position = self.runtime.temporal_runtime.normalize_position(actual_coordinate)
                self.runtime.temporal_runtime.prove_order(session.position.position,position)
            entry = {"status":"started","prepared_fingerprint":prepared.fingerprint,
                     "coordinate":actual_coordinate,"candidate_ref":prepared.candidate.candidate_ref}
            product["executions"][plan.action_id] = entry
            self._publish_product(product)
            try:
                outcome = _recovered_outcome if _recovered_outcome is not None else executor(plan.action_id,contents)
                if not isinstance(outcome,ExecutionOutcome):
                    raise ValueError("executor returned no verified outcome")
                composite=prepared.composite_plan()
                if composite is not None:
                    from model_packages.desktop_pet_v1.composite import validate_outcome
                    validate_outcome(composite,outcome)
                realization_evidence=thaw_model_data(prepared.candidate.realization_evidence)
                delivery_prepared=prepared
                if 'late_speech' in prepared.candidate.candidate_payload:
                    delivery_prepared=None
                    if 'expression_delivery' in realization_evidence:
                        raise ValueError('staged parent cannot preclaim speech delivery')
                    slot=prepared.candidate.candidate_payload['late_speech']['content_id']
                    private_staged=prepared.request.control.get('staged_version')=='desktop-pet-staged-v2-private-thought'
                    if private_staged:
                        slots={'speech':slot,'thought':prepared.candidate.candidate_payload['late_speech']['thought_content_id']}
                        if set(slots.values()).intersection(outcome.completed_content_ids):
                            from product_adapters.desktop_pet_v1.composite_writer import verify_expression_children
                            evidence=outcome.details.get('expression_children_evidence')
                            if not isinstance(evidence,Mapping):raise ValueError('actual expression lacks sealed children evidence')
                            children,delivery_prepared=verify_expression_children(parent=prepared,evidence=evidence,
                                binding=session.session_binding,owner_ref=self.owner_ref,user_ref=prepared.authorization.target_ref,child_ids=slots)
                            contents=(*contents,*children)
                            realization_evidence['expression_children_evidence']=thaw_model_data(evidence)
                            realization_evidence['expression_realizer_version']=delivery_prepared.request.control['version']
                            child_delivery=delivery_prepared.candidate.realization_evidence.get('expression_delivery')
                            if child_delivery is not None:realization_evidence['expression_delivery']=thaw_model_data(child_delivery)
                    elif slot in outcome.completed_content_ids:
                        from product_adapters.desktop_pet_v1.composite_writer import verify_speech_child
                        evidence=outcome.details.get('speech_child_evidence')
                        if not isinstance(evidence,Mapping):raise ValueError('actual speech lacks sealed child evidence')
                        speech,delivery_prepared=verify_speech_child(parent=prepared,evidence=evidence,
                            binding=session.session_binding,owner_ref=self.owner_ref,user_ref=prepared.authorization.target_ref,child_id=slot)
                        contents=(*contents,speech)
                        realization_evidence['speech_child_evidence']=thaw_model_data(evidence)
                        child_delivery=delivery_prepared.candidate.realization_evidence.get('expression_delivery')
                        if child_delivery is not None:realization_evidence['expression_delivery']=thaw_model_data(child_delivery)
                from model_packages.desktop_pet_v1.behavior import realized_contents
                delivered = realized_contents(contents, outcome)
                if not delivered or set(outcome.completed_content_ids) != {item.content_id for item in delivered}:
                    raise ValueError("executor returned invalid completed content identities")
                if prepared.request.control.get('action_sequence_contract'):
                    # Candidate evidence stays frozen. This receipt separately
                    # states which language channels actually became a fact.
                    realization_evidence['sequence_actual_channels']=[c.kind for c in delivered if c.kind in {'speech','thought'}]
                if completion_clock is not None:
                    actual_coordinate=(outcome.details["occurred_at_wall_ms"] if "occurred_at_wall_ms" in outcome.details else completion_clock())
                    position = self.runtime.temporal_runtime.normalize_position(actual_coordinate)
                    self.runtime.temporal_runtime.prove_order(session.position.position,position)
            except ExecutionCancelled:
                # A typed executor result confirms zero effect, distinct from uncertainty.
                product=self._product(source_id=plan.action_id,execution_id=plan.action_id);product["executions"].pop(plan.action_id)
                profile=dict(self.runtime.session.profile)
                cancelled=list(profile.get("desktop_pet_cancelled_executions_v1",()))
                cancelled.append({"action_id":plan.action_id,"candidate_ref":prepared.candidate.candidate_ref,
                    "prepared_fingerprint":prepared.fingerprint,"reason":"cancelled_before_any_effect"})
                profile["desktop_pet_cancelled_executions_v1"]=cancelled;profile[PRODUCT_KEY]=product
                updated=replace(self.runtime.session,profile=profile);publish_session(self,updated)
                raise
            except Exception as error:
                product = self._product(source_id=plan.action_id,execution_id=plan.action_id)
                product["executions"][plan.action_id]["status"] = "uncertain"
                product["executions"][plan.action_id]["failure_kind"] = type(error).__name__
                try:
                    self._publish_product(product)
                except OSError:
                    pass  # The durable started record already prohibits repeating external effects.
                raise ExecutionUncertain("external execution result is uncertain") from error
            if destination is not None:realization_evidence['participant_destination']=thaw_model_data(destination)
            execution_receipt = {"version":"desktop-pet-execution-v1","action_id":plan.action_id,
                "candidate_ref":prepared.candidate.candidate_ref,"outcome":outcome.to_record(),
                "occurred_at_position":position.to_record(),"completed_contents":[item.to_record() for item in delivered],
                "realization_evidence":realization_evidence}
            private_fact=prepared.request.control.get('allow_thought') is True
            if private_fact:execution_receipt['visibility_policy']='desktop-pet-owner-private-fact-v1'
            from model_packages.desktop_pet_v1.reaction import results_for
            execution_receipt['reaction_results']=results_for(contents,
                actual_ids=outcome.completed_content_ids,child_results=outcome.details.get('child_results'),
                declarations=None if composite is None else composite.children)
            execution_receipt["fingerprint"] = model_data_fingerprint(execution_receipt)
            committed = execute_action_commit_lifecycle(
                history=HistoryAuthority(session_binding_fingerprint=session.session_binding.fingerprint),
                session_binding=session.session_binding,current_position=prepared.position,
                authorization=prepared.authorization,realizer_request=prepared.request,realizer_attempt=prepared.attempt,
                candidate=prepared.candidate,scope_ref=prepared.scope_ref,visible_to_refs=(self.owner_ref,) if private_fact else (self.owner_ref,*(destination['audience'] if destination is not None else (self.user_ref,))),
                model_payload={"execution_receipt":execution_receipt},
                authorization_receipt_ref=prepared.authorization.fingerprint,
                realizer_request_receipt_ref=prepared.request.fingerprint,
            )
            event = PetEvent(event_id=plan.action_id,source_ref=prepared.candidate.candidate_ref,
                source_receipt_ref=committed.commit_receipt.fingerprint,owner_ref=self.owner_ref,actor_ref=self.owner_ref,
                target_refs=(prepared.authorization.target_ref,),scope_ref=prepared.scope_ref,position=position,event_kind="npc_output",contents=delivered)
            product = self._product(source_id=plan.action_id,execution_id=plan.action_id)
            product["executions"][plan.action_id]["status"] = "executed"
            if destination is not None:product["executions"][plan.action_id]['participant_destination']=thaw_model_data(destination)
            product["executions"][plan.action_id]["coordinate"] = actual_coordinate
            product["sources"][plan.action_id] = {"origin":"execution","event":event.to_record(),
                "evidence":{"fact":committed.committed_fact.to_record(),"commit_receipt":committed.commit_receipt.to_record()}}
            expression_state,expression_commit = ((None,None) if delivery_prepared is None
                else commit_delivery(session,delivery_prepared,committed,delivered))
            if expression_commit is not None:
                product["sources"][plan.action_id]["expression_commit"] = expression_commit
            self._publish_product(product,expression_state=expression_state)
            return self._settle(event,measure)

    def close(self):
        self.store.close()

