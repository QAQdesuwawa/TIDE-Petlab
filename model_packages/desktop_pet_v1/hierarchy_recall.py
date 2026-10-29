"""Hierarchical source properties mapped to the unchanged v2 recall operator."""
from dataclasses import replace
from framework.model_api import model_data_fingerprint,thaw_model_data
from . import grounded_recall as base
VERSION='desktop-pet-hierarchical-property-recall-v7'
KEY=base.KEY
register_event=base.register_event
view_for=base.view_for
current_projection=base.current_projection

def enabled(context):return context.metadata.get(KEY,{}).get('version')==VERSION

def make_cue(event,parsed):
    from .hierarchy_semantics import validate
    properties=validate(event,parsed)
    return {'version':VERSION,'event_id':event.event_id,'event_fingerprint':event.fingerprint,
        'source_ref':event.source_binding_ref,'properties':properties,'phrases':[],
        'signatures':base.signatures(event),'response_scope':{'owner_ref':event.owner_ref,'scope_ref':event.scope_ref}}


def metadata(runtime,event,parsed):
    original=base.metadata(runtime,event,{**parsed,'version':base.VERSION})[KEY]
    return {KEY:{**original,'version':VERSION,'cue':make_cue(event,parsed)}}

def source_for(view,identity):
    source=base.source_for(view,identity);fact=view.index.facts[view.index.by_id[identity]]
    return {**source,'_declaration_key':(fact.session_binding_fingerprint,fact.fingerprint)}

def match_properties(queries,sources):
    matches=[]
    for qi,q in enumerate(queries):
        for si,s in enumerate(sources):
            if s['kind']!='assertion':continue
            qo,so=q['owner'],s['owner']
            unknown=qo=='unresolved' or so=='unresolved' or qo=='third_party' or so=='third_party'
            if not unknown and qo!=so:continue
            # Explicitly different resolved objects never merge through a parent theme.
            if qo!='unresolved' and so!='unresolved' and qo!=so:continue
            qp=list(q['path']);sp=list(s['path']);n=min(len(qp),len(sp))
            if qp[:n]!=sp[:n]:continue
            if q['value'] is not None and s['value'] is not None and q['value']!=s['value']:continue
            level='unresolved_parent' if unknown else 'exact' if qp==sp else 'parent'
            matches.append({'query_index':qi,'source_index':si,'level':level,'source':s})
    return matches


def _queries(cue,source):
    from .hierarchy_sources import for_source
    annotation=for_source(source);matches=[]
    if annotation is not None and all(cue['response_scope'][k]==annotation[k] for k in ('owner_ref','scope_ref')):
        matches=match_properties(cue['properties'],annotation['properties'])
    phrases=[];proof=[]
    for item in matches:
        a,b=item['source']['span'];quote=source['text'][a:b]
        if quote not in phrases:phrases.append(quote)
        proof.append({k:item[k] for k in ('query_index','source_index','level')})
    yield {'kind':'hierarchical_property','phrases':phrases,'signatures':cue['signatures'],
        'property_matches':proof,'source_property_fingerprint':annotation['fingerprint'] if annotation else None}


def association(cue,source):
    values=[(base.association(q,source),q) for q in _queries(cue,source)]
    best=max(values,key=lambda x:x[0][0])
    return best[0][0],{'kind':'hierarchical-source-query','channel':best[1]['kind'],'match':best[0][1]}

def _context(context,query):
    prior=context.metadata[KEY]
    return replace(context,metadata={**context.metadata,KEY:{**prior,'version':base.VERSION,
        'cue':{'version':base.VERSION,'phrases':query['phrases'],'signatures':query['signatures']}}})

def recall(package,*,payload,context,rows=None,current=None):
    if not enabled(context):raise ValueError('hierarchical recall needs explicit version')
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
                **({'property_matches':q['property_matches'],'source_property_fingerprint':q['source_property_fingerprint']} if 'property_matches' in q else {}),'impulse':e['impulse'],'threshold':e['threshold'],'margin':e['margin'],'match':e['receipt']['match']} for q,e in choices],
            'selected_query_kind':query['kind']}}
    recalled=[i for i in ids if evidence[i]['recalled']]
    return {'recall_decision':{'version':VERSION,'candidate_event_ids':ids,'recalled_event_ids':recalled,'candidate_evidence':evidence},
        'memory_content_claims':{'version':'desktop-pet-grounded-content-claim-v2','claims':[
            {'event_id':i,'strength':evidence[i]['impulse'],'claim_version':'desktop-pet-grounded-content-claim-v2',
             'source_evidence_fingerprint':model_data_fingerprint(evidence[i])} for i in recalled]}}
