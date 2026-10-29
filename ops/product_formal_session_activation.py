"""Activate a configured formal product session without the legacy Runner."""
from __future__ import annotations

from collections.abc import Callable, Mapping

from ops.actor import Actor
from interaction_extensions.multi_participant.object_projection import (
    ParticipantProjectionError,
    require_participant_projection,
)
from ops.product_formal_session_owner import ProductFormalSessionOwner
from ops.product_runtime_session import ProductRuntimeSessionPlan
from ops.runtime_session_bootstrap import (
    bootstrap_runtime_session,
    restore_bootstrapped_runtime_session,
)


def activate_product_formal_session_owner(
    *,
    participant: object,
    profile: Mapping[str, object],
    plan: ProductRuntimeSessionPlan,
    persist_profile: Callable[[Mapping[str, object]], None],
) -> ProductFormalSessionOwner:
    """Create or restore exactly the session described by ``plan``.

    Creation persists the complete signed session anchor before publishing an
    owner.  Restoration validates existing products and performs no write.
    """
    try:
        require_participant_projection(participant, label="formal activation")
    except ParticipantProjectionError as exc:
        raise TypeError("formal activation requires participant projection") from exc
    if not isinstance(profile, Mapping):
        raise TypeError("formal activation requires a profile mapping")
    if not isinstance(plan, ProductRuntimeSessionPlan):
        raise TypeError("formal activation requires ProductRuntimeSessionPlan")
    if not callable(persist_profile):
        raise TypeError("persist_profile must be callable")

    composition = plan.composition
    has_anchor = "runtime_session_binding" in profile
    if has_anchor:
        if plan.initialization is not None:
            raise ValueError(
                "existing formal session restore rejects initialization")
        session = restore_bootstrapped_runtime_session(
            profile=profile,
            model_runtime=composition.model_runtime,
            temporal_runtime=composition.temporal_runtime,
            runtime_variant=composition.variant_binding,
        )
    else:
        initialization = plan.initialization
        if initialization is None:
            raise ValueError(
                "new formal session requires explicit initialization")
        session = bootstrap_runtime_session(
            profile=profile,
            model_runtime=composition.model_runtime,
            temporal_runtime=composition.temporal_runtime,
            runtime_variant=composition.variant_binding,
            initial_coordinate=initialization.initial_coordinate,
            parameter_payload=initialization.parameter_payload,
            state_payload=initialization.state_payload,
        )
        persist_profile(session.profile)

    return ProductFormalSessionOwner(
        participant=participant,
        session=session,
        model_runtime=composition.model_runtime,
        temporal_runtime=composition.temporal_runtime,
        runtime_variant=composition.variant_binding,
        persist_profile=persist_profile,
    )


def activate_product_formal_actor(
    *,
    runtime_owner: ProductFormalSessionOwner,
    start: bool = True,
    on_output=None,
) -> Actor:
    """Bind one formal owner to the serial Actor without legacy defaults."""
    if not isinstance(runtime_owner, ProductFormalSessionOwner):
        raise TypeError("formal Actor activation requires formal session owner")
    if on_output is not None and not callable(on_output):
        raise TypeError("formal Actor output callback must be callable or None")
    return Actor(
        runtime_owner,
        params={},
        start=bool(start),
        on_output=on_output,
    )


__all__ = (
    "activate_product_formal_actor",
    "activate_product_formal_session_owner",
)
