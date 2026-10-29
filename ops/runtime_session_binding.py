"""Explicit host persistence bridge for a formal model+temporal session.

This module serializes already-validated Kernel records.  It does not select
packages, read a clock, advance a position, schedule observations, or send.
"""
from __future__ import annotations

from collections.abc import Mapping
from collections import OrderedDict
from threading import RLock
from framework.model_api.immutable_record import ImmutableModelRecord
from ops.owned_record_validation import OwnedJournalValidation
from ops.settlement_validation import verify_settlement

from framework.model_api import model_data_fingerprint, freeze_model_payload
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimeModelParameters,
    RuntimeModelState,
    RuntimePositionState,
    RuntimeProgressAcceptance,
    RuntimeProgressAuthority,
    RuntimeProgressPreparation,
    RuntimeSessionBinding,
    RuntimeStateCommitAcceptance,
    RuntimeStateCommitAuthority,
)


ANCHOR_KEY = "runtime_session_binding"
LEGACY_SHADOW_KEY = "interaction_model_binding"
ANCHOR_VERSION = 1
MODEL_STATE_ANCHOR_VERSION = 2
MODEL_PARAMETERS_ANCHOR_VERSION = 3
PROGRESS_JOURNAL_KEY = "runtime_progress_transactions"


def append_runtime_session_binding(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    initial_state: RuntimePositionState,
    initial_model_state: RuntimeModelState | None = None,
    initial_model_parameters: RuntimeModelParameters | None = None,
) -> dict[str, object]:
    """Create a formal anchor for a new profile; never migrate an old profile."""
    if not isinstance(profile, Mapping) or ANCHOR_KEY in profile:
        raise RuntimeKernelContractError(
            "runtime session binding cannot replace an existing anchor")
    if LEGACY_SHADOW_KEY in profile:
        raise RuntimeKernelContractError(
            "legacy shadow binding requires an explicit migration transaction")
    session_binding.verify_integrity()
    initial_state.verify_integrity(session_binding)
    if initial_model_parameters is not None and initial_model_state is None:
        raise RuntimeKernelContractError(
            "model parameters require an initial model state")
    if initial_model_state is not None:
        if not isinstance(initial_model_state, RuntimeModelState):
            raise RuntimeKernelContractError(
                "initial model state must be RuntimeModelState")
        initial_model_state.verify_integrity(session_binding)
        if (
            initial_model_state.committed_runtime_ordinal
            > initial_state.runtime_ordinal
        ):
            raise RuntimeKernelContractError(
                "initial model state is ahead of initial runtime position")
        if initial_model_parameters is not None:
            if not isinstance(initial_model_parameters, RuntimeModelParameters):
                raise RuntimeKernelContractError(
                    "initial model parameters must be RuntimeModelParameters")
            initial_model_parameters.verify_integrity(session_binding)
            return dict(profile, **{
                ANCHOR_KEY: {
                    "schema_version": MODEL_PARAMETERS_ANCHOR_VERSION,
                    "mode": "active",
                    "revision": 0,
                    "binding": session_binding.to_record(),
                    "position_state": initial_state.to_record(),
                    "model_parameters": initial_model_parameters.to_record(),
                    "model_state": initial_model_state.to_record(),
                    "last_state_commit": None,
                },
            })
        return dict(profile, **{
            ANCHOR_KEY: {
                "schema_version": MODEL_STATE_ANCHOR_VERSION,
                "mode": "active",
                "revision": 0,
                "binding": session_binding.to_record(),
                "position_state": initial_state.to_record(),
                "model_state": initial_model_state.to_record(),
                "last_state_commit": None,
            },
        })
    return dict(profile, **{
        ANCHOR_KEY: {
            "schema_version": ANCHOR_VERSION,
            "mode": "active",
            "revision": 0,
            "binding": session_binding.to_record(),
            "state": initial_state.to_record(),
        },
    })


