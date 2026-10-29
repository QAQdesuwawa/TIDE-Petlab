"""Versioned references for duplicate topology evidence; no numerical changes."""
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.memory_runtime_order import readout_order

VERSION='desktop-pet-memory-topology-reference-v1'
CONTEXT_KEY='desktop_pet_memory_evidence'
REFERENCE_KEY='topological_order_reference'

def enabled(context):
    value=context.metadata.get(CONTEXT_KEY)
    if value is not None and value!=VERSION:
        raise ValueError('unsupported topology evidence format')
    return value==VERSION

def order_for(readouts):
    entries=[];ids=set();ordinals=[]
    for row in readouts:
        event=row['event'];identity=event['event_id']
        if identity in ids:raise ValueError('duplicate topology node')
        ids.add(identity);ordinal=readout_order(row['receipt']);ordinals.append(ordinal)
        entries.append((identity,event['committed_at_ms'],ordinal))
    if any(o is not None for o in ordinals):
        if any(o is None for o in ordinals) or len(set(ordinals))!=len(ordinals):
            raise ValueError('inconsistent topology ordinals')
        return tuple(x[0] for x in sorted(entries,key=lambda x:(x[1],x[2])))
    return tuple(x[0] for x in sorted(entries,key=lambda x:(x[1],x[0])))

def reference(order):
    return {'version':VERSION,'fingerprint':model_data_fingerprint(order),'node_count':len(order)}

def compact(result):
    inputs=result['memory_inputs'];rows=inputs['candidates']
    order=order_for([row['influence_input'] for row in rows]);ref=reference(order)
    candidates=[]
    for row in rows:
        readout=row['influence_input'];receipt=readout['receipt']
        if tuple(receipt.get('topological_order',()))!=order:
            raise ValueError('source topology differs from prepared node set')
        compact_receipt={k:v for k,v in receipt.items() if k!='topological_order'}
        compact_receipt[REFERENCE_KEY]=dict(ref)
        candidates.append({**row,'influence_input':{**readout,'receipt':compact_receipt}})
    return {**result,'memory_inputs':{**inputs,'version':VERSION,'candidates':candidates}}

def verify(readouts,context):
    mode=enabled(context)
    if not mode:
        if any(REFERENCE_KEY in row.get('receipt',{}) for row in readouts):
            raise ValueError('compact topology requires its explicit context version')
        return
    expected=reference(order_for(readouts))
    for row in readouts:
        receipt=row['receipt'];ref=receipt.get(REFERENCE_KEY)
        if ('topological_order' in receipt or not isinstance(ref,Mapping)
                or set(ref)!=set(expected) or type(ref.get('node_count')) is not int
                or dict(ref)!=expected):
            raise ValueError('topology reference differs from complete prepared nodes')
