"""Bounded view of the existing same-class formal predecessor, never a recall."""
from framework.model_api import model_data_fingerprint
from model_packages.desktop_pet_v1.reference_cues import VERSION,VERSIONS
from model_packages.desktop_pet_v1.semantic import evidence_source


def source(runtime,prior):
    return {'binding':runtime.session.session_binding.fingerprint,'event_id':prior.event.event_id,
        'event_fingerprint':prior.event.fingerprint,'fact_fingerprint':prior.fact.fingerprint,'source_ref':prior.event.source_binding_ref}


def build(runtime,event,reference,*,limit_chars=2048):
    if event.event_kind!='peer_input' or {c.kind for c in event.contents}!={'speech'} or not reference or not reference.get('previous_event_id'):return None
    prior=runtime.memory_instance(reference['previous_event_id'])
    if (prior.fact.fingerprint!=reference['previous_fact_fingerprint'] or prior.event.actor_ref!=event.actor_ref
        or prior.event.event_kind!=event.event_kind or prior.event.owner_ref!=event.owner_ref or prior.event.scope_ref!=event.scope_ref
        or prior.event.position.coordinate>event.position.coordinate or {c.kind for c in prior.event.contents}!={'speech'}
        or len(prior.event.visible_contents(event.owner_ref))!=len(prior.event.contents)):
        raise ValueError('input reference differs from actual same-class predecessor')
    text=evidence_source(prior.event)
    row={'version':VERSION,'event_fingerprint':event.fingerprint,'source':source(runtime,prior),'reference_fingerprint':reference['fingerprint'],
        'limit_chars':limit_chars,'source_chars':len(text),'status':'delivered' if len(text)<=limit_chars else 'deferred_budget','text':text if len(text)<=limit_chars else ''}
    row['fingerprint']=model_data_fingerprint(row);return row


def verify_runtime(runtime,event,measurement):
    from model_packages.desktop_pet_v1.grounded_recall import KEY
    parsed=measurement.get(KEY,{})
    if 'reference_version' not in parsed:return
    from model_packages.desktop_pet_v1.reference_window import VERSION as WINDOW_VERSION
    if parsed['reference_version']==WINDOW_VERSION:
        from .input_reference_window import verify_runtime as verify_window
        return verify_window(runtime,event,parsed)
    expected=build(runtime,event,measurement.get('local_calibration_reference'))
    if (expected is None or expected['status']!='delivered' or parsed['reference_version'] not in VERSIONS
        or dict(parsed.get('reference_source',{}))!=expected['source']):
        raise ValueError('reference cues differ from verified calibration predecessor')
