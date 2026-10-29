"""Validated committed property index; old histories never get invented owners."""
from contextvars import ContextVar
from contextlib import contextmanager
from framework.model_api import freeze_model_payload,model_data_fingerprint
from .events import PetEvent,MemoryInstance
from .hierarchy_semantics import RECALL_VERSION,validate
_COMMITTED={}
_ACTIVE=ContextVar('pet_hierarchy_restore',default=None)


def _record(profile,event,fact,measurement):
 from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
 entry=profile.get(EVENT_JOURNAL_KEY,{}).get(event.event_id)
 source=profile.get('desktop_pet_product_v1',{}).get('sources',{}).get(event.event_id)
 if (entry is None or source is None or entry['fact_fingerprint']!=fact.fingerprint
  or model_data_fingerprint(measurement)!=entry['measurement_fingerprint']
  or model_data_fingerprint(source.get('measurement'))!=entry['measurement_fingerprint']
  or PetEvent.from_record(entry['event'])!=event or PetEvent.from_record(source['event'])!=event):raise ValueError('property annotation differs from committed source')
 MemoryInstance(event,fact);properties=validate(event,measurement['pet_memory_recall'])
 value={'event_id':event.event_id,'event_fingerprint':event.fingerprint,'source_ref':event.source_binding_ref,
  'owner_ref':event.owner_ref,'actor_ref':event.actor_ref,'scope_ref':event.scope_ref,
  'measurement_fingerprint':entry['measurement_fingerprint'],'properties':properties}
 value['fingerprint']=model_data_fingerprint(value)
 return (fact.session_binding_fingerprint,fact.fingerprint),freeze_model_payload(value)


def register_committed(runtime,event,measurement):
 if measurement.get('pet_memory_recall',{}).get('version')not in (RECALL_VERSION,'desktop-pet-property-address-recall-v8'):return
 k,v=_record(runtime.session.profile,event,runtime.memory_instance(event.event_id).fact,measurement);_COMMITTED[k]=v


@contextmanager
def restore_scope(profile):
 from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
 from framework.history import HistoryAuthority
 from ops.runtime_full_memory_profile_commit import FRAMEWORK_HISTORY_PROFILE_KEY
 staged={};history=None
 for identity,entry in profile.get(EVENT_JOURNAL_KEY,{}).items():
  measurement=profile.get('desktop_pet_product_v1',{}).get('sources',{}).get(identity,{}).get('measurement',{})
  if measurement.get('pet_memory_recall',{}).get('version')not in (RECALL_VERSION,'desktop-pet-property-address-recall-v8'):continue
  if history is None:history=HistoryAuthority.from_record(profile[FRAMEWORK_HISTORY_PROFILE_KEY])
  k,v=_record(profile,PetEvent.from_record(entry['event']),history.fact(identity),measurement);staged[k]=v
 token=_ACTIVE.set(staged)
 try:yield
 except BaseException:raise
 else:_COMMITTED.update(staged)
 finally:_ACTIVE.reset(token)


def for_source(source):
 active=_ACTIVE.get();return (active if active is not None else _COMMITTED).get(source.get('_declaration_key'))
