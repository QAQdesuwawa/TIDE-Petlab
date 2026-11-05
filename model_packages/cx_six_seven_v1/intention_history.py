"""Private, append-only lifecycle evidence for accepted expression intentions.

This is an intermediate record, not an executed action, a fresh authorization,
or a numerical memory contribution. Consumers must preserve that distinction.
"""
from framework.model_api import model_data_fingerprint,thaw_model_data

# Preserve the existing intermediate-record wire identity; package ownership
# does not silently migrate already bound archives.
KEY='desktop-pet-intention-history-v1'

def extend(previous,*,owner,source,grant,eligible,status=None):
    previous=previous or {'version':KEY,'items':{}}
    data={'version':previous['version'],'items':dict(previous['items'])}
    if data['version']!=KEY:raise ValueError('unknown intention history')
    if not eligible:return data
    identity=model_data_fingerprint({'owner':owner,'source':source})
    item=data['items'].get(identity)
    if item is None:
        item={'owner':owner,'source':source,'visibility':'self','grant':grant,'transitions':[]}
        data['items'][identity]=item
    else:
        item=dict(thaw_model_data(item))
        item['transitions']=list(item['transitions'])
        data['items'][identity]=item
    if item['grant']!=grant:raise ValueError('intention grant changed')
    for value in ('formed',status):
        if value is None or any(r['status']==value for r in item['transitions']):continue
        row={'status':value,'previous':item['transitions'][-1]['fingerprint'] if item['transitions'] else None}
        item['transitions'].append({**row,'fingerprint':model_data_fingerprint(row)})
    return data
