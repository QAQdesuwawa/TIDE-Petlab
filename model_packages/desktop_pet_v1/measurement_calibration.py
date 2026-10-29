"""Read-only joint axis normalization; never a replacement root response.

Raw stimulus and realized delta are mapped into a common effect space using
original G. Recent values never enter provider messages. Activation is explicit.
"""
import math
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.root_dynamics import ROOT_ORDER,respond_to_root_stimulus

VERSION='desktop-pet-joint-axis-calibration-v3'

def _vector(v):
    if not isinstance(v,Mapping) or set(v)!=set(ROOT_ORDER):raise ValueError('calibration requires the six named axes')
    if any(type(v[k]) not in (int,float) or not math.isfinite(v[k]) for k in ROOT_ORDER):raise ValueError('calibration requires finite numeric axes')
    return {k:float(v[k]) for k in ROOT_ORDER}

def compare(current,reference):
    current=_vector(current);reference=_vector(reference)
    a=math.hypot(*current.values());b=math.hypot(*reference.values())
    cosine=None if not a or not b else max(-1.,min(1.,sum(current[k]*reference[k] for k in ROOT_ORDER)/a/b))
    return {'current_magnitude':a,'reference_magnitude':b,'direction_similarity':cosine,
        'difference':{k:current[k]-reference[k] for k in ROOT_ORDER},
        'authority':{'state_write':False,'send':False,'history_write':False}}

def calibrate(*,stimulus,state,constants,constants_version,response_parameters,
              previous_stimulus=None,previous_delta=None):
    """Joint axis-profile normalization; preserve strength, sign and novelty.

    The package applies the returned stimulus after validating a local reference.
    """
    raw=_vector(stimulus);state=_vector(state)
    def response(value):
        return respond_to_root_stimulus(constants,state,value,
            effective_constants_version=constants_version,params=response_parameters)
    first=response(raw);current=dict(first.delta_x_event);references=[]
    if previous_stimulus is not None:references.append(('previous_stimulus_at_current_state',dict(response(_vector(previous_stimulus)).delta_x_event)))
    if previous_delta is not None:
        delta=_vector(previous_delta)
        if any(abs(v)>1 for v in delta.values()):raise ValueError('actual root delta must be in [-1,1]')
        references.append(('previous_actual_delta',delta))
    magnitude=math.hypot(*current.values());rows=[];capacity=0.;support=0.
    for name,ref in references:
        c=compare(current,ref);b=c['reference_magnitude'];capacity=max(capacity,b)
        ratio=min(magnitude,b)/max(magnitude,b) if magnitude and b else 0.
        amplitude_similarity=2*ratio/(1+ratio*ratio)
        similarity=max(0.,c['direction_similarity'] or 0.)*amplitude_similarity
        support=max(support,similarity)
        rows.append({'source_kind':name,'vector':ref,**c,'amplitude_similarity':amplitude_similarity,'joint_similarity':similarity})
    excess=0. if not magnitude else max(0.,1-capacity/magnitude)
    proposed_factor=1-support*excess if rows else 1.
    adjusted=dict(raw);weight=0.;decision='preserve';joint_profile={}
    repeated=previous_stimulus is not None and raw==_vector(previous_stimulus)
    if repeated:decision='preserve_identical_reading'
    if not repeated and len(rows)==2 and magnitude and all(r['reference_magnitude'] for r in rows):
        r1,r2=rows
        agreement=max(0.,compare(r1['vector'],r2['vector'])['direction_similarity'] or 0.)
        weight=max(0.,min(1.,agreement*min(r1['joint_similarity'],r2['joint_similarity'])))
        increase=any(abs(current[k])>max(abs(r1['vector'][k]),abs(r2['vector'][k])) for k in ROOT_ORDER)
        if increase:decision='preserve_unsupported_change';weight=0.
        elif first.receipt['projection']['clipped_axes']:decision='preserve_boundary';weight=0.
        elif weight:
            for k in ROOT_ORDER:
                joint_profile[k]=(abs(r1['vector'][k]*r2['vector'][k])/r1['reference_magnitude']/r2['reference_magnitude']
                    if current[k]*r1['vector'][k]>0 and current[k]*r2['vector'][k]>0 else 0.)
            total=sum(joint_profile.values())
            if total:
                joint_profile={k:v/total for k,v in joint_profile.items()}
                mixed={}
                for k in ROOT_ORDER:
                    p=(current[k]/magnitude)**2
                    mixed[k]=raw[k]*math.sqrt((1-weight)+weight*joint_profile[k]/p) if p else raw[k]
                raw_norm=math.hypot(*raw.values());mixed_norm=math.hypot(*mixed.values())
                candidate={k:mixed[k]*raw_norm/mixed_norm for k in ROOT_ORDER} if mixed_norm else raw
                if all(abs(v)<=1 for v in candidate.values()) and not response(candidate).receipt['projection']['clipped_axes']:
                    adjusted=candidate;decision='joint_axis_profile'
                else:decision='preserve_stimulus_boundary';weight=0.
    result={'version':VERSION,'mode':'joint-axis-profile','raw_stimulus':raw,'state':state,
        'current_predicted_delta':current,'references':rows,'reference_capacity':capacity,
        'similarity_support':support,'excess_fraction':excess,'proposed_factor':proposed_factor,
        'decision':decision,'factor':1.,'profile_weight':weight,'joint_profile':joint_profile,
        'adjusted_stimulus':adjusted,'predicted_adjusted_delta':dict(response(adjusted).delta_x_event),
        'authority':{'state_write':False,'send':False,'history_write':False}}
    result['fingerprint']=model_data_fingerprint(result)
    return result
