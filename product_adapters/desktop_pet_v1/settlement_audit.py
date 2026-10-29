"""Small diagnostic snapshot from a verified transaction; no state authority."""
from framework.model_api import model_data_fingerprint

VERSION='desktop-pet-settlement-audit-v1'

def build(*,event,settled,fact):
    if fact.settlement_ref['acceptance_fingerprint']!=settled.acceptance.fingerprint:
        raise ValueError('settlement audit differs from accepted event')
    settlement=settled.settlement_execution.payload['settlement']
    aggregate=settled.aggregate_execution.payload['historical_aggregate']['aggregate']
    row={'version':VERSION,'event_id':event.event_id,'event_fingerprint':event.fingerprint,
        'fact_fingerprint':fact.fingerprint,'acceptance_fingerprint':settled.acceptance.fingerprint,
        'settlement_output_fingerprint':settled.settlement_execution.receipt.output_fingerprint,
        'aggregate_output_fingerprint':settled.aggregate_execution.receipt.output_fingerprint,
        'recall_output_fingerprint':settled.recall_execution.receipt.output_fingerprint,
        'recall_count':len(settled.recall.recalled_event_ids),
        'state_before':dict(settlement['state_before']),
        'state_after':dict(settlement['state_candidate']),
        'memory_delta':dict(aggregate['delta_x_memory'])}
    row['fingerprint']=model_data_fingerprint(row)
    return row

def verify(row,*,event,fact):
    if (row.get('version')!=VERSION or row.get('event_id')!=event.event_id
            or row.get('event_fingerprint')!=event.fingerprint or row.get('fact_fingerprint')!=fact.fingerprint
            or row.get('acceptance_fingerprint')!=fact.settlement_ref['acceptance_fingerprint']
            or row.get('fingerprint')!=model_data_fingerprint({k:v for k,v in row.items() if k!='fingerprint'})):
        raise ValueError('settlement audit source or integrity differs')
    return row
