"""Exact accepted input continuation; pure projections and required proof only."""
from __future__ import annotations
from petlab_app.destinations import primary as reply_destination
from dataclasses import replace,fields
from framework.runtime_kernel import RuntimeStateCommitAuthority

from framework.action_protocol import ActionAuthorization
from framework.model_api import model_data_fingerprint,thaw_model_data
from ops.runtime_expression_state import restore_runtime_expression_state
from ops.runtime_product_fact_bridge import RuntimeCommittedFactEvidence,PRODUCT_FACT_BRIDGE_VERSION
from ops.runtime_product_expression_continuation import (
    build_product_expression_continuation,attach_product_expression_continuation,
    load_product_expression_continuation,restore_product_expression_projection,restore_product_fact_products)
from ops.runtime_product_fact_lifecycle import complete_product_fact_lifecycle
from ops.runtime_product_action_authorization import authorize_required_product_reply


def attach_input_continuation(*,application,event,transaction,session,source,_products=None):
    application._verify_source(event)
    if event.event_kind!="peer_input" or source["origin"]!="input":
        raise ValueError("input expression requires an actual received source")
    policy=source["evidence"].get("response_policy")
    context=thaw_model_data(transaction.event_transaction.context.metadata)
    if policy not in {"required","eligible"} or context.get("response_policy")!=policy:
        raise ValueError("input expression reply policy differs from committed context")
    model=application.runtime.model_runtime
    temporal=application.runtime.temporal_runtime
    fields={"target_coordinate":event.position.coordinate,"conversation_id":event.scope_ref,
        "participant_id":event.owner_ref,"event_id":event.event_id,"action_id":None,
        "event_kind":event.event_kind,"response_policy":policy,
        "expected_runtime_ordinal":context["expected_runtime_ordinal"],
        "model_binding":model.binding.to_record(),"temporal_binding":temporal.binding.to_record()}
    material={"version":PRODUCT_FACT_BRIDGE_VERSION,**fields,
        "source_kind":"desktop-pet-received-input-v2","source_binding_ref":event.source_binding_ref,
        "source_receipt_ref":event.source_receipt_ref,
        "measurement_fingerprint":model_data_fingerprint(transaction.event_transaction.measurement_execution.payload),
        "authority":{"state_write":False,"send":False,"provider_call":False}}
    receipt={**material,"fingerprint":model_data_fingerprint(material)}
    evidence=RuntimeCommittedFactEvidence(**fields,context_metadata=context,
        measurement_payload=thaw_model_data(transaction.event_transaction.measurement_execution.payload),receipt=receipt)
    evidence.verify_for(model_runtime=model,temporal_runtime=temporal)
    anchor={"version":"desktop-pet-input-expression-anchor-v1","event_id":event.event_id,
        "action_id":None,"participant_id":event.owner_ref,"conversation_id":event.scope_ref,
        "session_binding_fingerprint":session.session_binding.fingerprint,
        "source_binding_ref":event.source_binding_ref,"transaction_receipt_fingerprint":model_data_fingerprint(transaction.receipt)}
    anchor["fingerprint"]=model_data_fingerprint(anchor)
    expression_state=restore_runtime_expression_state(session.profile,session_binding=session.session_binding)
    continuation=build_product_expression_continuation(transaction=transaction,evidence=evidence,
        continuation_id=event.event_id,anchor_receipt=anchor,committed_remainders=expression_state.committed_remainders)
    updated=attach_product_expression_continuation(session=session,continuation=continuation)
    if _products is not None:_products.append((evidence,transaction))
    return updated


