"""Source-declared addresses select association; original v2 numeric gates remain."""
from urllib.parse import quote
from framework.model_api import model_data_fingerprint,thaw_model_data
from model_packages.semantic_addresses import levels,address_indexes,match_addresses
from . import grounded_recall as base,hierarchy_recall as old
from .address_semantics import VERSION
from .hierarchy_semantics import validate
STRENGTH_VERSION="desktop-pet-property-shared-depth-v1"
KEY=base.KEY
register_event=base.register_event
view_for=base.view_for
source_for=old.source_for
current_projection=base.current_projection


def enabled(context):return context.metadata.get(KEY,{}).get('version')==VERSION


def addresses(properties):
 return [{'slot_id':'pet.source-property','entity_id':p['owner'] if p['owner'] in ('user','current_npc') else None,
  'attribute':'/'.join(quote(part,safe='') for part in p['path'])} for p in properties]


def match_properties(queries,candidates):
 rows=[{'event_id':identity,'indexes':address_indexes(addresses([p for p in props if p['kind']=='assertion']))}
       for identity,props in candidates.items()]
 return match_addresses(addresses(queries),rows)


def _graded_properties(queries,candidates):
 _,decisions=match_properties(queries,candidates)
 strengths={}
 for query,decision in zip(queries,decisions):
  depth=len(query['path'])-(decision['level'] if decision['level'] is not None else len(query['path']))
  if not depth:
   decision.update(level=None,fingerprint=None,matches=[])
   continue
  for identity in decision['matches']:
   for prop in candidates[identity]:
    if prop['kind']!='assertion' or decision['fingerprint'] not in levels(addresses([prop])[0]):continue
    strength=depth/max(len(query['path']),len(prop['path']))
    strengths[identity]=max(strength,strengths.get(identity,0.))
 return strengths,decisions


def property_strengths(queries,candidates):
 return _graded_properties(queries,candidates)[0]


def make_cue(event,parsed):
 cue=old.make_cue(event,parsed)
 if parsed.get('address_strength_version')==STRENGTH_VERSION:
  cue['address_strength_version']=STRENGTH_VERSION
 references=thaw_model_data(parsed.get('predecessor_refs',()))
 if references:
  from .semantic import evidence_source
  current=evidence_source(event)
  if (event.event_kind!='peer_input' or len(references)>16
      or any(current.count(row['current_quote'])!=1 for row in references)):
   raise ValueError('predecessor cue differs from current source')
  cue['predecessor_refs']=references
 return {**cue,'version':VERSION}


def metadata(runtime,event,parsed):
 result=old.metadata(runtime,event,parsed)[KEY]
 return {KEY:{**result,'version':VERSION,'cue':make_cue(event,parsed)}}


def _properties(source):
 from .hierarchy_sources import for_source
 annotation=for_source(source)
 if annotation is None:return []
 if any(annotation[k]!=source[k] for k in ('event_id','event_fingerprint','source_ref','owner_ref','scope_ref')):
  raise ValueError('property address source differs')
 return annotation['properties']


def _predecessor_phrases(cue,source):
 references=cue.get('predecessor_refs',())
 if not references:return
 binding,fact=source['_declaration_key']
 for row in references:
  ref=row['source']
  if (ref['binding']==binding and ref['fact_fingerprint']==fact
      and all(ref[k]==source[k] for k in ('event_id','event_fingerprint','source_ref'))
      and source['text'].count(row['prior_quote'])==1):
   yield row['prior_quote']


def association(cue,source):
 # Conservative per-source preparation. The final batch selects its first hit
 # level over the actual closed candidate set, using the same shared matcher.
 if any(cue['response_scope'][k]!=source[k] for k in ('owner_ref','scope_ref')):return 0.,{'kind':'property-address','matches':[]}
 candidates={'source':_properties(source)}
 graded=cue.get('address_strength_version')==STRENGTH_VERSION
 if graded:
  strengths,proof=_graded_properties(cue['properties'],candidates)
  hits=bool(strengths)
 else:
  hits,proof=match_properties(cue['properties'],candidates)
 references=list(_predecessor_phrases(cue,source))
 sem=base.association({'phrases':[source['text']] if hits else references,'signatures':cue['signatures']},source)[0]
 if graded and not cue['signatures']:
  sem*=1. if references else strengths.get('source',0.)
 return sem,{'kind':'property-address','decisions':proof,**({'predecessor_quotes':references} if references else {})}


def recall(package,*,payload,context,rows=None,current=None):
 if not enabled(context):raise ValueError('address recall needs explicit version')
 cue=context.metadata[KEY]['cue'];view=view_for(context);ids=list(payload['candidate_event_ids'])
 sources={i:source_for(view,i) for i in ids}
 properties={i:(_properties(s) if all(cue['response_scope'][k]==s[k] for k in ('owner_ref','scope_ref')) else []) for i,s in sources.items()}
 graded=cue.get('address_strength_version')==STRENGTH_VERSION
 if graded:
  strengths,decisions=_graded_properties(cue['properties'],properties)
 else:
  _,decisions=match_properties(cue['properties'],properties)
 current=current or current_projection(package,payload,context);evidence={}
 for identity in ids:
  source=sources[identity]
  references=list(_predecessor_phrases(cue,source)) if all(cue['response_scope'][k]==source[k] for k in ('owner_ref','scope_ref')) else []
  phrases=list(dict.fromkeys(references))
  for prop in properties[identity]:
   if prop['kind']!='assertion':continue
   keys=levels(addresses([prop])[0])
   if any(identity in d['matches'] and d['fingerprint'] in keys for d in decisions):
    a,b=prop['span'];text=source['text'][a:b]
    if text not in phrases:phrases.append(text)
  query={'phrases':phrases,'signatures':cue['signatures']}
  if graded and not cue['signatures']:
   query['semantic_strength']=1. if references else strengths.get(identity,0.)
  request={**payload,'candidate_event_ids':[identity],'historical_live':[x for x in payload['historical_live'] if x['event_id']==identity]}
  result=base.recall(package,payload=request,context=old._context(context,query),rows=rows,current=current)
  value=result['recall_decision']['candidate_evidence'][identity]
  evidence[identity]={**value,'receipt':{**value['receipt'],'query_version':VERSION,**({'predecessor_quotes':references} if references else {}),'address_decisions':[{'query_index':n,'level':d['level'],'fingerprint':d['fingerprint'],'matched':identity in d['matches']} for n,d in enumerate(decisions)]}}
 recalled=[i for i in ids if evidence[i]['recalled']]
 return {'recall_decision':{'version':VERSION,'candidate_event_ids':ids,'recalled_event_ids':recalled,'candidate_evidence':evidence},
  'memory_content_claims':{'version':'desktop-pet-grounded-content-claim-v2','claims':[
   {'event_id':i,'strength':evidence[i]['impulse'],'claim_version':'desktop-pet-grounded-content-claim-v2','source_evidence_fingerprint':model_data_fingerprint(evidence[i])} for i in recalled]}}
