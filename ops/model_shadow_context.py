"""Read-only F2 invocation identity from paired committed P3 evidence.

This compatibility bridge does not invoke a model stage. It cannot turn a lone
P3 row, a draft, or a legacy unscoped event into a conversation fact.
"""
from __future__ import annotations

from collections.abc import Mapping

from framework.model_api import ModelInvocationContext
from framework.model_runtime import BoundInteractionModel
from ops.model_session_binding import validate_shadow_model_binding
from interaction_extensions.multi_participant.participants import Participant


def _record(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be committed record data")
    return value


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an exact non-empty identifier")
    return value


def _time(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be an exact non-negative wall-clock time")
    return value


def creation_context_from_committed_profile(
    profile: Mapping[str, object], *, runtime: BoundInteractionModel,
) -> ModelInvocationContext:
    """Bind model creation to a saved session, without inventing a conversation."""
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("creation shadow context requires a registry-bound runtime")
    saved = _record(profile, "committed creation profile")
    anchor = validate_shadow_model_binding(saved, runtime=runtime)
    created_at = _time(saved.get("created_at"), "profile creation time")
    if anchor is None or anchor["bound_at_ms"] != created_at:
        raise ValueError("creation time differs from committed model binding")
    return ModelInvocationContext(
        session_id=runtime.session_id, conversation_id=None,
        participant_id=None, at_ms=created_at,
    )


def tick_context_from_committed_observation(
    p3_receipt: Mapping[str, object], *,
    wall_boundary: Mapping[str, object],
    actor_observation: Mapping[str, object],
    profile: Mapping[str, object],
    runtime: BoundInteractionModel,
) -> ModelInvocationContext:
    """Identify one Actor-local dynamic tick without inventing a destination.

    The caller must supply three rows read from the same committed owner journal.
    This read-only bridge cross-checks their exact tick, owner, time and plan
    version; it neither settles another tick nor grants autonomous send authority.
    """
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("tick shadow context requires a registry-bound runtime")
    saved = _record(profile, "tick profile")
    validate_shadow_model_binding(saved, runtime=runtime)
    identity = _record(saved.get("product_participant_identity"), "tick participant")
    try:
        participant = Participant.from_record(identity)
    except Exception as exc:
        raise ValueError("tick participant identity is not canonical") from exc
    if participant.kind != "npc" or not participant.capabilities.has_symbolic_scheduler:
        raise ValueError("tick participant is not an autonomous NPC")
    participant_id = participant.participant_id

    p3 = _record(p3_receipt, "tick P3 receipt")
    boundary = _record(wall_boundary, "tick wall boundary")
    observed = _record(actor_observation, "tick Actor observation")
    if p3.get("kind") != "opening_authority_receipt":
        raise ValueError("tick P3 receipt is not committed opening authority")
    event = _record(_record(p3.get("detail"), "tick P3 detail").get("event"),
                    "tick P3 event")
    if event.get("kind") != "tick":
        raise ValueError("tick P3 event kind is not a formal tick")
    tick_id = _id(event.get("id"), "tick event ID")
    at_ms = _time(p3.get("ts"), "tick P3 time")
    if _time(event.get("observed_at_ms"), "tick event time") != at_ms:
        raise ValueError("tick P3 event time differs from committed receipt")
    if boundary.get("kind") != "wall_clock_boundary_settled":
        raise ValueError("tick wall boundary is not committed")
    detail = _record(boundary.get("detail"), "tick wall detail")
    if observed.get("kind") != "tick_observation_root_vector":
        raise ValueError("tick Actor observation is not committed")
    boundary_version = detail.get("plan_version")
    if type(boundary_version) is not int or boundary_version < 1:
        raise ValueError("tick plan version is invalid")
    params_sha = _id(p3.get("params_sha"), "tick P3 parameter version")
    manifest = _record(saved.get("manifest"), "tick profile manifest")
    if (detail.get("tick_id") != tick_id
            or observed.get("tick_id") != tick_id
            or detail.get("participant_id") != participant_id
            or observed.get("participant_id") != participant_id
            or _time(boundary.get("ts"), "tick wall time") != at_ms
            or _time(observed.get("ts"), "tick observation time") != at_ms
            or observed.get("plan_version") != boundary_version
            or detail.get("params_sha") != params_sha
            or observed.get("params_sha") != params_sha
            or manifest.get("params_sha") != params_sha
            or _record(observed.get("detail"), "tick observation detail").get(
                "authority_event") != event):
        raise ValueError("tick observation differs from committed owner evidence")
    return ModelInvocationContext(
        session_id=runtime.session_id, conversation_id=None,
        participant_id=participant_id, event_id=tick_id, at_ms=at_ms,
        metadata={"committed_event_kind": "tick", "plan_version": boundary_version},
    )


def p3_context_from_committed_receipt(
    p3_receipt: Mapping[str, object], *,
    committed_evidence: Mapping[str, object],
    profile: Mapping[str, object],
    runtime: BoundInteractionModel,
    input_message: Mapping[str, object] | None = None,
) -> ModelInvocationContext:
    """Bind one P3 readout to its F03 or F16 fact without calling the model.

    Input requires both F03 measurement and its committed message so the
    recipient is independently proven. Output requires its exact F16 message.
    The local P3 event ID remains local; caused-by source IDs stay metadata.
    """
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("P3 shadow context requires a registry-bound runtime")
    validate_shadow_model_binding(profile, runtime=runtime)
    p3 = _record(p3_receipt, "P3 receipt")
    evidence = _record(committed_evidence, "committed evidence")
    if p3.get("schema_version") != 1 or p3.get("kind") != "opening_authority_receipt":
        raise ValueError("P3 shadow context requires a committed P3 receipt")
    detail = _record(p3.get("detail"), "P3 detail")
    event = _record(detail.get("event"), "P3 event")
    event_kind = _id(event.get("kind"), "P3 event kind")
    event_id = _id(event.get("id"), "P3 event ID")
    at_ms = _time(p3.get("ts"), "P3 time")
    if _time(event.get("observed_at_ms"), "P3 event time") != at_ms:
        raise ValueError("P3 event time differs from its committed receipt")

    if event_kind == "npc_output":
        if input_message is not None or evidence.get("role") != "npc":
            raise ValueError("output P3 requires its committed NPC message")
        action_id = _id(evidence.get("action_id"), "F16 action ID")
        if (p3.get("action_id") != action_id or event_id != action_id
                or _time(evidence.get("ts"), "F16 time") != at_ms):
            raise ValueError("output P3 does not match the committed F16 action")
        context = _record(evidence.get("conversation_context"), "F16 conversation context")
        if p3.get("conversation_context") != context:
            raise ValueError("output P3 conversation differs from committed F16")
        participant = _id(context.get("owner_participant_id"), "F16 participant")
        if context.get("sender_participant_id") != participant:
            raise ValueError("output P3 participant differs from committed F16")
        if evidence.get("output_identity") is not None:
            identity = _record(evidence["output_identity"], "F16 output identity")
            if (identity.get("actor_kind") != "npc"
                    or identity.get("participant_id") != participant):
                raise ValueError("output identity differs from committed F16 context")
        conversation = _id(context.get("conversation_id"), "F16 conversation")
        metadata = {
            "conversation_context": dict(context),
            "committed_event_kind": event_kind,
        }
        return ModelInvocationContext(
            session_id=runtime.session_id, conversation_id=conversation,
            participant_id=participant, event_id=event_id, action_id=action_id,
            at_ms=at_ms, metadata=metadata,
        )

    if input_message is None:
        raise ValueError("input P3 requires its committed input message")
    message = _record(input_message, "committed input message")
    expression_anchor = _record(evidence.get("expression_anchor"), "F03 event anchor")
    if (evidence.get("kind") != "input_measurement_committed"
            or expression_anchor.get("event_kind") != event_kind
            or evidence.get("turn_id") != event_id
            or p3.get("turn_id") != event_id
            or p3.get("action_id") is not None
            or _time(evidence.get("ts"), "F03 time") != at_ms
            or message.get("turn_id") != event_id
            or _time(message.get("ts"), "input message time") != at_ms):
        raise ValueError("input P3 does not match the committed F03 turn")
    delivery = _record(evidence.get("delivery_reference"), "F03 delivery")
    if p3.get("delivery_reference") != delivery:
        raise ValueError("input P3 delivery differs from committed F03")
    provenance = _record(message.get("conversation_provenance"), "input provenance")
    if any(provenance.get(key) != value for key, value in delivery.items()):
        raise ValueError("input message source differs from committed F03")
    source_kind = _id(evidence.get("participant_source_kind"), "F03 participant source")
    expected_kind = {
        "human": "human_input", "npc": "peer_input", "simulation": "simulation_input",
    }.get(source_kind)
    if (expected_kind is None or event_kind != expected_kind
            or message.get("participant_source_kind") != source_kind):
        raise ValueError("input P3 event kind differs from committed message source")
    participant = _id(p3.get("recipient_participant_id"), "P3 recipient")
    if provenance.get("recipient_participant_id") != participant:
        raise ValueError("input P3 recipient differs from committed message")
    conversation = _id(delivery.get("conversation_id"), "F03 conversation")
    return ModelInvocationContext(
        session_id=runtime.session_id, conversation_id=conversation,
        participant_id=participant, event_id=event_id, at_ms=at_ms,
        metadata={
            "delivery_reference": dict(delivery),
            "committed_event_kind": event_kind,
        },
    )


def time_context_from_committed_input_receipt(
    p3_receipt: Mapping[str, object], *,
    committed_evidence: Mapping[str, object],
    input_message: Mapping[str, object],
    profile: Mapping[str, object],
    runtime: BoundInteractionModel,
) -> ModelInvocationContext:
    """Identify a committed input's pre-event F08 segment without carrying model data.

    The F03 time-expression anchor is a frozen readout of the already settled
    wall-clock transition. Its six-axis values and constants remain stage payload,
    not invocation identity or a new authority to advance time.
    """
    context = p3_context_from_committed_receipt(
        p3_receipt, committed_evidence=committed_evidence,
        input_message=input_message, profile=profile, runtime=runtime,
    )
    f03 = _record(committed_evidence, "F03 receipt")
    p3_detail = _record(p3_receipt.get("detail"), "P3 detail")
    anchor = _record(f03.get("time_expression_anchor"), "committed F08 anchor")
    elapsed_ms = _time(anchor.get("elapsed_ms"), "committed F08 elapsed time")
    event_kind = _id(context.metadata.get("committed_event_kind"), "input event kind")
    if (anchor.get("version") != "time-expression-anchor-v1"
            or _time(anchor.get("settled_ms"), "committed F08 settled time") != context.at_ms
            or elapsed_ms == 0 or elapsed_ms > context.at_ms
            or anchor.get("event_kind") != event_kind + "_pre_event"
            or not f03.get("params_sha")
            or f03.get("params_sha") != p3_receipt.get("params_sha")
            or anchor.get("effective_constants_version")
            != p3_detail.get("effective_constants_version")):
        raise ValueError("committed F08 anchor differs from paired input event")
    _record(anchor.get("effective_constants"), "committed F08 constants")
    for name in ("X_before", "Delta_X_time", "X_after"):
        _record(anchor.get(name), "committed F08 " + name)
    return context


def semantic_context_from_committed_receipt(
    semantic_receipt: Mapping[str, object], *,
    committed_evidence: Mapping[str, object],
    profile: Mapping[str, object],
    runtime: BoundInteractionModel,
    input_message: Mapping[str, object] | None = None,
    delivery_receipt: Mapping[str, object] | None = None,
    output_p3_receipt: Mapping[str, object] | None = None,
) -> ModelInvocationContext:
    """Identify one already committed semantic measurement without invoking a model.

    Input uses the independent delivery, F03, semantic projection and message
    rows. Output uses its post-F16 semantic row, committed NPC message and
    same-action P3 identity shell; P3 math never supplies a semantic result.
    Neither path infers a conversation from a time or Store path.
    """
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("semantic shadow context requires a registry-bound runtime")
    validate_shadow_model_binding(profile, runtime=runtime)
    semantic = _record(semantic_receipt, "semantic receipt")
    evidence = _record(committed_evidence, "committed evidence")
    if semantic.get("schema_version") != 1:
        raise ValueError("semantic shadow context requires a committed receipt")
    kind = semantic.get("kind")
    at_ms = _time(semantic.get("ts"), "semantic receipt time")

    if kind == "output_semantic_measurement_committed":
        if input_message is not None or delivery_receipt is not None:
            raise ValueError("output semantic context cannot use input evidence")
        measurement = _record(semantic.get("measurement"), "output measurement")
        projection = measurement.get("projection")
        if (measurement.get("mode") != "semantic_projector"
                or measurement.get("event_kind") != "npc_output"
                or not isinstance(projection, Mapping) or not projection):
            raise ValueError("output semantic context requires an actual projection")
        if evidence.get("role") != "npc":
            raise ValueError("output semantic context requires a committed NPC message")
        action_id = _id(semantic.get("action_id"), "output semantic action ID")
        if (evidence.get("action_id") != action_id
                or _time(evidence.get("ts"), "F16 message time") != at_ms
                or semantic.get("turn_id") != evidence.get("turn_id")):
            raise ValueError("output semantic receipt differs from committed F16")
        p3 = _record(output_p3_receipt, "same-action P3 receipt")
        if (not semantic.get("params_sha")
                or semantic.get("params_sha") != p3.get("params_sha")):
            raise ValueError("output semantic parameters differ from same-action P3")
        p3_context = p3_context_from_committed_receipt(
            p3, committed_evidence=evidence,
            profile=profile, runtime=runtime,
        )
        if (p3_context.action_id != action_id or p3_context.event_id != action_id
                or p3_context.at_ms != at_ms):
            raise ValueError("output semantic receipt differs from same-action P3 identity")
        return ModelInvocationContext(
            session_id=p3_context.session_id,
            conversation_id=p3_context.conversation_id,
            participant_id=p3_context.participant_id,
            event_id=p3_context.event_id,
            action_id=p3_context.action_id,
            at_ms=p3_context.at_ms,
            metadata={**dict(p3_context.metadata), "committed_event_kind": "npc_output"},
        )

    if kind != "semantic_projected":
        raise ValueError("semantic shadow context requires a projected input")
    if output_p3_receipt is not None:
        raise ValueError("input semantic context cannot use output P3 evidence")
    projection = semantic.get("projection")
    if not isinstance(projection, Mapping) or not projection:
        raise ValueError("input semantic context requires an actual projection")
    f03 = evidence
    delivery = _record(delivery_receipt, "committed delivery")
    message = _record(input_message, "committed input message")
    if (f03.get("schema_version") != 1
            or f03.get("kind") != "input_measurement_committed"
            or delivery.get("schema_version") != 1
            or delivery.get("kind") != "conversation_delivery_committed"
            or message.get("role") != "user"):
        raise ValueError("input semantic context requires committed input evidence")
    turn_id = _id(semantic.get("turn_id"), "input semantic turn ID")
    if (f03.get("turn_id") != turn_id or delivery.get("turn_id") != turn_id
            or message.get("turn_id") != turn_id
            or _time(f03.get("ts"), "F03 time") != at_ms
            or _time(delivery.get("ts"), "delivery time") != at_ms
            or _time(message.get("ts"), "input message time") != at_ms
            or semantic.get("params_sha") != f03.get("params_sha")
            or semantic.get("params_sha") != delivery.get("params_sha")):
        raise ValueError("input semantic projection differs from committed F03")
    reference = _record(f03.get("delivery_reference"), "F03 delivery reference")
    provenance = _record(message.get("conversation_provenance"), "input provenance")
    if delivery.get("delivery_reference") != reference or any(
        provenance.get(key) != value for key, value in reference.items()
    ):
        raise ValueError("input semantic source differs from committed delivery")
    participant = _id(delivery.get("recipient_participant_id"), "delivery recipient")
    if (provenance.get("recipient_participant_id") != participant
            or participant not in (provenance.get("visible_recipient_ids") or ())):
        raise ValueError("input semantic recipient differs from committed message")
    anchor = _record(f03.get("expression_anchor"), "F03 event anchor")
    expected_kind = {
        "human": "human_input", "npc": "peer_input", "simulation": "simulation_input",
    }.get(delivery.get("sender_participant_kind"))
    if (expected_kind is None or anchor.get("event_kind") != expected_kind
            or f03.get("participant_source_kind") != delivery.get("sender_participant_kind")
            or message.get("participant_source_kind") != delivery.get("sender_participant_kind")):
        raise ValueError("input semantic event kind differs from committed delivery")
    conversation = _id(reference.get("conversation_id"), "F03 conversation")
    return ModelInvocationContext(
        session_id=runtime.session_id, conversation_id=conversation,
        participant_id=participant, event_id=turn_id, at_ms=at_ms,
        metadata={"delivery_reference": dict(reference),
                  "committed_event_kind": expected_kind},
    )
