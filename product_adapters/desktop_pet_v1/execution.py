"""One external execution contract for an already selected opaque candidate."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from framework.action_protocol import ActionCommitPlan
from framework.model_api import freeze_model_payload, model_data_fingerprint, thaw_model_data
from model_packages.desktop_pet_v1.events import Content


class ExecutionCancelled(RuntimeError):
    """Executor confirms cancellation before any physical effect."""


class ExecutionUncertain(RuntimeError):
    """External effects need reconciliation; repeating them is not permitted."""


@dataclass(frozen=True)
class ExecutionOutcome:
    completed_content_ids: tuple[str, ...]
    trace_ref: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        ids = self.completed_content_ids
        if not isinstance(ids, (tuple, list)) or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError("execution requires exact completed content identities")
        if len(ids) != len(set(ids)):
            raise ValueError("execution contains duplicate completed content identities")
        if not isinstance(self.trace_ref, str) or not self.trace_ref.strip():
            raise ValueError("execution requires an actual trace reference")
        if not isinstance(self.details, Mapping):
            raise ValueError("execution trace details must be a mapping")
        object.__setattr__(self, "completed_content_ids", tuple(ids))
        object.__setattr__(self, "details", freeze_model_payload(self.details))

    def to_record(self):
        return {"completed_content_ids": list(self.completed_content_ids), "trace_ref": self.trace_ref,
                "details": thaw_model_data(self.details)}


@dataclass(frozen=True)
class PreparedExecution:
    position: object
    scope_ref: str
    authorization: object
    request: object
    attempt: object
    candidate: object

    @property
    def fingerprint(self):
        return model_data_fingerprint({"position": self.position.to_record(), "scope_ref": self.scope_ref,
            "authorization": self.authorization.to_record(), "request": self.request.to_record(),
            "attempt": self.attempt.to_record(), "candidate": self.candidate.to_record()})

    def composite_plan(self):
        return self._declared_composite_plan

    @cached_property
    def _declared_composite_plan(self):
        # Request/candidate payloads are immutable. Parse the same sealed
        # declaration once; final source and cancellation guards stay live.
        from model_packages.desktop_pet_v1.composite import CompositePlan
        if 'action_sequence_contract' in self.request.control:
            from model_packages.desktop_pet_v1.action_sequence import validate_candidate
            return validate_candidate(self.request,self.candidate.candidate_payload)
        declaration=self.request.control.get('composite')
        if declaration is None:return None
        if not isinstance(declaration,Mapping) or set(declaration)!={'version','children'}:
            raise ValueError('invalid composite declaration')
        plan=CompositePlan.create(declaration['children'])
        if declaration['version']!=plan.version:raise ValueError('composite version differs from child contract')
        return plan

    def validate(self, *, binding, owner_ref, user_ref):
        self.authorization.verify_integrity(session_binding=binding, current_position=self.position)
        self.request.verify_integrity(session_binding=binding, current_position=self.position,
                                      authorization=self.authorization)
        self.attempt.verify_integrity(request=self.request)
        self.candidate.verify_integrity(request=self.request, attempt=self.attempt)
        if self.authorization.subject_ref != owner_ref or self.authorization.target_ref != user_ref:
            raise ValueError("execution owner or destination differs from its authorization")
        direct=(self.authorization.action_kind=="direct_reply" and self.authorization.host_authorization_evidence.get("origin") in {"direct_reply","deferred_direct_reply_v1"})
        observed=(self.authorization.action_kind=="autonomous_opening" and self.authorization.host_authorization_evidence.get("origin") in {"dynamic_interaction_tick","dynamic_idle_tick"} and bool(self.authorization.host_authorization_evidence.get("tick_receipt_fingerprint")))
        peer=(self.authorization.action_kind=="direct_reply" and self.authorization.host_authorization_evidence.get("origin")=="eligible_peer_reply_v1" and bool(self.authorization.host_authorization_evidence.get("peer_assessment_fingerprint")))
        if not (direct or peer or observed):raise ValueError("direct replies require a verified input; autonomous actions require a verified interaction tick")
        if not isinstance(self.scope_ref, str) or not self.scope_ref:
            raise ValueError("execution scope is missing")
        payload = self.candidate.candidate_payload
        private_staged=self.request.control.get('staged_version')=='desktop-pet-staged-v2-private-thought'
        staged=private_staged or self.request.control.get('staged_version')=='desktop-pet-staged-v1'
        if 'staged_version' in self.request.control and not staged:raise ValueError('unknown staged execution version')
        sequence='action_sequence_contract' in self.request.control
        if staged and sequence:raise ValueError('sequence is already sealed; staged speech is incompatible')
        if set(payload) != ({'contents','action_sequence'} if sequence else {'contents','late_speech'} if staged else {"contents"}) or not isinstance(payload["contents"], (tuple, list)) or not payload["contents"]:
            raise ValueError("candidate requires the unified content contract")
        contents = tuple(Content.from_record(record) for record in payload["contents"])
        if len({item.content_id for item in contents}) != len(contents):
            raise ValueError("candidate content identities are duplicated")
        allowed={"speech", "action"}
        if sequence or 'composite' in self.request.control:
            from model_packages.desktop_pet_v1.composite import CompositePlan
            composite=self.composite_plan()
            if staged:
                slot=payload['late_speech']
                speech_ids=[key for key,row in composite.children.items() if row['kind']=='speech']
                if (not isinstance(slot,Mapping) or set(slot)!=({'content_id','thought_content_id'} if private_staged else {'content_id'}) or speech_ids!=[slot['content_id']]
                        or self.request.control.get('allow_speech') is not True):
                    raise ValueError('staged speech slot differs from declared child')
                excluded={slot['content_id']}
                if private_staged:
                    if self.request.control.get('allow_thought') is not True or [key for key,row in composite.children.items() if row['kind']=='thought']!=[slot['thought_content_id']]:raise ValueError('staged thought slot differs')
                    excluded.add(slot['thought_content_id'])
                CompositePlan.create({key:row for key,row in composite.children.items() if key not in excluded}).validate_contents(contents)
            else:composite.validate_contents(contents)
            allowed.update(('expression','thought'))
        elif staged:raise ValueError('staged execution requires composite declaration')
        for item in contents:
            if item.kind not in allowed:raise ValueError('candidate includes undeclared content kind')
            if item.kind=='thought':
                if item.recipient_refs!=(owner_ref,) or self.request.control.get('allow_thought') is not True:
                    raise ValueError('thought requires explicit permission and exact owner-only visibility')
            elif user_ref not in item.recipient_refs:
                raise ValueError('external candidate includes non-deliverable content')
        from model_packages.desktop_pet_v1 import spatial
        for item in contents:
            face=item.parameters.get('presentation',{})
            if face.get('version')==spatial.PRESENTATION:
                scene=self.request.readonly_facts.get('scene_context')
                if (self.request.control.get('gaze_contract')!=spatial.GAZE or scene is None
                    or face['gaze']!=spatial.intent(scene,face['gaze']['target_ref'])):raise ValueError('gaze differs from sealed visible scene')
        # The sequence verifier already rebuilt every physical prerequisite
        # from the frozen authorized catalog and compared exact contents.
        # Writer intent keys need not include locally compiled approach steps.
        keys = ([row['key'] for row in composite.children.values() if row['kind']=='action']
                if sequence else self.request.control.get("selected_action_keys", ()))
        if not isinstance(keys, (tuple, list)) or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("selected action contract is invalid")
        if Counter(item.semantic_key for item in contents if item.kind == "action") != Counter(keys):
            raise ValueError("candidate actions differ from the selected action keys")
        if any(item.kind == "speech" for item in contents) and self.request.control.get("allow_speech") is not True:
            raise ValueError("candidate includes speech without expression permission")
        plan = ActionCommitPlan.create(
            session_binding=binding, current_position=self.position, authorization=self.authorization,
            action_kind=self.authorization.action_kind, subject_ref=self.authorization.subject_ref,
            source_fact_ref=self.authorization.source_fact_ref, target_ref=self.authorization.target_ref,
            realizer_attempt_ref=self.attempt.attempt_ref, candidate_ref=self.candidate.candidate_ref,
        )
        return plan, contents