class PetExpressionService:
    def __init__(self,*,application):
        self.application=application

    def project(self,event_id,*,historical=False):
        app=self.application
        with app._lock:
            app._require_ready()
            memory=app.runtime.memory_instance(event_id)
            app._verify_source(memory.event)
            if memory.event.event_kind!="peer_input":
                raise ValueError("direct expression source must be a received input")
            session=app.runtime.session
            continuation=load_product_expression_continuation(session.profile,delivery_id=event_id,
                session_binding=session.session_binding)
            # Source bytes and the current continuation are still checked above.
            # Reuse only the exact locally validated immutable inputs; a changed
            # state, position, source, binding or restored continuation misses.
            reuse_key=(event_id,session.session_binding,session.state,session.position,
                session.parameters,continuation,app.runtime.model_runtime,
                app.runtime.temporal_runtime,app._product()['sources'][event_id],
                app.runtime.model_runtime.manifest,app.runtime.temporal_runtime.manifest,
                getattr(app,'_live_input_products',None),
                complete_product_fact_lifecycle,restore_product_expression_projection)
            saved=None if historical else getattr(app,'_last_input_lifecycle',None)
            if (saved is not None and saved[0][0]==event_id
                    and all(a is b for a,b in zip(saved[0][1:],reuse_key[1:]))):
                if (thaw_model_data(saved[1].evidence.model_binding)!=app.runtime.model_runtime.binding.to_record()
                        or thaw_model_data(saved[1].evidence.temporal_binding)!=app.runtime.temporal_runtime.binding.to_record()):
                    raise ValueError('cached input projection binding differs')
                return saved[1]
            if historical:
                authority=RuntimeStateCommitAuthority(session_binding=session.session_binding)
                accepted=continuation.settlement_acceptance
                session=replace(session,state=authority.restore_state(accepted['state']),
                    position=authority.restore_position_state(accepted['position']))
            live=None if historical else getattr(app,'_live_input_products',None)
            fresh=False
            if live is not None and live[0]==event_id:
                evidence,transaction=live[1]
                accepted=transaction.event_transaction.acceptance
                if (session.state is accepted.state and session.position is accepted.position
                        and session.parameters is transaction.session.parameters):
                    current={'evidence':{f.name:getattr(evidence,f.name) for f in fields(evidence)},
                        'receipt':transaction.receipt,'context':transaction.event_transaction.context.to_record(),
                        'execution':{'payload':transaction.event_transaction.settlement_execution.payload,
                            'receipt':transaction.event_transaction.settlement_execution.receipt.to_record()},
                        'acceptance':accepted.to_record(),
                        'previous_state':transaction.event_input_session.state.to_record(),
                        'previous_position':transaction.settlement_input_position.to_record()}
                    expected={'evidence':continuation.fact_evidence,'receipt':continuation.transaction_receipt,
                        'context':continuation.settlement_context,'execution':continuation.settlement_execution,
                        'acceptance':continuation.settlement_acceptance,'previous_state':continuation.previous_state,
                        'previous_position':continuation.previous_position}
                    fresh=model_data_fingerprint(current)==model_data_fingerprint(expected)
            if not fresh:
                evidence,transaction=restore_product_fact_products(continuation=continuation,session=session,
                    runtime=app.runtime.model_runtime,temporal_runtime=app.runtime.temporal_runtime)
            else:
                # Attachment already verified this exact fact and all its fields
                # still match the stored continuation. Only the live runtime
                # identity can have changed since then; do not reparse the proof.
                if (thaw_model_data(evidence.model_binding)!=app.runtime.model_runtime.binding.to_record()
                        or thaw_model_data(evidence.temporal_binding)!=app.runtime.temporal_runtime.binding.to_record()):
                    raise ValueError("verified fact binding differs from current runtime")
            if (evidence.receipt.get("source_binding_ref")!=memory.event.source_binding_ref
                    or evidence.response_policy!=app._product()["sources"][event_id]["evidence"].get("response_policy")
                    or transaction.event_transaction.acceptance.fingerprint!=memory.fact.settlement_ref["acceptance_fingerprint"]):
                raise ValueError("expression continuation differs from actual committed input source")
            def project():
                if not fresh:
                    return restore_product_expression_projection(continuation=continuation,session=session,
                        runtime=app.runtime.model_runtime)
                # This is the existing native projection on the live accepted
                # values, rather than a simulated process restart of those values.
                from ops.runtime_product_expression_continuation import produce_expression_projection_shadow
                event=transaction.event_transaction
                return produce_expression_projection_shadow(runtime=app.runtime.model_runtime,
                    session_binding=session.session_binding,previous_state=transaction.event_input_session.state,
                    previous_position=transaction.settlement_input_position,model_parameters=session.parameters,
                    settlement_context=event.context,settlement_execution=event.settlement_execution,
                    settlement_acceptance=event.acceptance,committed_remainders=continuation.committed_remainders)
            from .projection_cache import reuse
            expression=reuse(app,previous_state=continuation.previous_state,
                previous_position=continuation.previous_position,parameters=session.parameters,
                context=continuation.settlement_context,execution=continuation.settlement_execution,
                acceptance=continuation.settlement_acceptance,remainders=continuation.committed_remainders,
                factory=project)
            result=complete_product_fact_lifecycle(evidence=evidence,transaction=transaction,expression=expression)
            if not historical:app._last_input_lifecycle=(reuse_key,result)
            return result

    def project_for_reply(self,event_id,*,historical=False):
        """One projection supplies packet and required authorization together."""
        app=self.application
        with app._lock:
            lifecycle=self.project(event_id,historical=historical)
            authorization=(self._authorize_projected_reply(event_id,lifecycle)
                if not historical and lifecycle.evidence.response_policy=="required" else None)
            return lifecycle,authorization

    def authorize_reply(self,event_id):
        app=self.application
        with app._lock:
            return self._authorize_projected_reply(event_id,self.project(event_id))

    def _authorize_projected_reply(self,event_id,lifecycle):
        app=self.application
        with app._lock:
            proof=authorize_required_product_reply(lifecycle)
            proof.verify_integrity(session_binding=app.runtime.session.session_binding,
                current_position=app.runtime.session.position)
            # Map the existing required lifecycle proof into the neutral action
            # protocol. No conversation router, Writer, tick, or executor runs.
            return ActionAuthorization.create(session_binding=app.runtime.session.session_binding,
                current_position=app.runtime.session.position,action_kind="direct_reply",
                subject_ref=app.owner_ref,source_fact_ref=event_id,
                source_settlement_fingerprint=lifecycle.transaction.event_transaction.acceptance.fingerprint,
                target_ref=reply_destination(app,event_id),
                package_eligibility_evidence={"version":"desktop-pet-required-source-v1",
                    "response_policy":lifecycle.evidence.response_policy,
                    "source_binding_ref":lifecycle.evidence.receipt["source_binding_ref"]},
                host_authorization_evidence={"origin":"direct_reply","required_proof_fingerprint":proof.fingerprint,
                    "lifecycle_receipt_fingerprint":lifecycle.receipt["fingerprint"]})
