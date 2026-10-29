"""Reuse native settlement integrity checks across read-only host consumers.

New native acceptances undergo the original Kernel constructor validation. The cache is only
an integrity memo, never a state/Commit/send permission or a proof substitute.
Actual content is compared, so a declared fingerprint cannot manufacture a hit.
"""
from collections import OrderedDict
from threading import RLock
from framework.model_api import model_data_fingerprint
from framework.runtime_kernel import (RuntimeStateCommitAcceptance,RuntimeSessionBinding,
    RuntimeModelState,RuntimePositionState,RuntimeStateCommitAuthority)

_CACHE=OrderedDict()
_LOCK=RLock()
_LIMIT=64


def verify_settlement(acceptance, *, session_binding, previous_state, previous_position):
    validator=type(acceptance).verify_integrity
    kwargs=dict(session_binding=session_binding,previous_state=previous_state,
        previous_position=previous_position)
    if (type(acceptance) is not RuntimeStateCommitAcceptance
            or type(session_binding) is not RuntimeSessionBinding
            or type(previous_state) is not RuntimeModelState
            or type(previous_position) is not RuntimePositionState
            or type(acceptance.state) is not RuntimeModelState
            or type(acceptance.position) is not RuntimePositionState):
        return validator(acceptance,**kwargs)
    b=session_binding
    actual=_actual(acceptance,session_binding,previous_state,previous_position)
    key=(id(acceptance),validator)
    with _LOCK:
        entry=_CACHE.get(key)
        if entry is not None and entry[0] is acceptance and entry[1] is b and entry[2]==actual:
            _CACHE.move_to_end(key)
            return None
        validator(acceptance,**kwargs)
        _CACHE[key]=(acceptance,b,actual)
        _CACHE.move_to_end(key)
        while len(_CACHE)>_LIMIT:_CACHE.popitem(last=False)


_NATIVE_ACCEPT=RuntimeStateCommitAuthority.accept_settlement
_NATIVE_CREATE=RuntimeStateCommitAcceptance.create.__func__
_NATIVE_VERIFY=RuntimeStateCommitAcceptance.verify_integrity


def _actual(acceptance,session_binding,previous_state,previous_position):
    b=session_binding
    return model_data_fingerprint({'binding':{'session_id':b.session_id,
        'model_binding':b.model_binding.to_record(),'temporal_binding':b.temporal_binding.to_record(),
        'runtime_variant':b.runtime_variant.to_record(),'fingerprint':b.fingerprint},
        'previous_state':previous_state.to_record(),'previous_position':previous_position.to_record(),
        'acceptance':acceptance.to_record()})



def accept_settlement(authority,**kwargs):
    """Admit only a fresh result of the original Kernel constructor.

    The constructor has already validated these exact four state/position
    values. External or overridden authorities still take the normal verifier.
    Every later use compares complete actual content with this snapshot.
    """
    native=(type(authority) is RuntimeStateCommitAuthority
        and set(vars(authority))=={'session_binding'}
        and getattr(authority.accept_settlement,'__func__',None) is _NATIVE_ACCEPT
        and type(authority).accept_settlement is _NATIVE_ACCEPT
        and RuntimeStateCommitAcceptance.create.__func__ is _NATIVE_CREATE
        and RuntimeStateCommitAcceptance.verify_integrity is _NATIVE_VERIFY)
    acceptance=authority.accept_settlement(**kwargs)
    if (native and set(vars(authority))=={'session_binding'}
            and getattr(authority.accept_settlement,'__func__',None) is _NATIVE_ACCEPT
            and type(authority).accept_settlement is _NATIVE_ACCEPT
            and RuntimeStateCommitAcceptance.create.__func__ is _NATIVE_CREATE
            and type(acceptance) is RuntimeStateCommitAcceptance
            and type(authority.session_binding) is RuntimeSessionBinding
            and all(type(value) is cls for value,cls in (
                (kwargs['current_state'],RuntimeModelState),(kwargs['current_position'],RuntimePositionState),
                (acceptance.state,RuntimeModelState),(acceptance.position,RuntimePositionState)))):
        actual=_actual(acceptance,authority.session_binding,kwargs['current_state'],kwargs['current_position'])
        with _LOCK:
            _CACHE[(id(acceptance),_NATIVE_VERIFY)]=(acceptance,authority.session_binding,actual)
            _CACHE.move_to_end((id(acceptance),_NATIVE_VERIFY))
            while len(_CACHE)>_LIMIT:_CACHE.popitem(last=False)
    return acceptance
