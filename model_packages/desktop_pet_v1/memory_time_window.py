"""Certified future-time envelope of the existing Phi + continuous projection.

This is an exclusion index, not another recall formula. Its intervals contain
all original readouts at ages >= a given age. Only a strictly negative upper
margin can remove a candidate. Persistent lever terms may yield no finite
expiry. Dependencies still require their original source validation.
"""
import math
from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.memory_projection import CanonicalMemoryProjection
from model_packages.cx_six_seven_v1.root_dynamics import flow_root_time,ROOT_ORDER,LEVER_AXES,HOUR_MS,ROOT_TIME_FLOW_VERSION
from model_packages.cx_six_seven_v1.continuous_root_projection import _parameter_package as projection_parameters,_AXIS_CONSTANTS,PROJECTION_AXES
from model_packages.cx_six_seven_v1.memory_recall_operators import _parameter_package as recall_parameters

VERSION='desktop-pet-phi-time-window-v1'
SLACK=256*math.ulp(1.)

def down(x):return math.nextafter(x,-math.inf)
def up(x):return math.nextafter(x,math.inf)

class I:
    __slots__=('lo','hi')
    def __init__(self,lo,hi=None):
        self.lo=float(lo);self.hi=float(lo if hi is None else hi)
        if not math.isfinite(self.lo) or not math.isfinite(self.hi) or self.lo>self.hi:raise ValueError('invalid finite interval')
    def __add__(self,other):
        other=as_i(other);return I(down(self.lo+other.lo),up(self.hi+other.hi))
    __radd__=__add__
    def __neg__(self):return I(-self.hi,-self.lo)
    def __sub__(self,other):return self+-as_i(other)
    def __rsub__(self,other):return as_i(other)+-self
    def __mul__(self,other):
        other=as_i(other);v=(self.lo*other.lo,self.lo*other.hi,self.hi*other.lo,self.hi*other.hi)
        return I(down(min(v)),up(max(v)))
    __rmul__=__mul__
    def __truediv__(self,other):
        other=as_i(other)
        if other.lo<=0<=other.hi:raise ValueError('interval divisor contains zero')
        return self*I(down(1/other.hi),up(1/other.lo))
    def square(self):return I(0 if self.lo<=0<=self.hi else max(0,down(min(self.lo*self.lo,self.hi*self.hi))),up(max(self.lo*self.lo,self.hi*self.hi)))
    def sqrt(self):return I(max(0,down(math.sqrt(max(0,self.lo)))),up(math.sqrt(max(0,self.hi))))
    def positive(self):return I(max(0,self.lo),max(0,self.hi))
    def magnitude(self):return self.square().sqrt()
    def tanh(self):return I(down(math.tanh(self.lo)),up(math.tanh(self.hi)))

def as_i(value):return value if isinstance(value,I) else I(value)
def minimum(a,b):return I(min(a.lo,b.lo),min(a.hi,b.hi))
def norm(values):return sum((x.square() for x in values),I(0)).sqrt()

def positive_smooth(x,epsilon):
    x=x.positive()
    # Monotone scalar function; avoid dependency widening in x*x/(x+eps).
    def f(v):return 0. if v==0 else v*v/(v+epsilon)
    return I(max(0,down(f(x.lo))),up(f(x.hi)))

def magnitude_smooth(x,epsilon):return (x.square()+epsilon*epsilon).sqrt()-epsilon

def geometry(x):return {'S':x['L_self']-x['L_opponent'],'T':x['L_self']+x['L_opponent'],
    'M':minimum(x['L_self'],x['L_opponent']),'R_net':x['V_attr']-x['V_rep'],'R_amb':x['V_attr']*x['V_rep']}

def conditions(x,g,eps):
    magnitude=magnitude_smooth(g['R_net'],eps).positive()
    need=positive_smooth(x['P_need'],eps)
    pressure=(positive_smooth(x['E_env'],eps)+positive_smooth(g['T'],eps))/3.
    return {'a1':positive_smooth(g['S'],eps),'a2':need/(1.+magnitude),'b1':pressure,'b2':need,
        'b3':positive_smooth(-g['S'],eps),'g1':pressure*positive_smooth(g['R_amb'],eps),'g2':need*(magnitude/(1.+magnitude))}

