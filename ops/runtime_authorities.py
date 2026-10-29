"""Reuse the original stateless Kernel authority instances for an exact session.

No Kernel method is patched/replaced. All position/input/commit checks still run
at their existing callers. Invalid actual fields or bindings cannot get a hit.
"""
from collections import OrderedDict
from threading import RLock
from framework.model_api import model_data_fingerprint
from framework.runtime_kernel import RuntimeSessionBinding,RuntimeStateCommitAuthority,RuntimeProgressAuthority
from framework.temporal_runtime import BoundTemporalDomain

_CACHE=OrderedDict();_LOCK=RLock()


def runtime_authorities(binding,temporal):
    if type(binding) is not RuntimeSessionBinding or type(temporal) is not BoundTemporalDomain:
        return (RuntimeStateCommitAuthority(session_binding=binding),
            RuntimeProgressAuthority(session_binding=binding,temporal_runtime=temporal))
    actual=model_data_fingerprint({'session_id':binding.session_id,
        'model_binding':binding.model_binding.to_record(),'temporal_binding':binding.temporal_binding.to_record(),
        'runtime_variant':binding.runtime_variant.to_record(),'fingerprint':binding.fingerprint,
        'actual_temporal':temporal.binding.to_record()})
    key=(id(binding),id(temporal),actual,RuntimeStateCommitAuthority.__init__,RuntimeProgressAuthority.__init__)
    with _LOCK:
        cached=_CACHE.get(key)
        if cached is not None:
            state,progress=cached
            if (type(state) is RuntimeStateCommitAuthority and type(progress) is RuntimeProgressAuthority
                    and set(vars(state))=={'session_binding'} and state.session_binding is binding
                    and set(vars(progress))=={'session_binding','temporal_runtime'}
                    and progress.session_binding is binding and progress.temporal_runtime is temporal):
                _CACHE.move_to_end(key)
                return cached
        pair=(RuntimeStateCommitAuthority(session_binding=binding),
            RuntimeProgressAuthority(session_binding=binding,temporal_runtime=temporal))
        _CACHE[key]=pair;_CACHE.move_to_end(key)
        while len(_CACHE)>8:_CACHE.popitem(last=False)
        return pair
