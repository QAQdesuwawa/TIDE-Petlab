"""Host-local reuse of a projection after its source has been verified.

Both callers validate their source first. The key reads the complete effective
inputs, never caller-supplied fingerprint fields alone. No state or send rights.
"""
from dataclasses import replace
from framework.model_api import model_data_fingerprint,freeze_model_payload

VERSION='pet-verified-projection-reuse-v1'

def reuse(app,*,previous_state,previous_position,parameters,context,execution,acceptance,remainders,factory):
    def record(value):return value.to_record() if hasattr(value,'to_record') else value
    values={'previous_state':record(previous_state),'previous_position':record(previous_position),
        'parameters':record(parameters),'context':record(context),'execution':execution,
        'acceptance':record(acceptance),'remainders':remainders,
        'runtime_binding':record(app.runtime.model_runtime.binding),
        'configured_scope':getattr(getattr(app,'definition',None),'scope_ref',None)}
    key=(id(app.runtime.model_runtime),model_data_fingerprint(values))
    cache=getattr(app,'_verified_projection_products',{})
    if key in cache:return cache[key]
    result=factory()
    result=replace(result,receipt=freeze_model_payload(result.receipt))
    if len(cache)>=8:cache.pop(next(iter(cache)))
    cache[key]=result;app._verified_projection_products=cache
    return result
