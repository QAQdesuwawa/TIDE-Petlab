"""Opt-in audit export for an isolated test host; never a model consumer.

The original application must have finished durable settlement first. Exports
copy existing stage products and receipts, without another provider/model call.
Normal DesktopPetWindow startup does not activate this diagnostic hook.
"""
from contextlib import contextmanager
from dataclasses import dataclass,field
import json
from pathlib import Path
from unittest.mock import patch
from framework.model_api import model_data_fingerprint,thaw_model_data

VERSION='petlab-memory-diagnostic-v1'


def build_memory_diagnostic(result):
    full=result.transaction
    if result.replayed or full is None:return None
    transaction=full.event_transaction
    fact=result.memory.fact
    if fact.settlement_ref['acceptance_fingerprint']!=transaction.acceptance.fingerprint:
        raise ValueError('diagnostic fact differs from original transaction acceptance')
    executions={
        'measurement':transaction.measurement_execution,
        'event_contribution':transaction.event_contribution_execution,
        'recall':transaction.recall_execution,
        'influence':transaction.influence_execution,
        'aggregate':transaction.aggregate_execution,
        'settlement':transaction.settlement_execution,
    }
    stages={}
    for name,execution in executions.items():
        receipt=execution.receipt.to_record();payload=thaw_model_data(execution.payload)
        if receipt['output_fingerprint']!=model_data_fingerprint(payload):raise ValueError('diagnostic stage output differs from original receipt')
        stages[name]={'receipt':receipt,'payload':payload}
    record={'version':VERSION,'source_fact_id':fact.fact_id,'source_fact_fingerprint':fact.fingerprint,
        'source_event_fingerprint':result.memory.event.fingerprint,
        'acceptance_fingerprint':transaction.acceptance.fingerprint,
        'accepted_runtime_ordinal':fact.accepted_runtime_ordinal,
        'context':transaction.context.to_record(),
        'history_read':None if transaction.history_read is None else transaction.history_read.to_record(),
        'transaction_receipt':thaw_model_data(full.receipt),'event_transaction_receipt':thaw_model_data(transaction.receipt),
        'candidate_authorization':thaw_model_data(full.candidate_authorization.receipt),
        'memory_input':{'candidate_event_ids':list(full.memory_input_product.candidate_event_ids),
            'receipt':thaw_model_data(full.memory_input_product.receipt),
            'recall_payload':thaw_model_data(full.memory_input_product.memory_recall_payload)},
        'stages':stages,
        'diagnostic_authority':False,'state_commit_authority':False,'memory_commit_authority':False}
    return dict(record,fingerprint=model_data_fingerprint(record))


@dataclass
class DiagnosticCapture:
    records:list=field(default_factory=list)
    written_paths:list=field(default_factory=list)
    errors:list=field(default_factory=list)


@contextmanager
def capture_memory_diagnostics(directory):
    """Activate only in the caller's isolated serial test process.

    A diagnostic storage failure is recorded separately after the original
    settlement succeeds. It cannot make an accepted event appear to fail or
    trigger another business/provider invocation.
    """
    from .session import SinglePetApplication
    capture=DiagnosticCapture();directory=Path(directory)
    original=SinglePetApplication._settle
    def settle(application,*args,**kwargs):
        result=original(application,*args,**kwargs)
        try:
            record=build_memory_diagnostic(result)
            if record is not None:
                capture.records.append(record);directory.mkdir(parents=True,exist_ok=True)
                path=directory/(record['fingerprint']+'.json')
                path.write_text(json.dumps(record,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
                capture.written_paths.append(str(path))
        except Exception as error:
            capture.errors.append({'source_fact_id':result.memory.fact.fact_id,'error_type':type(error).__name__,'error':str(error)})
        return result
    with patch.object(SinglePetApplication,'_settle',settle):yield capture


def validate_memory_diagnostic(record,*,session_record):
    """Check an archived export against its actual committed source history."""
    from framework.history import HistoryAuthority
    if record.get('version')!=VERSION or record.get('fingerprint')!=model_data_fingerprint({k:v for k,v in record.items() if k!='fingerprint'}):raise ValueError('diagnostic integrity differs')
    history_record=session_record['profile']['framework_history']
    history=HistoryAuthority.from_record(history_record)
    fact=history.fact(record['source_fact_id'])
    if fact.fingerprint!=record['source_fact_fingerprint'] or fact.accepted_runtime_ordinal!=record['accepted_runtime_ordinal'] or fact.settlement_ref['acceptance_fingerprint']!=record['acceptance_fingerprint']:raise ValueError('diagnostic committed source differs')
    event=session_record['profile']['desktop_pet_events_v1'][fact.fact_id]['event']
    if event['fingerprint']!=record['source_event_fingerprint']:raise ValueError('diagnostic event source differs')
    if record['context']['event_ref']!=fact.fact_id:raise ValueError('diagnostic context source differs')
    for stage in record['stages'].values():
        if stage['receipt']['output_fingerprint']!=model_data_fingerprint(stage['payload']):raise ValueError('diagnostic stage output differs')
    execution=record['stages']['recall'];decision=execution['payload']['recall_decision']
    actual=[item for item in history.actual_recalls() if item.trigger_fact_id==fact.fact_id]
    if len(actual)!=1:raise ValueError('diagnostic lacks one formal actual recall')
    recall=actual[0]
    if (recall.model_stage_receipt_fingerprint!=model_data_fingerprint(execution['receipt'])
            or recall.model_output_fingerprint!=execution['receipt']['output_fingerprint']
            or set(recall.recalled_fact_ids)!=set(decision['recalled_event_ids'])):raise ValueError('diagnostic differs from formal recall')
    read=record['history_read']
    if read is not None and not any(item['fingerprint']==read['fingerprint'] and item==read for item in history_record['reads']):raise ValueError('diagnostic history authorization differs')
    if read is not None and set(read['fact_ids'])!=set(decision['candidate_event_ids']):raise ValueError('diagnostic candidate set differs')
    return {'source_fact_id':fact.fact_id,'candidate_count':len(decision['candidate_event_ids']),
        'recalled_fact_ids':list(recall.recalled_fact_ids),'verified_against_committed_history':True}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='Readonly verification of original memory stage exports')
    parser.add_argument('--session',required=True,type=Path);parser.add_argument('--capture-dir',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args(argv)
    session=json.loads(args.session.read_text(encoding='utf-8'))
    rows=[validate_memory_diagnostic(json.loads(p.read_text(encoding='utf-8')),session_record=session) for p in sorted(args.capture_dir.glob('*.json'))]
    if not rows:raise ValueError('no original memory diagnostic records found')
    report={'version':VERSION,'source_session':str(args.session.resolve()),'verified_records':rows,'new_provider_calls':0,'state_modified':False}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'verified_records':len(rows),'new_provider_calls':0,'state_modified':False}))

if __name__=='__main__':main()