def restore_runtime_session_state(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    authority: RuntimeProgressAuthority,
) -> RuntimePositionState:
    """Validate exact package/domain identity before restoring Kernel state."""
    if not isinstance(profile, Mapping):
        raise RuntimeKernelContractError(
            "runtime session profile must be a mapping")
    anchor = profile.get(ANCHOR_KEY)
    if not isinstance(anchor, Mapping):
        raise RuntimeKernelContractError(
            "runtime session binding is missing from profile")
    schema_version = anchor.get("schema_version")
    expected_fields = (
        {"schema_version", "mode", "revision", "binding", "state"}
        if schema_version == ANCHOR_VERSION
        else {
            "schema_version", "mode", "revision", "binding",
            "position_state", "model_state", "last_state_commit",
        }
        if schema_version == MODEL_STATE_ANCHOR_VERSION
        else {
            "schema_version", "mode", "revision", "binding",
            "position_state", "model_parameters", "model_state",
            "last_state_commit",
        }
        if schema_version == MODEL_PARAMETERS_ANCHOR_VERSION
        else set()
    )
    if set(anchor) != expected_fields:
        raise RuntimeKernelContractError(
            "runtime session anchor has invalid fields")
    if (
        type(anchor.get("schema_version")) is not int
        or anchor.get("schema_version") not in {
            ANCHOR_VERSION, MODEL_STATE_ANCHOR_VERSION,
            MODEL_PARAMETERS_ANCHOR_VERSION,
        }
        or anchor.get("mode") != "active"
        or isinstance(anchor.get("revision"), bool)
        or not isinstance(anchor.get("revision"), int)
        or anchor["revision"] < 0
    ):
        raise RuntimeKernelContractError(
            "runtime session anchor metadata is invalid")
    session_binding.verify_integrity()
    if authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "progress authority session binding does not match persisted binding")
    if anchor.get("binding") != session_binding.to_record():
        raise RuntimeKernelContractError(
            "runtime session binding does not match persisted anchor")
    state = anchor.get(
        "state" if schema_version == ANCHOR_VERSION else "position_state")
    if not isinstance(state, Mapping):
        raise RuntimeKernelContractError(
            "runtime session anchor state is invalid")
    return authority.restore_state(state)


# Exact owned anchors only; different native authority/actual binding gets a new
# scope. This cache retains native values, never a replacement authority.
_MODEL_SNAPSHOT_VALIDATION = OwnedJournalValidation()


def _restore_runtime_session_model_values(profile, *, session_binding, authority):
    anchor=profile.get(ANCHOR_KEY) if isinstance(profile,Mapping) else None
    def restore(_anchor):
        # ImmutableModelRecord freezes lists to tuples. Decode once only at the
        # external restoration boundary; keep the original validator unchanged.
        from framework.model_api import thaw_model_data
        source=({**profile,ANCHOR_KEY:thaw_model_data(_anchor)}
            if type(_anchor) is ImmutableModelRecord else profile)
        return _restore_runtime_session_model_values_uncached(source,
            session_binding=session_binding,authority=authority)
    if (type(anchor) is not ImmutableModelRecord
            or type(authority) is not RuntimeStateCommitAuthority
            or authority.session_binding is not session_binding
            or set(vars(authority))!={'session_binding'}):
        return restore(anchor)
    # Construct key from actual nested fields. Never trust a supplied fingerprint
    # to authorize a hit after a binding or authority has changed.
    binding_value=model_data_fingerprint({'session_id':session_binding.session_id,
        'model_binding':session_binding.model_binding.to_record(),
        'temporal_binding':session_binding.temporal_binding.to_record(),
        'runtime_variant':session_binding.runtime_variant.to_record(),
        'fingerprint':session_binding.fingerprint})
    return _MODEL_SNAPSHOT_VALIDATION.restore(
        (authority,binding_value,type(authority).restore_state,type(authority).restore_position_state),(anchor,),restore)[0]


def restore_runtime_session_model_state(profile, *, session_binding, authority):
    position, _parameters, state=_restore_runtime_session_model_values(profile,
        session_binding=session_binding,authority=authority)
    return position,state


def restore_runtime_session_model_snapshot(profile, *, session_binding, authority):
    """Restore position, parameters and state together, without a second parse."""
    values=_restore_runtime_session_model_values(profile,session_binding=session_binding,authority=authority)
    if values[1] is None:
        raise RuntimeKernelContractError(
            'expression-ready runtime session requires persisted model parameters')
    return values


