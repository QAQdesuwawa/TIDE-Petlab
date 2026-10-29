"""Asset vocabulary only. Existing discrete descriptors, no root/score access."""
import json
from pathlib import Path
from framework.model_api import model_data_fingerprint
from .relation_presentation import VERSION as RELATION_VERSION, validate_entry

def expression_for(tokens):
    vocabulary=json.loads((Path(__file__).parent/'resources/settled-expression.json').read_text(encoding='utf-8'))
    for rule in vocabulary['rules']:
        if any(all(token.get(key)==rule[key] for key in ('axis','orientation','prominence')) for token in tokens):
            return rule['expression']
    return vocabulary['default']

def validate_snapshot(value):
    if (value.get('version') not in {'desktop-pet-settled-presentation-v1','desktop-pet-settled-presentation-v2',RELATION_VERSION}
        or type(value.get('ordinal')) is not int
        or value.get('fingerprint')!=model_data_fingerprint({k:v for k,v in value.items() if k!='fingerprint'})):
        raise ValueError('settled presentation receipt differs')
    if value['version'] in {'desktop-pet-settled-presentation-v2',RELATION_VERSION}:
        from .reaction import validate
        reaction=validate(value['reaction'])
        if reaction['source']!=value['settlement_fingerprint'] or reaction['ordinal']!=value['ordinal']:
            raise ValueError('reaction differs from snapshot source')
    if value['version']==RELATION_VERSION:
        entries=value.get('relation_presentations',{})
        if value.get('target_ref') not in entries or value.get('display_target_ref') not in entries:
            raise ValueError('snapshot target is unavailable')
        for peer,item in entries.items():
            validate_entry(item,session_binding=value['session_binding'],owner_ref=value['owner_ref'],target_ref=peer)
            if item['reaction']['ordinal']>value['ordinal']:raise ValueError('future relation presentation')
        current=entries[value['target_ref']]
        if current['reaction']!=value['reaction'] or current['source_kind']!=value['source_kind'] or current['source_ref']!=value['source_ref']:
            raise ValueError('snapshot relation source differs')
    return value


def expression_from_snapshot(value):
    validate_snapshot(value)
    if value['version'] in {'desktop-pet-settled-presentation-v2',RELATION_VERSION}:
        return value['reaction']['feedback']['expression']['token']
    return expression_for(value['tokens'])