def projection_envelope(before,after,constants,projection_config):
    _version,eps,gain,scales=projection_parameters(projection_config)
    gb=geometry(before);ga=geometry(after);cb=conditions(before,gb,eps);ca=conditions(after,ga,eps)
    shock=norm([(ga[k]-gb[k])/scales[k] for k in scales])/math.sqrt(len(scales))
    shock=I(max(0,min(1,shock.lo)),max(0,min(1,shock.hi)))
    channels={}
    for key in constants:
        change=ca[key]-cb[key]
        if key.startswith('g'):combined=norm([ca[key],magnitude_smooth(change,eps).positive()+shock])/math.sqrt(2)
        else:combined=(ca[key]+change)/math.sqrt(2)
        channels[key]=constants[key]*combined
    return {axis:(sum((channels[k] for k in _AXIS_CONSTANTS[axis]),I(0))/math.sqrt(len(_AXIS_CONSTANTS[axis]))*gain).tanh() for axis in PROJECTION_AXES}


def _flow(constants,before,version,params):
    result=flow_root_time(constants,before,0,effective_constants_version=version,params=params)
    return result.receipt['rest_point'],result.receipt['effective_rate_per_hour']


def profile_for_node(package,node,edges):
    projection=CanonicalMemoryProjection.from_record(node['projection']);evolution=node['evolution_binding']
    if projection.event.event_id!=node['event_id']:raise ValueError('canonical source event differs')
    if projection.root_time_operator_version!=ROOT_TIME_FLOW_VERSION:raise ValueError('unsupported canonical Phi version')
    params=evolution['root_time_parameters']
    if params['version']!=projection.root_time_parameter_version:raise ValueError('canonical Phi parameter binding differs')
    if model_data_fingerprint(evolution['forward_readout_parameters'])!=model_data_fingerprint(package._package_params('memory_forward_readout')):raise ValueError('unsupported forward readout binding')
    rest,rates=_flow(projection.effective_constants,projection.x_before,projection.effective_constants_version,params)
    terms=[]
    for edge in edges:
        e=edge.evidence['edge_evidence']['evolution_binding']
        if model_data_fingerprint({k:v for k,v in e.items() if k!='fingerprint'})!=e['fingerprint']:raise ValueError('edge evolution fingerprint differs')
        if e['root_time_operator_version']!=ROOT_TIME_FLOW_VERSION:raise ValueError('unsupported edge Phi version')
        binding=e['constant_binding'];_,edge_rates=_flow(binding['effective_constants'],projection.x_before,binding['effective_constants_version'],e['root_time_parameters'])
        vector={k:float(edge.contribution_payload[k]) for k in ROOT_ORDER}
        if any(not math.isfinite(v) for v in vector.values()):raise ValueError('invalid frozen edge delta')
        terms.append({'delta':vector,'rates':dict(edge_rates)})
    profile={'before':dict(projection.x_before),'delta':{k:projection.x_after[k]-projection.x_before[k] for k in ROOT_ORDER},
        'rest':dict(rest),'rates':dict(rates),'constants':dict(projection.effective_constants),'terms':terms,
        'radius':float(evolution['forward_readout_parameters']['bound_radius']),
        'projection_config':dict(package._package_params('shared_projection'))}
    profile['fingerprint']=model_data_fingerprint(profile)
    return profile


def decay(rate,age):
    if math.isinf(age):return I(0)
    return I(0,up(math.exp(-float(rate)*(age/HOUR_MS))))


def future_magnitude_upper(profile,age_ms):
    if age_ms<0:raise ValueError('negative candidate age')
    before={};raw={}
    for axis in ROOT_ORDER:
        if axis in LEVER_AXES:
            before[axis]=I(profile['before'][axis]);raw[axis]=I(profile['delta'][axis])
        else:
            d=decay(profile['rates'][axis],age_ms)
            before[axis]=profile['rest'][axis]+(profile['before'][axis]-profile['rest'][axis])*d
            # Original Phi subtracts two rounded endpoints. Enclose that
            # roundoff in addition to the analytic exponential difference.
            error=8*math.ulp(max(1.,abs(profile['before'][axis]),abs(profile['rest'][axis])))
            raw[axis]=profile['delta'][axis]*d+I(-error,error)
        for term in profile['terms']:
            raw[axis]=raw[axis]+term['delta'][axis]*(I(1.) if axis in LEVER_AXES else decay(term['rates'][axis],age_ms))
    length=norm(list(raw.values()));radius=profile['radius']
    divisor=I(max(1,length.lo/radius),max(1,length.hi/radius))
    after={k:before[k]+raw[k]/divisor for k in ROOT_ORDER}
    vector=projection_envelope(before,after,profile['constants'],profile['projection_config'])
    return min(up(norm(list(vector.values())).hi/math.sqrt(len(PROJECTION_AXES))),1.+SLACK)


