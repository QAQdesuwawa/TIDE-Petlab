"""Bounded witnessed conversation facts for interpreting a new visible input.

This is semantic source context, not formal recall, an inferred parent, or a
cross-participant memory reader. It never reads another Actor's private store.
"""
import hashlib
import json
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.events import PetEvent
from model_packages.desktop_pet_v1.semantic import evidence_source

VERSION='desktop-pet-visible-conversation-context-v1'


def _artifact(app,source):
 from petlab_app.participant_input import VERSIONS
 receipt=source.get('evidence',{}).get('input_source',{})
 if receipt.get('version') not in VERSIONS:return None
 root=app.store.path.with_suffix('.resources').resolve();path=(root/receipt['artifact']).resolve()
 if not path.is_relative_to(root/'participants') or path.suffix!='.json':raise ValueError('conversation context artifact escapes owner store')
 raw=path.read_bytes()
 if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=receipt['artifact_sha256']:raise ValueError('conversation context artifact differs from accepted source')
 return json.loads(raw)


def build(app,event,*,limit_chars=2048,max_events=16):
 if type(limit_chars)is not int or limit_chars<=0 or type(max_events)is not int or not 1<=max_events<=16:raise ValueError('conversation context budget invalid')
 if event.event_kind!='peer_input':return None
 sources=app.runtime.session.profile['desktop_pet_product_v1']['sources']
 current=sources.get(event.event_id,{})
 from petlab_app.participant_input import VISIBLE_VERSION
 if current.get('evidence',{}).get('input_source',{}).get('version')!=VISIBLE_VERSION:return None
 app._verify_source(event)
 artifact=_artifact(app,current);conversation=artifact['conversation']
 if conversation['kind']!='group':return None
 from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
 journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
 before=event.event_id not in journal;rows=[];refs=[];visited=0;boundary=None
 for identity in reversed(journal):
  if not before:
   if identity==event.event_id:before=True
   continue
  if visited>=max_events:break
  visited+=1
  prior=PetEvent.from_record(journal[identity]['event'])
  if prior.owner_ref!=event.owner_ref or prior.position.coordinate>event.position.coordinate:continue
  source=sources[identity]
  if source['origin']=='input':
   prior_artifact=_artifact(app,source)
   if prior_artifact is None or prior_artifact['conversation']!=conversation:continue
  elif source['origin']=='execution':
   dest=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')
   if dest is None or thaw_model_data(dest['conversation'])!=conversation or event.actor_ref not in dest['audience']:continue
  else:continue
  contents=tuple(c for c in prior.visible_contents(event.owner_ref) if not c.private and c.kind in ('speech','expression','action')
    and (source['origin']!='execution' or event.actor_ref in c.recipient_refs))
  if not contents:continue
  app._verify_source(prior);memory=app.runtime.memory_instance(identity)
  body={'实际说话或行动者':prior.actor_ref,'事件方向':prior.event_kind,'原文':evidence_source(replace(prior,contents=contents))}
  candidate=json.dumps([body,*rows],ensure_ascii=False,separators=(',',':'))
  if len(candidate)>limit_chars:boundary=identity;break
  rows.insert(0,body)
  refs.insert(0,{'event_id':identity,'event_fingerprint':prior.fingerprint,'fact_fingerprint':memory.fact.fingerprint,
   'source_binding_ref':prior.source_binding_ref,'conversation_id':conversation['conversation_id']})
 if not rows and boundary is None:return None
 text=json.dumps(rows,ensure_ascii=False,separators=(',',':')) if rows else ''
 value={'version':VERSION,'event_fingerprint':event.fingerprint,'session_binding_fingerprint':app.runtime.session.session_binding.fingerprint,
  'conversation':conversation,'limit_chars':limit_chars,'max_events':max_events,'sources':refs,
  'budget_boundary_event_id':boundary,'status':'delivered' if rows else 'deferred_budget','text':text}
 return {**value,'fingerprint':fp(value)}
