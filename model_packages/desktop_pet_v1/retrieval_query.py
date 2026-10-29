"""Read-only retrieval text from the existing, source-verified numeric cue."""
from framework.model_api import model_data_fingerprint
from .grounded_recall import VERSION as GROUNDED_VERSION,make_cue

VERSION='desktop-pet-shared-source-query-v1'


def query_basis(event,parsed=None):
    text='\n'.join(c.description for c in event.visible_contents(event.owner_ref) if c.kind=='speech')
    if parsed is not None and parsed.get('version')=='desktop-pet-property-address-recall-v8':
        from .address_recall import make_cue as property_cue,addresses
        cue=property_cue(event,parsed)
        paths=addresses(cue['properties'])
        details=[{'对象':p['owner'],'属性':list(p['path']),'值':p['value'],'类型':p['kind']} for p in cue['properties']]
        import json
        record={'version':'desktop-pet-shared-property-query-v2','event_fingerprint':event.fingerprint,'source_ref':event.source_binding_ref,
            'parsed_fingerprint':model_data_fingerprint(parsed),'cue':cue,'addresses':paths,
            'text':text+'\n属性查询：'+json.dumps(details,ensure_ascii=False,separators=(',',':')),'state_commit_authority':False}
        return {**record,'fingerprint':model_data_fingerprint(record)}
    cue=make_cue(event,parsed) if parsed is not None and parsed.get('version')==GROUNDED_VERSION else None
    extra=[p for p in cue['phrases'] if p not in text] if cue is not None else []
    query=text+('\n已定位的指代词：'+'；'.join(extra) if extra else '')
    record={'version':VERSION,'event_fingerprint':event.fingerprint,'source_ref':event.source_binding_ref,
            'parsed_fingerprint':model_data_fingerprint(parsed) if parsed is not None else None,
            'cue':cue,'text':query,'state_commit_authority':False}
    return {**record,'fingerprint':model_data_fingerprint(record)}
