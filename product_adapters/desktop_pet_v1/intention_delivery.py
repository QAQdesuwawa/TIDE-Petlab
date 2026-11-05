"""Annotate only an already selected private memory, using its own evidence."""
from framework.model_api import model_data_fingerprint as fp
from model_packages.cx_six_seven_v1.intention_facts import VERSION as FACTS,validate
from model_packages.cx_six_seven_v1.intention_presentation import VERSION,PHASE_LABELS,REASON_LABELS
from model_packages.desktop_pet_v1.events import PetEvent
from petlab_app.intention_progress import PROOFS
from petlab_app.intention_source import ORIGIN

FIELD='自身意图进度'

from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
from threading import RLock
_PLAN_CACHE=OrderedDict()
_PLAN_LOCK=RLock()


def planned_contents(proof):
    """Read one sealed candidate; the history keeps a reference, not a copy."""
    if 'candidate_archive' not in proof:return None  # Earlier evidence had no locator.
    import json
    from pathlib import Path
    path=Path(proof['candidate_archive'])
    raw=path.read_bytes()
    key=(str(path.resolve()),proof['archive_kind'],sha256(raw).digest(),
         proof['candidate_fingerprint'],proof['candidate_ref'])
    with _PLAN_LOCK:
        if key in _PLAN_CACHE:
            _PLAN_CACHE.move_to_end(key)
            return deepcopy(_PLAN_CACHE[key])
    if proof['archive_kind']=='local_realizer':
        candidate=json.loads(raw.decode('utf-8'))['candidate']
    elif proof['archive_kind']=='writer_attempt':
        rows=[json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
        found=[r['payload']['provider']['provider_result']['realizer_trace']['candidate']
               for r in rows if r.get('stage')=='provider']
        if len(found)!=1:raise ValueError('intention plan lacks unique sealed candidate')
        candidate=found[0]
    else:raise ValueError('unknown intention candidate archive')
    if fp(candidate)!=proof['candidate_fingerprint'] or candidate['candidate_ref']!=proof['candidate_ref']:
        raise ValueError('intention candidate evidence differs')
    payload=candidate['candidate_payload']
    children=payload.get('action_sequence',{}).get('children',{})
    result=[{'子项':c['content_id'],'通道':c['kind'],'计划内容':c['description'],
             '对象':c['recipient_refs'],
             **({'前序子项':list(children[c['content_id']]['after'])}
                if c['content_id'] in children and 'after' in children[c['content_id']] else {})}
            for c in payload['contents']]
    with _PLAN_LOCK:
        _PLAN_CACHE[key]=deepcopy(result)
        while len(_PLAN_CACHE)>64:_PLAN_CACHE.popitem(last=False)
    return result


def annotation(app,memory,*,reader_ref,snapshot=None):
    event=memory.event
    source=app.runtime.session.profile.get('desktop_pet_product_v1',{}).get('sources',{}).get(event.event_id)
    if source is None or source['origin']!=ORIGIN:return None,()
    if reader_ref!=app.owner_ref or event.owner_ref!=reader_ref:
        raise ValueError('private intention belongs to another reader')
    app._verify_source(event)
    chain=app.runtime.session.profile[FACTS][source['evidence']['source_ref']]
    validate(chain)
    if snapshot is not None:
        # Capture only selected intentions. Restore exactly the same prefix,
        # even if the live chain has since advanced at the same wall time.
        head=snapshot.setdefault(event.event_id,chain[-1]['fingerprint'])
        indices=[i for i,row in enumerate(chain) if row['fingerprint']==head]
        if len(indices)!=1:raise ValueError('intention snapshot is not in its source chain')
        chain=chain[:indices[0]+1]
    if chain[0]['fingerprint']!=source['evidence']['formation_ref']:
        raise ValueError('private memory formation differs')
    proofs=app.runtime.session.profile.get(PROOFS,{})
    latest=None;planned=False;plan=None;effects=[];refs=[chain[0]['fingerprint']]
    # Read this intention only; never scan other memory bodies.
    attempt=chain[-1]['attempt_id']
    for row in chain[1:]:
        if row['attempt_id']!=attempt:continue
        proof=proofs[row['evidence_ref']]
        if (fp(proof)!=row['evidence_ref'] or proof['owner']!=reader_ref
                or proof['visibility']!='self' or proof['phase']!=row['phase']
                or proof.get('attempt_ref',proof['source_ref'])!=row['attempt_id']):
            raise ValueError('private intention progress evidence differs')
        refs.append(row['fingerprint']);latest=(row,proof)
        if row['phase']=='planned':
            planned=True;plan=planned_contents(proof)
        if row['phase']=='effect':
            actual_source=app.runtime.session.profile['desktop_pet_product_v1']['sources'][proof['event_ref']]
            actual=PetEvent.from_record(actual_source['event']);app._verify_source(actual)
            if actual_source['origin']!='execution':raise ValueError('intention effect is not execution')
            receipt=actual_source['evidence']['fact']['model_payload']['execution_receipt']
            if (row['actual_effect_ref']!=actual.event_id or actual.source_binding_ref!=proof['source_binding_ref']
                    or receipt['fingerprint']!=proof['execution_receipt_ref']):
                raise ValueError('intention effect receipt differs')
            from .conversation_delivery import execution_progress
            progress=execution_progress(receipt,actual.visible_contents(reader_ref))
            effects.append({'已确认内容':[c.as_text() for c in actual.visible_contents(reader_ref)],
                            '子项结果':{key:value['status'] for key,value in receipt['outcome']['details'].get('child_results',{}).items()},
                            **({'打断进度':progress} if progress is not None else {})})
            refs.append(actual.source_binding_ref)
    value={'范围':'本人私有经历','形成':'已形成回应意图','本次已编排':planned,
           '当前进度':PHASE_LABELS[latest[0]['phase'] if latest else 'formed'],
           '实际效果':effects}
    if plan is not None:value['已编排计划（不代表已执行）']=plan
    if latest and latest[1].get('reason') in REASON_LABELS:value['结束原因']=REASON_LABELS[latest[1]['reason']]
    return {'version':VERSION,**value},tuple(refs)
