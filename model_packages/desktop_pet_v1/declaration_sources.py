"""Read-only annotations, published only after accepted source or full restore."""
from contextvars import ContextVar
from contextlib import contextmanager
from framework.model_api import model_data_fingerprint,freeze_model_payload
from .declared_forms import RECALL_VERSION,validate_declarations
from .events import PetEvent,MemoryInstance

_COMMITTED={}
_ACTIVE=ContextVar('pet_declared_sources_restore',default=None)


def _record(profile,event,fact,measurement):
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    entry=profile.get(EVENT_JOURNAL_KEY,{}).get(event.event_id)
    source=profile.get('desktop_pet_product_v1',{}).get('sources',{}).get(event.event_id)
    if (entry is None or source is None or entry['fact_fingerprint']!=fact.fingerprint
        or model_data_fingerprint(measurement)!=entry['measurement_fingerprint']
        or model_data_fingerprint(source.get('measurement'))!=entry['measurement_fingerprint']
        or PetEvent.from_record(entry['event'])!=event or PetEvent.from_record(source['event'])!=event):
        raise ValueError('declared form differs from accepted measurement/source')
    MemoryInstance(event,fact)
    parsed=measurement['pet_memory_recall'];forms=validate_declarations(event,parsed)
    key=(fact.session_binding_fingerprint,fact.fingerprint)
    value={'event_id':event.event_id,'event_fingerprint':event.fingerprint,'source_ref':event.source_binding_ref,
        'owner_ref':event.owner_ref,'actor_ref':event.actor_ref,'scope_ref':event.scope_ref,
        'measurement_fingerprint':entry['measurement_fingerprint'],'forms':forms}
    value['fingerprint']=model_data_fingerprint(value)
    return key,freeze_model_payload(value)


def register_committed(runtime,event,measurement):
    from .hierarchy_sources import register_committed as register_properties
    register_properties(runtime,event,measurement)
    parsed=measurement.get('pet_memory_recall',{})
    if parsed.get('version') not in (RECALL_VERSION,'desktop-pet-source-bound-reference-recall-v5','desktop-pet-owner-bound-reference-recall-v6') or not parsed.get('declared_forms'):return
    key,value=_record(runtime.session.profile,event,runtime.memory_instance(event.event_id).fact,measurement)
    _COMMITTED[key]=value


def _staged(profile):
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from framework.history import HistoryAuthority
    from ops.runtime_full_memory_profile_commit import FRAMEWORK_HISTORY_PROFILE_KEY
    staged={};history=None
    for identity,entry in profile.get(EVENT_JOURNAL_KEY,{}).items():
        source=profile.get('desktop_pet_product_v1',{}).get('sources',{}).get(identity,{})
        measurement=source.get('measurement',{});parsed=measurement.get('pet_memory_recall',{})
        if parsed.get('version') not in (RECALL_VERSION,'desktop-pet-source-bound-reference-recall-v5','desktop-pet-owner-bound-reference-recall-v6') or not parsed.get('declared_forms'):continue
        if history is None:history=HistoryAuthority.from_record(profile[FRAMEWORK_HISTORY_PROFILE_KEY])
        key,value=_record(profile,PetEvent.from_record(entry['event']),history.fact(identity),measurement)
        staged[key]=value
    return staged


@contextmanager
def restore_scope(profile):
    # Stage locally for transaction replay. Failed loads never publish values.
    staged=_staged(profile);token=_ACTIVE.set(staged)
    try:
        from .hierarchy_sources import restore_scope as restore_properties
        with restore_properties(profile):yield
    except BaseException:raise
    else:_COMMITTED.update(staged)
    finally:_ACTIVE.reset(token)


def for_source(source):
    key=source.get('_declaration_key')
    active=_ACTIVE.get()
    return (active if active is not None else _COMMITTED).get(key)
