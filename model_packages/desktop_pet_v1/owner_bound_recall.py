"""Source-declared scope plus topic, each scored by unchanged v2 recall."""
from dataclasses import replace
from framework.model_api import model_data_fingerprint,thaw_model_data
from . import grounded_recall as base
from .declared_forms import validate_declarations
from . import declared_recall as declared
VERSION='desktop-pet-owner-bound-reference-recall-v6'
from .declaration_sources import for_source
KEY=base.KEY
register_event=base.register_event
view_for=base.view_for
current_projection=base.current_projection

def enabled(context):return context.metadata.get(KEY,{}).get('version')==VERSION

def make_cue(event,parsed):
    if parsed.get('version')!=VERSION:raise ValueError('unknown owner-bound recall version')
    from . import reference_owners,source_bound_recall
    reference_owners.validate(parsed)
    cue=source_bound_recall.make_cue(event,{**parsed,'version':source_bound_recall.VERSION})
    return {**cue,'version':VERSION,'reference_owner_version':reference_owners.VERSION}


def metadata(runtime,event,parsed):
    original=base.metadata(runtime,event,{**parsed,'version':base.VERSION})[KEY]
    return {KEY:{**original,'version':VERSION,'cue':make_cue(event,parsed)}}

def source_for(view,identity):
    source=base.source_for(view,identity);fact=view.index.facts[view.index.by_id[identity]]
    return {**source,'_declaration_key':(fact.session_binding_fingerprint,fact.fingerprint)}

def _queries(cue,source):
    # Literal topics and source-declared conventions retain their original channels.
    yield from declared._queries(cue,source)
    key=source.get('_declaration_key')
    for row in cue['reference_anchors']:
        ref=row['reference_source']
        if (key!=(ref['binding'],ref['fact_fingerprint'])
            or any(source.get(k)!=ref.get(k) for k in ('event_id','event_fingerprint','source_ref'))):continue
        yield {'kind':'source_bound_reference','phrases':[row['text']],'signatures':[],
            'reference_anchor':row}


def association(cue,source):
    values=[(base.association(q,source),q) for q in _queries(cue,source)]
    best=max(values,key=lambda x:x[0][0])
    return best[0][0],{'kind':'declared-source-query-union','channel':best[1]['kind'],'match':best[0][1]}

def _context(context,query):
    prior=context.metadata[KEY]
    return replace(context,metadata={**context.metadata,KEY:{**prior,'version':base.VERSION,
        'cue':{'version':base.VERSION,'phrases':query['phrases'],'signatures':query['signatures']}}})

def recall(package,*,payload,context,rows=None,current=None):
    if not enabled(context):raise ValueError('declared recall needs explicit version')
    current=current or current_projection(package,payload,context)
    cue=context.metadata[KEY]['cue'];view=view_for(context);ids=list(payload['candidate_event_ids']);evidence={}
    for identity in ids:
        source=source_for(view,identity);choices=[]
        request={**payload,'candidate_event_ids':[identity],
            'historical_live':[x for x in payload['historical_live'] if x['event_id']==identity]}
        for query in _queries(cue,source):
            result=base.recall(package,payload=request,context=_context(context,query),rows=rows,current=current)
            choices.append((query,result['recall_decision']['candidate_evidence'][identity]))
        query,best=max(choices,key=lambda pair:pair[1]['margin'])
        evidence[identity]={**best,'receipt':{**best['receipt'],'query_version':VERSION,
            'query_channels':[{'kind':q['kind'],**({'declaration_source_fingerprint':q['declaration_source_fingerprint']} if 'declaration_source_fingerprint' in q else {}),
                **({'reference_anchor':q['reference_anchor']} if 'reference_anchor' in q else {}),'impulse':e['impulse'],'threshold':e['threshold'],'margin':e['margin'],'match':e['receipt']['match']} for q,e in choices],
            'selected_query_kind':query['kind']}}
    recalled=[i for i in ids if evidence[i]['recalled']]
    return {'recall_decision':{'version':VERSION,'candidate_event_ids':ids,'recalled_event_ids':recalled,'candidate_evidence':evidence},
        'memory_content_claims':{'version':'desktop-pet-grounded-content-claim-v2','claims':[
            {'event_id':i,'strength':evidence[i]['impulse'],'claim_version':'desktop-pet-grounded-content-claim-v2',
             'source_evidence_fingerprint':model_data_fingerprint(evidence[i])} for i in recalled]}}
