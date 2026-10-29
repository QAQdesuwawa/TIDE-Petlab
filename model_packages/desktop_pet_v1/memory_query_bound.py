"""Conservative rejection proof for the existing CX67 recall operator.
No time cutoff is invented: this proof covers every admissible historical
projection, including time-evolved DAG influence. Inconclusive queries still
use the unchanged historical operators.
"""
import math
from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.continuous_root_projection import PROJECTION_AXES,project_continuous_roots
from model_packages.cx_six_seven_v1.interaction_geometry import relationship_transition
from model_packages.cx_six_seven_v1.memory_recall_operators import _parameter_package
from model_packages.cx_six_seven_v1.full_v2_package import CxSixSevenFullV2Package
from model_packages.cx_six_seven_v1.v2_package import _validated_contribution,_constant_binding
VERSION='desktop-pet-recall-global-upper-bound-v1'


def upper_margin(vector,params):
    _version,_projection,radius,amplitude_weight,relation_weight=_parameter_package(params)
    if set(vector)!=set(PROJECTION_AXES):raise ValueError('bound requires the existing three axes')
    numbers=[float(vector[axis]) for axis in PROJECTION_AXES]
    if any(not math.isfinite(v) or abs(v)>1 for v in numbers):raise ValueError('invalid bounded projection')
    # H<=1, Q=||current||/radius, relation<=1. The original margin is
    # monotone in both amplitude and relation. Its upper value is obtained
    # at H=1 and relation=1: (1+w_a)*sqrt(Q)-w_a.
    q=math.sqrt(sum(v*v for v in numbers))/radius
    return (1+amplitude_weight)*math.sqrt(q)-amplitude_weight


def projection_for_query(package,query,context):
    current={k:v for k,v in query.items() if k!='budget_ms'}
    empty={'candidate_event_ids':[],'historical_live':[],**current}
    if 'shared_projection' in query:
        from model_packages.cx_six_seven_v1.autonomous_recall_package import CxSixSevenAutonomousRecallPackage
        # Retain the original settlement, source and shared-projection checks.
        CxSixSevenAutonomousRecallPackage.recall_history(package,payload=empty,context=context)
        projection=query['shared_projection']
    else:
        CxSixSevenFullV2Package.recall_history(package,payload=empty,context=context)
        event=_validated_contribution(query['event_contribution'],kind='event')
        projection=project_continuous_roots(transition=relationship_transition(event['x_before'],event['x_after']),
            constant_binding=_constant_binding(query['parameters']),params=package._package_params('shared_projection')).to_record()
    return projection


def current_upper_bound(package,query,context):
    projection=projection_for_query(package,query,context)
    params=package._package_params('actual_recall')
    upper=upper_margin(projection['vector'],params)
    # Conservative numerical slack only; ambiguous boundary values use the
    # original operator. This is not a new psychological threshold.
    if upper>=-64*math.ulp(1.0):return None
    return {'version':VERSION,'upper_margin':upper,'historical_magnitude_upper':1.0,'relation_upper':1.0,
        'current_projection_fingerprint':model_data_fingerprint(projection),
        'actual_recall_parameters_fingerprint':model_data_fingerprint(params),
        'all_historical_ages_covered':True,'source_validation':'original-empty-recall-invocation'}
