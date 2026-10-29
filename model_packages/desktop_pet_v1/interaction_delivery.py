"""Source-bound local geometry projection for the explicit observation wire."""
from collections.abc import Mapping
import json
from framework.model_api import thaw_model_data,model_data_fingerprint
from .trajectory_geometry import summarize_trajectory
from .trajectory_features import summarize_features

CAPTURE_VERSION="petlab-mouse-observation-v2"
BOUNDED_CAPTURE_VERSION="petlab-mouse-observation-v3"
CONTACT_CAPTURE_VERSION="petlab-mouse-contact-v4"
MAX_MODEL_PARAMETERS_CHARS=4096
def project_parameters(parameters):
    if parameters.get('capture_version')=='petlab-mouse-batch-v5':
        from .mouse_batch import validate_batch
        data=validate_batch(parameters)
        result={k:v for k,v in data.items() if k not in {'parts','trajectory'}}
        if len(json.dumps(result,ensure_ascii=False,allow_nan=False))>MAX_MODEL_PARAMETERS_CHARS:
            # Validate full evidence above; only the delivered metadata is compacted.
            from .mouse_batch import bound_summary
            summary=result['summary']
            compact=bound_summary({'交互摘要':'批次操作定义与部位摘要。','操作含义':summary['definitions'],
                '起始部位':summary['targets'],'事实边界':'完整定义与逐次证据保存在本地事件。'})
            result['summary']={**summary,'definitions':compact['操作含义'],'targets':compact['起始部位']}
            result['summary_delivery']=compact.get('摘要收敛',{})
            result['summary_source_fingerprint']=model_data_fingerprint(summary)
            if len(json.dumps(result,ensure_ascii=False,allow_nan=False))>MAX_MODEL_PARAMETERS_CHARS:
                raise ValueError('fixed batch feature payload exceeds 4096 character limit')
        return result
    data=thaw_model_data(parameters)
    version=data.get("capture_version")
    if version not in {CAPTURE_VERSION, BOUNDED_CAPTURE_VERSION, CONTACT_CAPTURE_VERSION}:return data
    if version==CONTACT_CAPTURE_VERSION:
        if (any(isinstance(data.get(k),bool) or not isinstance(data.get(k),int)
                for k in ('sequence','interval_duration_ms','total_duration_ms'))
                or data.get('phase')!='started' or data.get('sequence')!=1
                or len(data['trajectory'])!=1 or data.get('interval_duration_ms')!=0
                or data.get('total_duration_ms')!=0 or data.get('gesture')!='press_release'):
            raise ValueError('contact onset must contain exactly its actual press point')
        summary=summarize_features(data['trajectory'],allow_point=True)
    else:
        summary=(summarize_features if version==BOUNDED_CAPTURE_VERSION else summarize_trajectory)(data["trajectory"])
    if 'drag_slop_px' in data:
        slop=data['drag_slop_px'];points=data['trajectory'];first=points[0]
        if not isinstance(slop,(list,tuple)) or len(slop)!=2 or any(type(v) is not int or v<0 for v in slop):raise ValueError('interaction drag slop invalid')
        moved=any(abs(x-first[0])>slop[0] or abs(y-first[1])>slop[1] for x,y,_ in points)
        # Later intervals can be stationary while the original full gesture was already dragged.
        if moved and data.get('gesture')!='drag':raise ValueError('drag classification differs from capture')
    if model_data_fingerprint(summary)!=model_data_fingerprint(data["geometry_summary"]):
        raise ValueError("interaction geometry differs from physical trajectory")
    data.pop("trajectory")
    if version in {BOUNDED_CAPTURE_VERSION,CONTACT_CAPTURE_VERSION}:
        # The feature schema has no sample-indexed or stop-indexed collections.
        # Keep a hard wire bound even for unusual custom definitions/numbers.
        if len(json.dumps(data,ensure_ascii=False,allow_nan=False))>MAX_MODEL_PARAMETERS_CHARS:
            raise ValueError('mouse feature payload exceeds 4096 character limit')
        return data
    data["raw_capture_delivery"]={"omitted":True,"reason":"ordered geometry summary; raw points remain in accepted local event",
        "source_sha256":summary["source_sha256"],"source_point_count":summary["source_point_count"],"tolerance_px":summary["tolerance_px"]}
    return data

def has_observations(contents):
    return any(item.kind=="interaction" and item.parameters.get("capture_version") in {CAPTURE_VERSION,BOUNDED_CAPTURE_VERSION,CONTACT_CAPTURE_VERSION,'petlab-mouse-batch-v5'} for item in contents)


def has_body_targets(contents):
    return any(item.kind=='interaction' and item.parameters.get('body_target') is not None for item in contents)
