"""Versioned, local joint-reference constraint before the unchanged formal G.

This is a pet-package calibration model, not the original CX67 psychology.
Raw provider evidence is immutable. No recent values enter provider messages.
"""
import math
from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.root_dynamics import ROOT_ORDER,respond_to_root_stimulus
from .measurement_calibration import _vector

VERSION='desktop-pet-joint-axis-constraint-v4'


def calibrate(*,stimulus,state,constants,constants_version,response_parameters,
              previous_stimulus=None,previous_delta=None):
    raw=_vector(stimulus);state=_vector(state)
    def response(value):
        return respond_to_root_stimulus(constants,state,value,
            effective_constants_version=constants_version,params=response_parameters)
    before=response(raw)
    # Compare pre-box effects. A saturated axis must not masquerade as a tiny
    # semantic measurement. Actual historical delta remains a distinct input.
    current=dict(before.receipt['raw_delta'])
    prior=(None if previous_stimulus is None else
           dict(response(_vector(previous_stimulus)).receipt['raw_delta']))
    actual=None if previous_delta is None else _vector(previous_delta)
    if actual is not None and any(abs(v)>1 for v in actual.values()):
        raise ValueError('actual root delta must be in [-1,1]')
    adjusted=dict(raw);evidence={}
    for axis in ROOT_ORDER:
        value=current[axis];p=None if prior is None else prior[axis]
        d=None if actual is None else actual[axis]
        factor=1.;capacity=None;reason='no_joint_reference'
        if not value:reason='zero_current'
        elif p is not None and d is not None:
            if not p:reason='novel_axis'
            elif value*p<0:reason='direction_reversal'
            elif p*d<0:reason='conflicting_references'
            elif abs(value)>max(abs(p),abs(d)):reason='stronger_axis'
            else:
                # Two-source RMS is a data-dependent scale, not a fixed gain.
                # Keeping the raw reference prevents recursive actual-delta
                # attenuation from making an unchanged reading collapse to 0.
                capacity=math.hypot(p,d)/math.sqrt(2.)
                factor=capacity/math.hypot(capacity,value)
                reason='joint_soft_constraint'
                adjusted[axis]=raw[axis]*factor
        evidence[axis]={'current_unclipped_effect':value,
            'raw_reference_effect':p,'previous_actual_delta':d,
            'capacity':capacity,'factor':factor,'decision':reason}
    after=response(adjusted)
    norm=math.hypot(*raw.values());adjusted_norm=math.hypot(*adjusted.values())
    result={'version':VERSION,'mode':'joint-axis-soft-constraint',
        'raw_stimulus':raw,'adjusted_stimulus':adjusted,'state':state,
        'current_predicted_delta':dict(before.delta_x_event),
        'current_unclipped_effect':current,
        'predicted_adjusted_delta':dict(after.delta_x_event),
        'previous_stimulus':None if previous_stimulus is None else _vector(previous_stimulus),
        'previous_delta':actual,'axis_evidence':evidence,
        'decision':'joint_axis_constraint' if adjusted!=raw else 'preserve',
        'factor':adjusted_norm/norm if norm else 1.,
        'raw_magnitude':norm,'adjusted_magnitude':adjusted_norm,
        'authority':{'state_write':False,'send':False,'history_write':False}}
    result['fingerprint']=model_data_fingerprint(result)
    return result