def expiry_age_ms(profile,current_magnitude,params,upper_age=None):
    _v,_p,radius,weight,_relation=recall_parameters(params)
    q=float(current_magnitude)
    if not math.isfinite(q) or q<0:raise ValueError('invalid current magnitude')
    def margin(age):return (1+weight)*math.sqrt(future_magnitude_upper(profile,age)*q)-weight
    if margin(0)<-SLACK:return 0
    if upper_age is not None and margin(upper_age)>=-SLACK:return None
    if upper_age is None and margin(math.inf)>=-SLACK:return None
    # The future envelopes are nested. We bisect their sufficient exclusion
    # condition, never the original (potentially non-monotone) recall predicate.
    hi=1 if upper_age is None else max(1,int(upper_age))
    while margin(hi)>=-SLACK:
        hi*=2
        if hi>2**63:return None # conservative failure to certify, never exclusion
    lo=0
    while hi-lo>1:
        mid=(lo+hi)//2
        if margin(mid)<-SLACK:hi=mid
        else:lo=mid
    return hi


class TimeWindow:
    def __init__(self,package,view,query,context,direct):
        from .memory_query_bound import projection_for_query
        from model_packages.cx_six_seven_v1.full_v2_package import _evaluation_time_ms
        self.package=package;self.view=view;self.direct=direct;self.at=_evaluation_time_ms(context)
        self.params=package._package_params('actual_recall')
        _v,_p,radius,_wa,_wr=recall_parameters(self.params)
        projection=projection_for_query(package,query,context)
        self.q=math.sqrt(sum(float(v)**2 for v in projection['vector'].values()))/radius
        if not hasattr(view.index,'_phi_time_profiles'):view.index._phi_time_profiles={}
        self.profiles=view.index._phi_time_profiles;self.boundaries={};self.exclusions=[]
        if not hasattr(view.index,'_phi_time_profile_groups'):view.index._phi_time_profile_groups={}
        self.groups=view.index._phi_time_profile_groups
    def exclusion(self,identity,position):
        from framework.model_api import freeze_model_payload
        key=self.direct.key(identity)
        edges=self.view.incoming(identity)
        # An original successful Full readout must have validated parent
        # snapshots/collinearity before a DAG node gains a pruning certificate.
        if edges and key not in self.direct.cache:return None
        profile=self.profiles.get(key)
        if profile is None:
            node=self.view.node(identity);group_key=None
            if not edges:
                from model_packages.cx_six_seven_v1.memory_numeric_contracts import MemoryEventRef
                from model_packages.cx_six_seven_v1.memory_projection import _identifier
                record=node['projection'];event=MemoryEventRef.from_record(record['event'])
                if event.event_id!=identity:raise ValueError('canonical source event differs')
                _identifier(record['stimulus_receipt_ref'],'stimulus receipt')
                _identifier(record['root_response_receipt_ref'],'response receipt')
                # Only event identity/time and artifact reference names differ.
                # All numerical material, schema and bindings remain in the key.
                group_key=model_data_fingerprint({'projection':{k:v for k,v in record.items() if k not in ('event','stimulus_receipt_ref','root_response_receipt_ref')},
                    'evolution':node['evolution_binding']['fingerprint'],'package':self.direct.parameter_key})
                profile=self.groups.get(group_key)
            if profile is None:
                profile=freeze_model_payload(profile_for_node(self.package,node,edges))
                if group_key is not None:self.groups[group_key]=profile
            self.profiles[key]=profile
        age=self.at-self.view.node(identity)['projection']['event']['committed_at_ms']
        if age<0:raise ValueError('future memory node is not a candidate')
        fp=profile['fingerprint'];entry=self.boundaries.get(fp)
        if entry is not None and entry['cutoff'] is not None:
            cutoff=entry['cutoff']
        else:
            if entry is not None and age<=entry['not_expired_through']:return None
            cutoff=expiry_age_ms(profile,self.q,self.params,upper_age=age)
            self.boundaries[fp]={'cutoff':cutoff,'not_expired_through':age}
        if cutoff is None or age<cutoff:return None
        proof={'profile_fingerprint':fp,'expiry_age_ms':cutoff,'birth_before_or_at_ms':self.at-cutoff,
            'first_position':position,'last_position':position}
        if self.exclusions and all(self.exclusions[-1][k]==proof[k] for k in ('profile_fingerprint','expiry_age_ms','birth_before_or_at_ms')) and position==self.exclusions[-1]['first_position']-1:
            self.exclusions[-1]['first_position']=position
        else:self.exclusions.append(proof)
        return proof