def _restore_runtime_session_model_values_uncached(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    authority: RuntimeStateCommitAuthority,
):
    """Restore one v2 atomic position+opaque-model-state session snapshot."""
    if not isinstance(profile, Mapping):
        raise RuntimeKernelContractError(
            "runtime session profile must be a mapping")
    anchor = profile.get(ANCHOR_KEY)
    if not isinstance(anchor, Mapping):
        raise RuntimeKernelContractError(
            "runtime session binding is missing from profile")
    schema_version = anchor.get("schema_version")
    required = (
        {
            "schema_version", "mode", "revision", "binding",
            "position_state", "model_state", "last_state_commit",
        }
        if schema_version == MODEL_STATE_ANCHOR_VERSION
        else {
            "schema_version", "mode", "revision", "binding",
            "position_state", "model_parameters", "model_state",
            "last_state_commit",
        }
        if schema_version == MODEL_PARAMETERS_ANCHOR_VERSION
        else set()
    )
    if set(anchor) != required:
        raise RuntimeKernelContractError(
            "runtime model-state anchor has invalid fields")
    if (
        type(anchor.get("schema_version")) is not int
        or anchor.get("schema_version") not in {
            MODEL_STATE_ANCHOR_VERSION, MODEL_PARAMETERS_ANCHOR_VERSION,
        }
        or anchor.get("mode") != "active"
        or isinstance(anchor.get("revision"), bool)
        or not isinstance(anchor.get("revision"), int)
        or anchor["revision"] < 0
    ):
        raise RuntimeKernelContractError(
            "runtime model-state anchor metadata is invalid")
    session_binding.verify_integrity()
    if not isinstance(authority, RuntimeStateCommitAuthority):
        raise RuntimeKernelContractError(
            "model-state restore requires RuntimeStateCommitAuthority")
    if authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "state commit authority session binding does not match persisted binding")
    if anchor.get("binding") != session_binding.to_record():
        raise RuntimeKernelContractError(
            "runtime session binding does not match persisted anchor")
    position_record = anchor.get("position_state")
    model_state_record = anchor.get("model_state")
    if not isinstance(position_record, Mapping):
        raise RuntimeKernelContractError(
            "runtime model-state anchor position is invalid")
    if not isinstance(model_state_record, Mapping):
        raise RuntimeKernelContractError(
            "runtime model-state anchor model state is invalid")
    position = authority.restore_position_state(position_record)
    model_state = authority.restore_state(model_state_record)
    parameters = (RuntimeModelParameters.restore(
        anchor.get("model_parameters"), session_binding=session_binding)
        if schema_version == MODEL_PARAMETERS_ANCHOR_VERSION else None)
    if model_state.committed_runtime_ordinal > position.runtime_ordinal:
        raise RuntimeKernelContractError(
            "persisted model state is ahead of persisted runtime position")

    last_commit = anchor.get("last_state_commit")
    if last_commit is None:
        if anchor.get("revision") != 0:
            raise RuntimeKernelContractError(
                "revised model-state anchor requires a commit receipt")
        return position, parameters, model_state
    if not isinstance(last_commit, Mapping):
        raise RuntimeKernelContractError(
            "runtime state commit receipt is invalid")
    receipt_fields = {
        "session_binding_fingerprint", "previous_state_fingerprint",
        "previous_position_fingerprint", "state", "position",
        "stage_receipt_fingerprint", "fingerprint",
    }
    if set(last_commit) != receipt_fields:
        raise RuntimeKernelContractError(
            "runtime state commit receipt has invalid fields")
    if (
        last_commit.get("session_binding_fingerprint")
        != session_binding.fingerprint
        or last_commit.get("state") != model_state.to_record()
        or last_commit.get("position") != position.to_record()
    ):
        raise RuntimeKernelContractError(
            "runtime state commit receipt does not match persisted snapshot")
    material = {
        "session_binding_fingerprint": session_binding.fingerprint,
        "previous_state_fingerprint": last_commit.get(
            "previous_state_fingerprint"),
        "previous_position_fingerprint": last_commit.get(
            "previous_position_fingerprint"),
        "state_fingerprint": model_state.fingerprint,
        "position_fingerprint": position.fingerprint,
        "stage_receipt_fingerprint": last_commit.get(
            "stage_receipt_fingerprint"),
    }
    if model_data_fingerprint(material) != last_commit.get("fingerprint"):
        raise RuntimeKernelContractError(
            "runtime state commit receipt fingerprint is invalid")
    return position, parameters, model_state


def commit_runtime_session_settlement(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    authority: RuntimeStateCommitAuthority,
    acceptance: RuntimeStateCommitAcceptance,
) -> dict[str, object]:
    """Return one v2 profile revision containing the entire accepted snapshot."""
    current_position, current_model_state = (
        restore_runtime_session_model_state(
            profile,
            session_binding=session_binding,
            authority=authority,
        )
    )
    if not isinstance(acceptance, RuntimeStateCommitAcceptance):
        raise RuntimeKernelContractError(
            "runtime session settlement requires RuntimeStateCommitAcceptance")
    verify_settlement(acceptance,
        session_binding=session_binding,
        previous_state=current_model_state,
        previous_position=current_position,
    )
    anchor = dict(profile[ANCHOR_KEY])
    anchor["revision"] = int(anchor["revision"]) + 1
    anchor["position_state"] = acceptance.position.to_record()
    anchor["model_state"] = acceptance.state.to_record()
    anchor["last_state_commit"] = acceptance.to_record()
    return dict(profile, **{ANCHOR_KEY: anchor})


