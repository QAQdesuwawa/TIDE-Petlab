"""Applicability of an ongoing-context response, not a new speech qualifier."""
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint, thaw_model_data
from .three_time import ObservationTime

VERSION='desktop-pet-continuation-check-v1'


def assess_continuation(observation,*,capture,checked_at_ms,time_domain,host_stopping=False):
    if not isinstance(observation,ObservationTime):raise TypeError('formal observation time is required')
    if isinstance(checked_at_ms,bool) or not isinstance(checked_at_ms,int):
        raise ValueError('applicability check needs actual integer wall time')
    reason=None;progress=None
    if time_domain!=observation.time_domain:reason='different_time_domain'
    elif observation.query_completed_ms is None:reason='query_incomplete'
    elif checked_at_ms<observation.query_completed_ms:reason='check_precedes_query_completion'
    elif host_stopping:reason='host_stopping'
    elif capture is None:reason='capture_missing'
    elif not isinstance(capture,Mapping):reason='invalid_capture'
    elif capture.get('gesture_id')!=observation.interaction_id:reason='different_interaction'
    else:
        first,last=capture.get('start_point'),capture.get('last_point')
        valid=lambda p:isinstance(p,(list,tuple)) and len(p)==3 and all(isinstance(v,int) and not isinstance(v,bool) for v in p)
        if not valid(first) or not valid(last):reason='invalid_capture'
        elif first[2]!=observation.origin_ms:reason='different_origin'
        elif last[2]<observation.world_end_ms:reason='capture_precedes_observation'
        elif last[2]>checked_at_ms:reason='capture_after_check'
        else:
            progress=last[2]-observation.world_end_ms
            phase=capture.get('phase')
            reason='same_interaction_ongoing' if phase=='ongoing' else 'interaction_'+phase if phase in {'ended','cancelled'} else 'invalid_capture_phase'
    record={'version':VERSION,'observation_time_fingerprint':observation.to_record()['fingerprint'],
        'capture':None if capture is None else thaw_model_data(capture),'checked_at_ms':checked_at_ms,
        'time_domain':time_domain,'host_stopping':bool(host_stopping),
        'applicable':reason=='same_interaction_ongoing','reason':reason,
        'unobserved_capture_ms':progress,
        'query_duration_ms':None if observation.query_completed_ms is None else observation.query_completed_ms-observation.query_started_ms,
        'observation_age_ms':checked_at_ms-observation.world_end_ms}
    return {**record,'fingerprint':model_data_fingerprint(record)}
