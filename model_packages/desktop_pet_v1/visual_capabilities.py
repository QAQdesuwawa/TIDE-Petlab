"""Read-only selectable presentation vocabulary; no roots or action authority."""
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint,thaw_model_data
from .face_expression import CODES,LABELS,VERSION as FACE_VERSION
VERSION='desktop-pet-visual-capabilities-v1'


def create(*,codes,asset_sha256,descriptions=None):
    if not isinstance(codes,(list,tuple)) or not codes or any(not isinstance(c,str) or c not in CODES for c in codes) or len(set(codes))!=len(codes) or 'normal' not in codes:
        raise ValueError('visual capability choices invalid')
    if not isinstance(asset_sha256,str) or len(asset_sha256)!=64 or any(c not in '0123456789abcdef' for c in asset_sha256):raise ValueError('visual capability asset source invalid')
    descriptions={} if descriptions is None else descriptions
    if not isinstance(descriptions,Mapping) or not set(descriptions)<=set(codes) or any(not isinstance(v,str) or not v.strip() for v in descriptions.values()):raise ValueError('visual capability descriptions invalid')
    choices={c:LABELS[c] for c in CODES if c in codes}
    body={'version':VERSION,'presentation_contract':FACE_VERSION,'asset_sha256':asset_sha256,
        'choices':choices,'descriptions':{c:descriptions.get(c,LABELS[c]+'的完整表情画面') for c in choices}}
    return {**body,'fingerprint':model_data_fingerprint(body)}


def validate(value):
    if value is None:return None
    if not isinstance(value,Mapping) or set(value)!={'version','presentation_contract','asset_sha256','choices','descriptions','fingerprint'} or not isinstance(value['choices'],Mapping):raise ValueError('visual capability fields invalid')
    expected=create(codes=tuple(value['choices']),asset_sha256=value['asset_sha256'],descriptions=value['descriptions'])
    if thaw_model_data(value)!=expected:raise ValueError('visual capability fingerprint or vocabulary differs')
    return expected


def presentation_contract(value):
    if value is None:return {'version':FACE_VERSION,'choices':dict(LABELS)}
    value=validate(value)
    return {'version':FACE_VERSION,'choices':value['choices'],'descriptions':value['descriptions']}