def commit_runtime_session_progress_settlement(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    progress_authority: RuntimeProgressAuthority,
    state_authority: RuntimeStateCommitAuthority,
    progress_acceptance: RuntimeProgressAcceptance,
    settlement_acceptance: RuntimeStateCommitAcceptance,
    progress_stage_receipt_fingerprint: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Atomically persist accepted position progress and its model settlement."""
    current_position, current_model_state = restore_runtime_session_model_state(
        profile,
        session_binding=session_binding,
        authority=state_authority,
    )
    anchor_record = profile.get(ANCHOR_KEY)
    if (
        not isinstance(anchor_record, Mapping)
        or anchor_record.get("schema_version")
        != MODEL_PARAMETERS_ANCHOR_VERSION
    ):
        raise RuntimeKernelContractError(
            "progress settlement requires a schema-v3 runtime session")
    if not isinstance(progress_authority, RuntimeProgressAuthority):
        raise RuntimeKernelContractError(
            "progress settlement requires RuntimeProgressAuthority")
    if progress_authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "progress authority session binding does not match persisted binding")
    if state_authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "state authority session binding does not match persisted binding")
    if not isinstance(progress_acceptance, RuntimeProgressAcceptance):
        raise RuntimeKernelContractError(
            "progress settlement requires RuntimeProgressAcceptance")
    if not isinstance(settlement_acceptance, RuntimeStateCommitAcceptance):
        raise RuntimeKernelContractError(
            "progress settlement requires RuntimeStateCommitAcceptance")
    progress_acceptance.verify_integrity(
        session_binding=session_binding,
        previous_state=current_position,
    )
    verify_settlement(settlement_acceptance,
        session_binding=session_binding,
        previous_state=current_model_state,
        previous_position=progress_acceptance.state,
    )
    if (
        settlement_acceptance.position.position.fingerprint
        != progress_acceptance.state.position.fingerprint
    ):
        raise RuntimeKernelContractError(
            "progress settlement changed the accepted temporal position")
    stage_fingerprint = str(progress_stage_receipt_fingerprint or "").strip()
    if not stage_fingerprint:
        raise RuntimeKernelContractError(
            "progress settlement requires a stage receipt fingerprint")

    material = {
        "version": "runtime-progress-transaction-v1",
        "session_binding_fingerprint": session_binding.fingerprint,
        "previous_position_fingerprint": current_position.fingerprint,
        "previous_state_fingerprint": current_model_state.fingerprint,
        "progress_acceptance": progress_acceptance.to_record(),
        "progress_stage_receipt_fingerprint": stage_fingerprint,
        "settlement_acceptance": settlement_acceptance.to_record(),
        "settlement_acceptance_fingerprint": settlement_acceptance.fingerprint,
        "final_position_fingerprint": settlement_acceptance.position.fingerprint,
        "final_state_fingerprint": settlement_acceptance.state.fingerprint,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    existing_journal = profile.get(PROGRESS_JOURNAL_KEY, ())
    if not isinstance(existing_journal, (tuple, list)) or not all(
        isinstance(item, Mapping) for item in existing_journal
    ):
        raise RuntimeKernelContractError(
            "runtime progress transaction journal is invalid")

    anchor = dict(anchor_record)
    anchor["revision"] = int(anchor["revision"]) + 1
    anchor["position_state"] = settlement_acceptance.position.to_record()
    anchor["model_state"] = settlement_acceptance.state.to_record()
    anchor["last_state_commit"] = settlement_acceptance.to_record()
    committed = dict(profile)
    committed[ANCHOR_KEY] = anchor
    committed[PROGRESS_JOURNAL_KEY] = [
        *(item if type(item) is ImmutableModelRecord else dict(item) for item in existing_journal), receipt,
    ]
    # Both native acceptances/preparations were verified above and every field
    # in this receipt was just constructed from those values. Register the exact
    # content for the existing read cache instead of cold-restoring it next turn.
    if (type(progress_authority) is RuntimeProgressAuthority
            and type(state_authority) is RuntimeStateCommitAuthority):
        key=(progress_authority.temporal_runtime,session_binding.fingerprint,
            model_data_fingerprint(receipt))
        with _PROGRESS_VALIDATION_LOCK:
            _PROGRESS_VALIDATION_CACHE[key]=freeze_model_payload(receipt)
            _PROGRESS_VALIDATION_CACHE.move_to_end(key)
            while len(_PROGRESS_VALIDATION_CACHE)>_PROGRESS_VALIDATION_LIMIT:
                _PROGRESS_VALIDATION_CACHE.popitem(last=False)
    return committed, receipt


def commit_runtime_session_fact_settlement(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    progress_authority: RuntimeProgressAuthority,
    state_authority: RuntimeStateCommitAuthority,
    progress_preparation: RuntimeProgressPreparation,
    settlement_acceptance: RuntimeStateCommitAcceptance,
    progress_stage_receipt_fingerprint: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Atomically commit temporal progress inside one fact settlement.

    The prepared position carries the target coordinate but deliberately keeps
    the current ordinal.  The settlement therefore advances revision and
    ordinal exactly once for the whole fact.
    """
    current_position, current_model_state = restore_runtime_session_model_state(
        profile,
        session_binding=session_binding,
        authority=state_authority,
    )
    anchor_record = profile.get(ANCHOR_KEY)
    if (
        not isinstance(anchor_record, Mapping)
        or anchor_record.get("schema_version")
        != MODEL_PARAMETERS_ANCHOR_VERSION
    ):
        raise RuntimeKernelContractError(
            "fact settlement requires a schema-v3 runtime session")
    if progress_authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "fact progress authority binding does not match persisted binding")
    if state_authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "fact state authority binding does not match persisted binding")
    progress_preparation.verify_integrity(
        session_binding=session_binding,
        previous_state=current_position,
    )
    verify_settlement(settlement_acceptance,
        session_binding=session_binding,
        previous_state=current_model_state,
        previous_position=progress_preparation.state,
    )
    stage_fingerprint = str(progress_stage_receipt_fingerprint or "").strip()
    if not stage_fingerprint:
        raise RuntimeKernelContractError(
            "fact settlement requires a progress stage receipt fingerprint")

    material = {
        "version": "runtime-fact-progress-settlement-v1",
        "session_binding_fingerprint": session_binding.fingerprint,
        "previous_position_fingerprint": current_position.fingerprint,
        "previous_state_fingerprint": current_model_state.fingerprint,
        "progress_preparation": progress_preparation.to_record(),
        "progress_stage_receipt_fingerprint": stage_fingerprint,
        "settlement_acceptance": settlement_acceptance.to_record(),
        "settlement_acceptance_fingerprint": settlement_acceptance.fingerprint,
        "final_position_fingerprint": settlement_acceptance.position.fingerprint,
        "final_state_fingerprint": settlement_acceptance.state.fingerprint,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    existing_journal = profile.get(PROGRESS_JOURNAL_KEY, ())
    if not isinstance(existing_journal, (tuple, list)) or not all(
        isinstance(item, Mapping) for item in existing_journal
    ):
        raise RuntimeKernelContractError(
            "runtime progress transaction journal is invalid")

    anchor = dict(anchor_record)
    anchor["revision"] = int(anchor["revision"]) + 1
    anchor["position_state"] = settlement_acceptance.position.to_record()
    anchor["model_state"] = settlement_acceptance.state.to_record()
    anchor["last_state_commit"] = settlement_acceptance.to_record()
    committed = dict(profile)
    committed[ANCHOR_KEY] = anchor
    committed[PROGRESS_JOURNAL_KEY] = [
        *(item if type(item) is ImmutableModelRecord else dict(item) for item in existing_journal), receipt,
    ]
    # Both native acceptances/preparations were verified above and every field
    # in this receipt was just constructed from those values. Register the exact
    # content for the existing read cache instead of cold-restoring it next turn.
    if (type(progress_authority) is RuntimeProgressAuthority
            and type(state_authority) is RuntimeStateCommitAuthority):
        key=(progress_authority.temporal_runtime,session_binding.fingerprint,
            model_data_fingerprint(receipt))
        with _PROGRESS_VALIDATION_LOCK:
            _PROGRESS_VALIDATION_CACHE[key]=freeze_model_payload(receipt)
            _PROGRESS_VALIDATION_CACHE.move_to_end(key)
            while len(_PROGRESS_VALIDATION_CACHE)>_PROGRESS_VALIDATION_LIMIT:
                _PROGRESS_VALIDATION_CACHE.popitem(last=False)
    return committed, receipt


# Operational cache only: full record content, binding, and registered runtime.
# Only frozen evidence is retained; eviction changes cost, never validity.
_PROGRESS_VALIDATION_CACHE = OrderedDict()
_PROGRESS_VALIDATION_LOCK = RLock()
_PROGRESS_VALIDATION_LIMIT = 4096
_PROGRESS_OWNED_VALIDATION = OwnedJournalValidation()


def restore_runtime_progress_transactions(profile, *, session_binding,
        progress_authority, state_authority, materialize=True):
    if type(materialize) is not bool:
        raise TypeError("progress materialization must be boolean")
    if not isinstance(profile, Mapping):
        raise RuntimeKernelContractError("runtime progress journal profile must be a mapping")
    journal = profile.get(PROGRESS_JOURNAL_KEY, ())
    if not isinstance(journal, (tuple, list)):
        raise RuntimeKernelContractError("runtime progress transaction journal is invalid")
    # Keep custom validators on the uncached path.
    if type(progress_authority) is not RuntimeProgressAuthority or type(state_authority) is not RuntimeStateCommitAuthority:
        restored = _restore_runtime_progress_transactions_uncached(profile,
            session_binding=session_binding, progress_authority=progress_authority,
            state_authority=state_authority)
        return restored if materialize else None
    if progress_authority.session_binding != session_binding or state_authority.session_binding != session_binding:
        raise RuntimeKernelContractError("progress journal authority binding is invalid")
    session_binding.verify_integrity()
    temporal = progress_authority.temporal_runtime
    if temporal.binding != session_binding.temporal_binding:
        raise RuntimeKernelContractError("progress journal temporal binding is invalid")
    def validate(item):
        # Mutable records retain the content-addressed path. Immutable records
        # reach this only on their first read in this bound runtime.
        key = (temporal, session_binding.fingerprint, model_data_fingerprint(item))
        with _PROGRESS_VALIDATION_LOCK:
            known = key in _PROGRESS_VALIDATION_CACHE and item == _PROGRESS_VALIDATION_CACHE[key]
            if known:
                _PROGRESS_VALIDATION_CACHE.move_to_end(key)
        if not known:
            _restore_runtime_progress_transactions_uncached(
                {PROGRESS_JOURNAL_KEY: (item,)}, session_binding=session_binding,
                progress_authority=progress_authority, state_authority=state_authority)
            with _PROGRESS_VALIDATION_LOCK:
                _PROGRESS_VALIDATION_CACHE[key] = freeze_model_payload(item)
                while len(_PROGRESS_VALIDATION_CACHE) > _PROGRESS_VALIDATION_LIMIT:
                    _PROGRESS_VALIDATION_CACHE.popitem(last=False)
        return None

    _PROGRESS_OWNED_VALIDATION.restore(
        (temporal, session_binding.fingerprint), journal, validate)
    return tuple(dict(item) for item in journal) if materialize else None


def _restore_runtime_progress_transactions_uncached(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    progress_authority: RuntimeProgressAuthority,
    state_authority: RuntimeStateCommitAuthority,
) -> tuple[dict[str, object], ...]:
    """Validate and return the append-only, content-free progress journal."""
    if not isinstance(profile, Mapping):
        raise RuntimeKernelContractError(
            "runtime progress journal profile must be a mapping")
    journal = profile.get(PROGRESS_JOURNAL_KEY, ())
    if not isinstance(journal, (tuple, list)):
        raise RuntimeKernelContractError(
            "runtime progress transaction journal is invalid")
    if progress_authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "progress journal authority binding is invalid")
    if state_authority.session_binding != session_binding:
        raise RuntimeKernelContractError(
            "progress journal state authority binding is invalid")

    expected_fields = {
        "version", "session_binding_fingerprint",
        "previous_position_fingerprint", "previous_state_fingerprint",
        "progress_acceptance", "progress_stage_receipt_fingerprint",
        "settlement_acceptance", "settlement_acceptance_fingerprint",
        "final_position_fingerprint", "final_state_fingerprint",
        "fingerprint",
    }
    restored: list[dict[str, object]] = []
    for item in journal:
        if (
            isinstance(item, Mapping)
            and item.get("version") == "runtime-fact-progress-settlement-v1"
        ):
            expected_prepared_fields = {
                "version", "session_binding_fingerprint",
                "previous_position_fingerprint", "previous_state_fingerprint",
                "progress_preparation", "progress_stage_receipt_fingerprint",
                "settlement_acceptance", "settlement_acceptance_fingerprint",
                "final_position_fingerprint", "final_state_fingerprint",
                "fingerprint",
            }
            if (
                set(item) != expected_prepared_fields
                or item.get("session_binding_fingerprint")
                != session_binding.fingerprint
            ):
                raise RuntimeKernelContractError(
                    "runtime fact progress journal binding is invalid")
            preparation_record = item.get("progress_preparation")
            if not isinstance(preparation_record, Mapping):
                raise RuntimeKernelContractError(
                    "runtime fact progress preparation is invalid")
            prepared_state = progress_authority.restore_state(
                preparation_record.get("state"))
            order_record = preparation_record.get("order_proof")
            if not isinstance(order_record, Mapping):
                raise RuntimeKernelContractError(
                    "runtime fact progress order proof is invalid")
            from_record = order_record.get("from_position")
            to_record = order_record.get("to_position")
            if not isinstance(from_record, Mapping) or not isinstance(to_record, Mapping):
                raise RuntimeKernelContractError(
                    "runtime fact progress positions are invalid")
            from_position = progress_authority.temporal_runtime.normalize_position(
                from_record.get("coordinate"))
            to_position = progress_authority.temporal_runtime.normalize_position(
                to_record.get("coordinate"))
            order_proof = progress_authority.temporal_runtime.prove_order(
                from_position, to_position)
            progress = progress_authority.temporal_runtime.derive_progress(
                order_proof)
            previous_position = RuntimePositionState.create(
                session_binding=session_binding,
                position=from_position,
                runtime_ordinal=prepared_state.runtime_ordinal,
            )
            preparation = RuntimeProgressPreparation.create(
                session_binding=session_binding,
                previous_state=previous_position,
                state=prepared_state,
                order_proof=order_proof,
                progress=progress,
            )
            if (
                preparation.to_record() != dict(preparation_record)
                or item.get("previous_position_fingerprint")
                != previous_position.fingerprint
            ):
                raise RuntimeKernelContractError(
                    "runtime fact progress preparation fingerprint is invalid")
            settlement_record = item.get("settlement_acceptance")
            if not isinstance(settlement_record, Mapping):
                raise RuntimeKernelContractError(
                    "runtime fact progress settlement is invalid")
            settlement_state = state_authority.restore_state(
                settlement_record.get("state"))
            settlement_position = state_authority.restore_position_state(
                settlement_record.get("position"))
            settlement_material = {
                "session_binding_fingerprint": session_binding.fingerprint,
                "previous_state_fingerprint": settlement_record.get(
                    "previous_state_fingerprint"),
                "previous_position_fingerprint": settlement_record.get(
                    "previous_position_fingerprint"),
                "state_fingerprint": settlement_state.fingerprint,
                "position_fingerprint": settlement_position.fingerprint,
                "stage_receipt_fingerprint": settlement_record.get(
                    "stage_receipt_fingerprint"),
            }
            if (
                settlement_record.get("session_binding_fingerprint")
                != session_binding.fingerprint
                or settlement_record.get("previous_position_fingerprint")
                != prepared_state.fingerprint
                or settlement_position.position.fingerprint
                != prepared_state.position.fingerprint
                or settlement_position.runtime_ordinal
                != prepared_state.runtime_ordinal + 1
                or settlement_state.committed_runtime_ordinal
                != settlement_position.runtime_ordinal
                or model_data_fingerprint(settlement_material)
                != settlement_record.get("fingerprint")
                or item.get("settlement_acceptance_fingerprint")
                != settlement_record.get("fingerprint")
                or item.get("final_position_fingerprint")
                != settlement_position.fingerprint
                or item.get("final_state_fingerprint")
                != settlement_state.fingerprint
            ):
                raise RuntimeKernelContractError(
                    "runtime fact progress settlement fingerprint is invalid")
            prepared_material = {
                key: item.get(key)
                for key in expected_prepared_fields - {"fingerprint"}
            }
            if model_data_fingerprint(prepared_material) != item.get("fingerprint"):
                raise RuntimeKernelContractError(
                    "runtime fact progress journal fingerprint is invalid")
            restored.append(dict(item))
            continue
        if not isinstance(item, Mapping) or set(item) != expected_fields:
            raise RuntimeKernelContractError(
                "runtime progress journal receipt has invalid fields")
        if (
            item.get("version") != "runtime-progress-transaction-v1"
            or item.get("session_binding_fingerprint")
            != session_binding.fingerprint
        ):
            raise RuntimeKernelContractError(
                "runtime progress journal binding is invalid")

        progress_record = item.get("progress_acceptance")
        if not isinstance(progress_record, Mapping) or set(progress_record) != {
            "session_binding_fingerprint", "previous_state_fingerprint",
            "state", "order_proof", "progress", "fingerprint",
        }:
            raise RuntimeKernelContractError(
                "runtime progress journal acceptance is invalid")
        accepted_position = progress_authority.restore_state(
            progress_record.get("state"))
        order_record = progress_record.get("order_proof")
        if not isinstance(order_record, Mapping):
            raise RuntimeKernelContractError(
                "runtime progress journal order proof is invalid")
        from_record = order_record.get("from_position")
        to_record = order_record.get("to_position")
        if not isinstance(from_record, Mapping) or not isinstance(to_record, Mapping):
            raise RuntimeKernelContractError(
                "runtime progress journal positions are invalid")
        from_position = progress_authority.temporal_runtime.normalize_position(
            from_record.get("coordinate"))
        to_position = progress_authority.temporal_runtime.normalize_position(
            to_record.get("coordinate"))
        if (
            from_position.to_record() != dict(from_record)
            or to_position.to_record() != dict(to_record)
        ):
            raise RuntimeKernelContractError(
                "runtime progress journal position fingerprint is invalid")
        order_proof = progress_authority.temporal_runtime.prove_order(
            from_position, to_position)
        if order_proof.to_record() != dict(order_record):
            raise RuntimeKernelContractError(
                "runtime progress journal order proof fingerprint is invalid")
        progress = progress_authority.temporal_runtime.derive_progress(order_proof)
        if progress.to_record() != progress_record.get("progress"):
            raise RuntimeKernelContractError(
                "runtime progress journal progress fingerprint is invalid")
        if accepted_position.runtime_ordinal < 1:
            raise RuntimeKernelContractError(
                "runtime progress journal ordinal is invalid")
        previous_position = RuntimePositionState.create(
            session_binding=session_binding,
            position=from_position,
            runtime_ordinal=accepted_position.runtime_ordinal - 1,
        )
        progress_acceptance = RuntimeProgressAcceptance.create(
            session_binding=session_binding,
            previous_state=previous_position,
            state=accepted_position,
            order_proof=order_proof,
            progress=progress,
        )
        if (
            progress_acceptance.to_record() != dict(progress_record)
            or item.get("previous_position_fingerprint")
            != previous_position.fingerprint
        ):
            raise RuntimeKernelContractError(
                "runtime progress journal acceptance fingerprint is invalid")

        settlement_record = item.get("settlement_acceptance")
        if not isinstance(settlement_record, Mapping) or set(settlement_record) != {
            "session_binding_fingerprint", "previous_state_fingerprint",
            "previous_position_fingerprint", "state", "position",
            "stage_receipt_fingerprint", "fingerprint",
        }:
            raise RuntimeKernelContractError(
                "runtime progress journal settlement is invalid")
        settlement_state = state_authority.restore_state(
            settlement_record.get("state"))
        settlement_position = state_authority.restore_position_state(
            settlement_record.get("position"))
        settlement_material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "previous_state_fingerprint": settlement_record.get(
                "previous_state_fingerprint"),
            "previous_position_fingerprint": settlement_record.get(
                "previous_position_fingerprint"),
            "state_fingerprint": settlement_state.fingerprint,
            "position_fingerprint": settlement_position.fingerprint,
            "stage_receipt_fingerprint": settlement_record.get(
                "stage_receipt_fingerprint"),
        }
        if (
            settlement_record.get("session_binding_fingerprint")
            != session_binding.fingerprint
            or settlement_record.get("previous_position_fingerprint")
            != accepted_position.fingerprint
            or settlement_position.position.fingerprint
            != accepted_position.position.fingerprint
            or settlement_position.runtime_ordinal
            != accepted_position.runtime_ordinal + 1
            or settlement_state.committed_runtime_ordinal
            != settlement_position.runtime_ordinal
            or model_data_fingerprint(settlement_material)
            != settlement_record.get("fingerprint")
            or item.get("settlement_acceptance_fingerprint")
            != settlement_record.get("fingerprint")
            or item.get("final_position_fingerprint")
            != settlement_position.fingerprint
            or item.get("final_state_fingerprint")
            != settlement_state.fingerprint
        ):
            raise RuntimeKernelContractError(
                "runtime progress journal settlement fingerprint is invalid")
        material = {key: item.get(key) for key in expected_fields - {"fingerprint"}}
        if model_data_fingerprint(material) != item.get("fingerprint"):
            raise RuntimeKernelContractError(
                "runtime progress journal fingerprint is invalid")
        restored.append(dict(item))
    return tuple(restored)


