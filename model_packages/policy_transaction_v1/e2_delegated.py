"""Isolated E2 operator: provider owns decisions; package only validates/binds.

This package never invokes a provider. The public composition host supplies a
receipted decision as operator input. The default package manifest is unchanged.
"""
from framework.model_api import InteractionModelManifest, model_data_fingerprint
from . import (
    PolicyTransactionV1Package, _context, _exact, _fingerprinted,
    _validated_parameters, _validated_settlement,
    _validated_shared_projection,
)
from .memory import validate_experience_aggregate
from .e2_recall import E2RecallInfluencePolicyTransactionPackage
from .policy_gate import POLICY_GATE_VERSION, TOOL_DESCRIPTOR_VERSION, validate_policy_gate, validate_tool_descriptor
from .state_space import validate_state


class E2DelegatedPolicyTransactionPackage(E2RecallInfluencePolicyTransactionPackage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        material = self.manifest.to_record()
        material.pop("schema_fingerprint")
        material.pop("parameter_fingerprint")
        material["package_version"] += ".e2-delegated-v1"
        material["operator_versions"]["project_shared_semantics"] = "policy-transaction-delegated-shared-v1"
        material["operator_versions"]["assess_action"] = "policy-transaction-delegated-assessment-v1"
        material["operator_versions"]["project_semantic_resources"] = "policy-transaction-delegated-resources-v1"
        material['operator_versions']['recall_history'] = 'provider-selected-full-candidate-scope-v1'
        material['package_version'] += '.memory-choice-v1'
        material["shared_projection_schema"]["fields"].append("delegated_action_eligible")
        self.manifest = InteractionModelManifest.create(**material)

    def recall_history(self, *, payload, context):
        from .memory import validate_memory_recall
        invocation = _context(context)
        scope = payload['scope']
        if scope != {'session_id': invocation.session_id, 'task_scope_ref': invocation.scope_ref}:
            raise ValueError('delegated memory scope differs')
        candidates = list(payload['candidate_event_ids'])
        events = {row['event_id']: row for row in payload['candidate_events']}
        if len(candidates) != len(set(candidates)) or set(events) != set(candidates) or len(events) != len(payload['candidate_events']):
            raise ValueError('delegated candidate scope differs')
        choice = _exact(payload['delegated_memory_selection'], {'selected_event_ids'}, 'delegated memory selection')
        selected = list(choice['selected_event_ids'])
        if len(selected) != len(set(selected)) or not set(selected) <= set(candidates):
            raise ValueError('delegated memory selection names unknown or duplicate candidates')
        for row in events.values():
            if row['session_id'] != invocation.session_id or row['task_scope_ref'] != invocation.scope_ref or row['ordinal'] >= invocation.runtime_ordinal:
                raise ValueError('delegated candidate is not in the committed past scope')
        memory = validate_memory_recall(_fingerprinted({
            'version': 'e2-provider-memory-recall-v1', **scope,
            'selected_event_ids': selected,
            'selection_witnesses': [{'event_id': ref, 'source_kind': events[ref]['source_kind']} for ref in selected],
            'selection_rule': 'provider-selected-full-candidate-scope',
            'source_selection_receipt': payload['source_selection_receipt'], 'state_commit_authority': False}))
        evidence = {ref: {'selected': ref in selected, 'reason': 'provider_memory_selection'} for ref in candidates}
        return {'recall_decision': {'version': 'e2-provider-recall-decision-v1',
            'candidate_event_ids': candidates, 'recalled_event_ids': selected, 'candidate_evidence': evidence},
            'memory_recall': memory, 'memory_content_claims': []}

    def project_shared_semantics(self, *, payload, context):
        _context(context)
        record = _exact(payload, {"parameters", "settlement", "policy_contract",
                                  "experience_aggregate", "delegated_control"}, "delegated shared input")
        parameters = _validated_parameters(record["parameters"], package=self)
        settlement = _validated_settlement(record["settlement"], package=self)
        state = validate_state(settlement["state_candidate"], definition=self.state_space_definition)
        policy_fingerprint = model_data_fingerprint(record["policy_contract"])
        if parameters["policy_contract_digest"] != policy_fingerprint:
            raise ValueError("delegated policy differs from frozen parameters")
        aggregate = record["experience_aggregate"]
        witnesses = {}
        if aggregate is not None:
            aggregate = validate_experience_aggregate(aggregate)
            invocation = _context(context)
            if aggregate["session_id"] != invocation.session_id or aggregate["task_scope_ref"] != invocation.scope_ref:
                raise ValueError("delegated historical aggregate differs from scope")
            if aggregate["exact_tool_ids"] != parameters["exact_tool_ids"]:
                raise ValueError("delegated historical tools differ")
            witnesses = {row["tool_id"]: row for row in aggregate["experience_witnesses"]}
        reply = _exact(record["delegated_control"], {"tool_decisions", "action_eligible"}, "delegated control")
        if type(reply["action_eligible"]) is not bool:
            raise ValueError("delegated action eligibility must be boolean")
        gates, descriptors, seen = [], [], set()
        for raw in reply["tool_decisions"]:
            row = _exact(raw, {"tool_id", "decision", "recommendation", "missing_requirements",
                              "matched_blockers", "policy_source_refs"}, "delegated tool decision")
            if row["tool_id"] in seen:
                raise ValueError("delegated decision repeats tool ID")
            seen.add(row["tool_id"])
            gate = _fingerprinted({
                "version": POLICY_GATE_VERSION,
                **{key: value for key, value in row.items() if key != "recommendation"},
                "state_fingerprint": model_data_fingerprint(state),
                "policy_contract_fingerprint": policy_fingerprint,
            })
            gate = validate_policy_gate(gate)
            descriptor = validate_tool_descriptor(_fingerprinted({
                "version": TOOL_DESCRIPTOR_VERSION, **row,
                "policy_gate_fingerprint": gate["fingerprint"],
                "experience_witness": witnesses.get(row["tool_id"]),
                "text_response_required": True, "auto_execute": False,
                "action_commit_authority": False,
            }))
            gates.append(gate)
            descriptors.append(descriptor)
        if seen != set(parameters["exact_tool_ids"]):
            raise ValueError("delegated decisions must cover exact tool IDs")
        return {"shared_projection": _fingerprinted({
            "version": "policy-transaction-shared-projection-v2", "state": state,
            "policy_gates": gates, "canonical_tool_descriptors": descriptors,
            "text_response_required": True,
            "source_settlement_fingerprint": settlement["fingerprint"],
            "source_parameters_fingerprint": parameters["fingerprint"],
            "delegated_action_eligible": reply["action_eligible"],
        })}

    def _read_delegated_projection(self, payload):
        shared = dict(_exact(payload, {"shared_projection"}, "delegated projection input")["shared_projection"])
        signature = shared.pop("fingerprint")
        if signature != model_data_fingerprint(shared):
            raise ValueError("delegated projection signature drift")
        eligible = shared.pop("delegated_action_eligible")
        if type(eligible) is not bool:
            raise ValueError("delegated eligibility must be boolean")
        base = _validated_shared_projection(_fingerprinted(shared), package=self)
        return eligible, base

    def assess_action(self, *, payload, context):
        _context(context)
        eligible, shared = self._read_delegated_projection(payload)
        # The original assessor has a fixed eligible=True. Do not call it and
        # pretend that this is a delegated decision.
        return {"action_assessment": {
            "version": "policy-transaction-action-assessment-v2",
            "source_shared_projection_fingerprint": model_data_fingerprint(payload),
            "action_kind": "policy_transaction_response", "eligible": eligible,
            "constraints": {"canonical_tool_descriptors": shared["canonical_tool_descriptors"],
                            "tool_execution_authority": False, "automatic_tool_selection": False},
            "observation_advice": {"text_response_required": True, "autonomous_send_authority": False},
            "action_commit_authority": False,
        }}

    def project_semantic_resources(self, *, payload, context):
        _context(context)
        _, shared = self._read_delegated_projection(payload)
        return {"expression_resource_batch": {
            "version": "policy-transaction-realizer-control-v3",
            "control": {"canonical_tool_descriptors": shared["canonical_tool_descriptors"],
                        "text_response_required": True},
            "source_shared_projection_fingerprint": payload["shared_projection"]["fingerprint"],
            "state_commit_authority": False,
        }}
