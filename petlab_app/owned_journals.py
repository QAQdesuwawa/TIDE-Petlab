"""Product storage representation only: freeze journal rows before publication.

No values, signatures, event times or model decisions are changed. Existing
owned rows can be shared; mutable caller input is always copied and validated.
"""
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from framework.model_api.immutable_record import ImmutableModelRecord

VERSION='petlab-owned-journal-rows-v2-shallow-reuse'

_TABLES=(
    ("desktop_pet_product_v1","sources"),
    ("desktop_pet_product_v1","executions"),
    ("desktop_pet_idle_opening_v1","ticks"),
    ("desktop_pet_idle_opening_v1","outcomes"),
    ("desktop_pet_interaction_observations_v1","ticks"),
    ("desktop_pet_events_v1",),
    ("desktop_pet_temporal_measurements_v1",),
    ("desktop_pet_continuation_checks_v1",),
    ("desktop_pet_retired_writer_v1",),
)
_LISTS=("runtime_progress_transactions","runtime_product_expression_continuations",
        "desktop_pet_cancelled_executions_v1")

def _owned(value,previous=None):
    if type(value) is ImmutableModelRecord:return value
    # Generic progress commits copy row dictionaries but retain their factory-
    # frozen children. Reuse only an exactly matching shallow view of an owned
    # row, never a declared digest or an equal mutable value.
    if (type(value) is dict and type(previous) is ImmutableModelRecord
            and len(value)==len(previous)
            and all(key in previous and item is previous[key] for key,item in value.items())):
        return previous
    return ImmutableModelRecord(value) if isinstance(value,Mapping) else value

def seal_session(session,*,previous_profile=None):
    profile=dict(session.profile)
    previous_profile={} if previous_profile is None else previous_profile
    for path in _TABLES:
        if path[0] not in profile:continue
        parent=profile
        for key in path[:-1]:
            parent[key]=dict(parent[key]);parent=parent[key]
        table=parent[path[-1]]
        previous=previous_profile
        for key in path:previous=previous.get(key,{}) if isinstance(previous,Mapping) else {}
        parent[path[-1]]={key:_owned(row,previous.get(key) if isinstance(previous,Mapping) else None) for key,row in table.items()}
    for key in _LISTS:
        if key in profile:
            previous=previous_profile.get(key,())
            if not isinstance(previous,(list,tuple)):previous=()
            profile[key]=[_owned(row,previous[index] if index<len(previous) else None) for index,row in enumerate(profile[key])]
    # The current Kernel anchor is one value, not a journal. Own it once so
    # repeated reads within a native transaction can reuse its validated values.
    key='runtime_session_binding'
    if key in profile:
        profile[key]=_owned(profile[key],previous_profile.get(key))
    # The framework snapshot is already validated on restore. Seal its rows
    # in the product storage view too: leaving decoded JSON here makes every
    # pre-ingest save compare the same old tree again before History.pack runs.
    history=profile.get('framework_history')
    if (isinstance(history,Mapping) and history.get('version')=='framework-history-snapshot-storage-v2'
            and not (type(history) is MappingProxyType and all(
                type(row) is ImmutableModelRecord for key in ('facts','reads','actual_recalls','influence_edges')
                for row in history.get(key,())))):
        history=dict(history)
        previous=previous_profile.get('framework_history',{})
        if not isinstance(previous,Mapping):previous={}
        for key in ('facts','reads','actual_recalls','influence_edges'):
            if key not in history:continue
            old=previous.get(key,())
            if not isinstance(old,(list,tuple)):old=()
            history[key]=tuple(_owned(row,old[index] if index<len(old) else None)
                for index,row in enumerate(history[key]))
        profile['framework_history']=history
    return replace(session,profile=profile)

def publish_session(app,session):
    baseline=getattr(app.store,'owned_profile',None)
    updated=seal_session(session,previous_profile=app.runtime.session.profile if baseline is None else baseline)
    app.store.save(updated)
    app.runtime.session=updated
    return updated
