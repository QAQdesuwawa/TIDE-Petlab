"""Operational memory evidence; detailed evidence is rebuilt from frozen sources.

No process-global cache and no new memory/state deltas. The signed stage input
already binds the canonical source. These references retain its exact identity
and all replay coordinates without embedding a calculation transcript per row.
"""
from dataclasses import replace
from framework.model_api import model_data_fingerprint
from .memory_topology import CONTEXT_KEY, order_for

VERSION = "desktop-pet-memory-runtime-reference-v2"

def enabled(context):
    return context.metadata.get(CONTEXT_KEY) == VERSION

def readout_format(context):
    from .memory_batch import enabled as batch_enabled
    return "source-reference-v1" if enabled(context) or batch_enabled(context) else "full"

def _seal(record):
    return model_data_fingerprint({k:v for k,v in record.items() if k != "runtime_fingerprint"})

def compact(result, payload, context):
    inputs=result['memory_inputs']; rows=inputs['candidates']
    order=order_for([row['influence_input'] for row in rows])
    source=model_data_fingerprint(payload)
    group={'version':VERSION,'fingerprint':model_data_fingerprint(order),'node_count':len(order)}
    candidates=[]
    for row in rows:
        live=row['influence_input']; receipt=live['receipt']
        operational={k:v for k,v in receipt.items() if k!='intrinsic_term'}
        operational.update(source_input_fingerprint=source, prepared_nodes=group,
                           temporal_position_fingerprint=context.temporal_position.fingerprint)
        readout={**live,'receipt':operational}
        operational['runtime_fingerprint']=_seal(readout)
        recall=row['recall_input'];projection=recall['projection']
        # The detailed projection terms are diagnostic only; recall consumes
        # vector/formula/binding. The live locator plus source rebuilds them.
        projection={**projection,'receipt':{'version':VERSION,'source_input_fingerprint':source}}
        candidates.append({**row,'recall_input':{**recall,'projection':projection},'influence_input':readout})
    return {**result,'memory_inputs':{**inputs,'version':VERSION,
            'candidates':candidates}}

def verify(readouts, context):
    order=order_for(readouts)
    expected={'version':VERSION,'fingerprint':model_data_fingerprint(order),'node_count':len(order)}
    source=None
    for row in readouts:
        receipt=row['receipt']; fingerprint=receipt.get('source_input_fingerprint')
        if (not isinstance(fingerprint,str) or len(fingerprint)!=64
                or receipt.get('prepared_nodes')!=expected
                or receipt.get('temporal_position_fingerprint')!=context.temporal_position.fingerprint
                or receipt.get('runtime_fingerprint')!=_seal({**row,'receipt':{k:v for k,v in receipt.items() if k!='runtime_fingerprint'}})):
            raise ValueError('runtime memory evidence differs from prepared source')
        if source is not None and source!=fingerprint:
            raise ValueError('runtime memory evidence mixes source inputs')
        source=fingerprint

def rebuild_evidence(*, package, payload, context, prepared):
    inputs=prepared['memory_inputs']
    source=model_data_fingerprint(payload)
    if inputs.get('version')!=VERSION or any(row['influence_input']['receipt'].get('source_input_fingerprint')!=source for row in inputs['candidates']):
        raise ValueError('memory evidence replay source mismatch')
    # Explicit diagnostic path: same immutable source/time/ordinal/parameters,
    # legacy complete representation. Does not write or call any provider.
    metadata={k:v for k,v in context.metadata.items() if k!=CONTEXT_KEY}
    legacy=replace(context,metadata=metadata)
    detailed=package.prepare_memory_inputs(payload=payload,context=legacy)
    # Bind the reconstructed numeric result as well as the source locator.
    regenerated=package.prepare_memory_inputs(payload=payload,context=context)
    if regenerated!=prepared:
        raise ValueError('memory evidence replay differs from prepared values')
    return detailed
