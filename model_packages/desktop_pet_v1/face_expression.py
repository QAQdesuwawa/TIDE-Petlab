"""Presentation choices only; no emotion measurement or scheduling authority."""
from collections.abc import Mapping
VERSION="desktop-pet-face-expression-v1"
CODES=("normal","happy","sad","aggrieved","angry")
LABELS={"normal":"正常","happy":"高兴","sad":"难过","aggrieved":"委屈","angry":"愤怒"}
def validate(value):
    from .spatial import PRESENTATION,validate_gaze
    if isinstance(value,Mapping) and value.get('version')==PRESENTATION:
        if set(value)!={'version','expression','gaze'} or value['expression'] not in CODES:raise ValueError('invalid face gaze presentation')
        validate_gaze(value['gaze'])
        return value['expression']
    if not isinstance(value,Mapping) or set(value)!={"version","expression"} or value["version"]!=VERSION or value["expression"] not in CODES:
        raise ValueError("invalid face expression presentation")
    return value["expression"]
