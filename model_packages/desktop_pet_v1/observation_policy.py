"""Authored interaction sampling advice only; no state or send authority."""
from framework.model_api import model_data_fingerprint
VERSION='desktop-pet-interaction-observation-policy-v1'

def accelerate(*,base_interval_ms,min_interval_ms,gesture_origin_ms,planning_coordinate):
    args=(base_interval_ms,min_interval_ms,gesture_origin_ms,planning_coordinate)
    if any(isinstance(v,bool) or not isinstance(v,int) for v in args):
        raise ValueError('interaction policy requires integer milliseconds')
    if not 0<min_interval_ms<=base_interval_ms or gesture_origin_ms<0 or planning_coordinate<gesture_origin_ms:
        raise ValueError('interaction policy bounds or elapsed time invalid')
    elapsed=planning_coordinate-gesture_origin_ms
    denominator=base_interval_ms+elapsed
    # Exact rational calculation, rounded up: never schedules below the bound.
    numerator=(base_interval_ms-min_interval_ms)*base_interval_ms
    after=min_interval_ms+(numerator+denominator-1)//denominator
    record={'version':VERSION,'base_interval_ms':base_interval_ms,'min_interval_ms':min_interval_ms,
        'gesture_origin_ms':gesture_origin_ms,'planning_coordinate':planning_coordinate,'elapsed_ms':elapsed,
        'effective_advice':{'after_ms':after}}
    return dict(record,fingerprint=model_data_fingerprint(record))

def effective_advice(plan,*,gesture_origin_ms=None):
    raw=plan['planning_output']['observation_plan']['observation_advice']
    if plan['observation_advice']!=raw:raise ValueError('interaction raw model advice differs')
    if plan['version']=='desktop-pet-interaction-plan-v1':
        if 'interaction_policy' in plan:raise ValueError('legacy plan cannot gain a new policy')
        return raw
    if plan['version']!='desktop-pet-interaction-plan-v2':raise ValueError('interaction plan version invalid')
    stored=plan['interaction_policy']
    expected=accelerate(base_interval_ms=raw['after_ms'],min_interval_ms=plan['bounds']['min_interval_ms'],
        gesture_origin_ms=stored['gesture_origin_ms'],planning_coordinate=plan['origin_coordinate'])
    if stored!=expected or (gesture_origin_ms is not None and stored['gesture_origin_ms']!=gesture_origin_ms):
        raise ValueError('interaction policy differs from original gesture or model advice')
    return expected['effective_advice']