def advance_runtime_session_binding(
    profile: Mapping[str, object],
    *,
    session_binding: RuntimeSessionBinding,
    authority: RuntimeProgressAuthority,
    acceptance: RuntimeProgressAcceptance,
) -> dict[str, object]:
    """Return one new profile revision from the exact persisted current state."""
    anchor_record = (
        profile.get(ANCHOR_KEY) if isinstance(profile, Mapping) else None)
    if (
        isinstance(anchor_record, Mapping)
        and anchor_record.get("schema_version") in {
            MODEL_STATE_ANCHOR_VERSION, MODEL_PARAMETERS_ANCHOR_VERSION,
        }
    ):
        raise RuntimeKernelContractError(
            "version 2 model-state anchor requires settlement commit")
    current = restore_runtime_session_state(
        profile,
        session_binding=session_binding,
        authority=authority,
    )
    if not isinstance(acceptance, RuntimeProgressAcceptance):
        raise RuntimeKernelContractError(
            "runtime session advance requires RuntimeProgressAcceptance")
    acceptance.verify_integrity(
        session_binding=session_binding,
        previous_state=current,
    )
    anchor = dict(profile[ANCHOR_KEY])
    anchor["revision"] = int(anchor["revision"]) + 1
    anchor["state"] = acceptance.state.to_record()
    return dict(profile, **{ANCHOR_KEY: anchor})


__all__ = (
    "ANCHOR_KEY",
    "PROGRESS_JOURNAL_KEY",
    "advance_runtime_session_binding",
    "append_runtime_session_binding",
    "commit_runtime_session_progress_settlement",
    "commit_runtime_session_fact_settlement",
    "commit_runtime_session_settlement",
    "restore_runtime_progress_transactions",
    "restore_runtime_session_model_snapshot",
    "restore_runtime_session_model_state",
    "restore_runtime_session_state",
)
