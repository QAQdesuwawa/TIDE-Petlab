"""Recipient projections of actual outward children; never a second event store.

The canonical source carries a manifest, not inaccessible content bodies. Each
recipient archive gets only its permitted bodies, bound to the sender's commit.
"""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp
from model_packages.desktop_pet_v1.events import Content

VERSION='petlab-visible-children-v1'
FRAGMENT_VERSION='petlab-visible-children-v2-fragments'
KINDS={'speech','action','expression'}
ENVELOPE_TEXT='[已发生的可见内容，按接收者投递]'


def outward(event,audience):
 return tuple(c for c in event.contents if c.kind in KINDS and not c.private and set(c.recipient_refs)&set(audience))


PRESENTATION_PROJECTION='petlab-visible-attached-expression-v1'


def expand_presentations(contents):
 """Expose a played speech face as a sibling; keep the original source event."""
 from model_packages.desktop_pet_v1.events import Expression
 from model_packages.desktop_pet_v1.face_expression import validate,LABELS
 values=tuple(contents);ids={c.content_id for c in values};result=[]
 for c in values:
  result.append(c)
  if c.kind!='speech' or c.private or 'presentation' not in c.parameters:continue
  identity=c.content_id+'::visible-expression'
  if identity in ids:raise ValueError('attached face projection identity collision')
  ids.add(identity);code=validate(c.parameters['presentation'])
  text='说话时呈现'+LABELS[code]+'的表情。'
  gaze=c.parameters['presentation'].get('gaze')
  if gaze is not None:
   if gaze['status']=='planned':raise ValueError('unexecuted gaze cannot be projected as visible')
   if gaze['status']=='shown':text+=('视线朝向屏幕前方。' if gaze['target_ref']=='front' else '视线看向'+gaze['target_ref']+'。')
  result.append(Expression(content_id=identity,description=text,
   recipient_refs=c.recipient_refs,parameters=c.parameters))
 return tuple(result)


def create(event,audience):
 rows=[];bodies={}
 for c in expand_presentations(outward(event,audience)):
  # Executed descriptions carry actual completion/interruption facts. Numeric
  # state, internal selection and renderer frames stay with the source owner.
  params=dict(c.parameters) if c.kind in {'speech','expression'} else {}
  item=replace(c,recipient_refs=tuple(x for x in c.recipient_refs if x in audience),parameters=params).to_record()
  digest=fp(item);bodies[digest]=item
  rows.append({'content_id':c.content_id,'kind':c.kind,'recipient_refs':item['recipient_refs'],'body_fingerprint':digest})
 if not rows:raise ValueError('execution has no outward visible contents')
 manifest={'version':VERSION,'parent_event_id':event.event_id,'actor_ref':event.actor_ref,
  'target_refs':list(event.target_refs),'items':rows}
 return manifest,bodies


def validate_manifest(source):
 m=source['visible_manifest'];e=source['event'];fragment=m.get('version')==FRAGMENT_VERSION
 fields={'version','parent_event_id','actor_ref','target_refs','items'}|({'segment_id','sequence','previous_segment_id'} if fragment else set())
 if (set(m)!=fields or m['version'] not in {VERSION,FRAGMENT_VERSION}
  or (m.get('segment_id') if fragment else m['parent_event_id'])!=e['sender_action_id'] or m['actor_ref']!=e['sender_participant_id']
  or e['text']!=ENVELOPE_TEXT or not m['items'] or len({x['content_id'] for x in m['items']})!=len(m['items'])
  or not isinstance(m['target_refs'],list) or not set(m['target_refs']).issubset(e['member_snapshot_ids'])):
  raise ValueError('visible source manifest differs')
 if fragment:
  if type(m['sequence']) is not int or m['sequence']<0 or (m['sequence']==0)!=(m['previous_segment_id'] is None):raise ValueError('visible fragment sequence differs')
  origin=source['origin']
  if (origin.get('parent_action_id')!=m['parent_event_id'] or origin.get('event_fingerprint')!=fp(m)
      or origin.get('evidence_kind') not in {'physical_execution_checkpoint','committed_execution'}
      or not m['segment_id'].startswith(m['parent_event_id']+'#visible:')
      or m['segment_id'].split('#visible:')[-1]!=origin.get('commit_fingerprint')):raise ValueError('visible fragment provenance differs')
 for item in m['items']:
  fields={'content_id','kind','recipient_refs','body_fingerprint'}|({'phase','started_at_ms','at_ms','reason','cause_ref'} if fragment else set())
  if (set(item)!=fields or item['kind'] not in KINDS
   or not item['recipient_refs'] or len(set(item['recipient_refs']))!=len(item['recipient_refs'])
   or not set(item['recipient_refs']).issubset(set(e['member_snapshot_ids'])-{e['sender_participant_id']})
   or not isinstance(item['body_fingerprint'],str) or len(item['body_fingerprint'])!=64
   or any(c not in '0123456789abcdef' for c in item['body_fingerprint'])):
   raise ValueError('visible source item invalid')
  if fragment and (item['phase'] not in ({'started','completed','interrupted'} if item['kind']=='action' else {'completed'})
      or type(item['at_ms']) is not int or type(item['started_at_ms']) is not int
      or not item['started_at_ms']<=item['at_ms']<=e['committed_at_ms']):raise ValueError('visible fragment time/phase differs')
 return m


def expected_items(source,reader):
 return [x for x in validate_manifest(source)['items'] if reader in x['recipient_refs']]


def validate_projection(source,reader,value):
 m=validate_manifest(source);items=expected_items(source,reader)
 fragment=m['version']==FRAGMENT_VERSION
 if (set(value)!=({'version','parent_event_id','target_refs','contents'}|({'segment_id','sequence'} if fragment else set())) or value['version']!=m['version']
  or fragment and (value.get('segment_id')!=m['segment_id'] or value.get('sequence')!=m['sequence'])
  or value['parent_event_id']!=m['parent_event_id'] or value['target_refs']!=m['target_refs']
  or len(value['contents'])!=len(items) or not items):raise ValueError('visible recipient projection differs')
 result=[]
 for expected,body in zip(items,value['contents']):
  c=Content.from_record(body)
  if (fp(body)!=expected['body_fingerprint'] or c.content_id!=expected['content_id']
   or c.kind!=expected['kind'] or list(c.recipient_refs)!=expected['recipient_refs']
   or c.private or c.kind not in KINDS or c.kind=='action' and c.parameters):
   raise ValueError('visible child differs from committed manifest')
  result.append(c)
 return tuple(result)


def project(root,source,reader):
 from .multi_pet import _read
 if 'visible_manifest' not in source:return None
 m=validate_manifest(source);items=expected_items(source,reader)
 if not items:return None
 value={'version':m['version'],'parent_event_id':m['parent_event_id'],'target_refs':m['target_refs'],
  **({'segment_id':m['segment_id'],'sequence':m['sequence']} if m['version']==FRAGMENT_VERSION else {}),
  'contents':[_read(root/'visible-contents'/(x['body_fingerprint']+'.json')) for x in items]}
 validate_projection(source,reader,value)
 return value



def source_context(app,event):
 """Read only this retained input's recipient artifact, never the full history."""
 import json
 from .participant_input import VISIBLE_VERSION,validate
 source=app._product(source_id=event.event_id)['sources'].get(event.event_id,{})
 receipt=source.get('evidence',{}).get('input_source',{})
 if receipt.get('version')!=VISIBLE_VERSION:return None
 validate(app,receipt,event.contents,event_id=event.event_id)
 artifact=json.loads((app.store.path.with_suffix('.resources')/receipt['artifact']).read_text(encoding='utf8'))
 return artifact['visible']


def presentation_fields(app,event,fields):
 context=source_context(app,event)
 if context is None:return fields
 attrs=[{('可见受众' if k=='对象' else k):v for k,v in row.items()} for row in fields.get('内容属性',())]
 return {**fields,'对象':context['target_refs'],'接收者':app.owner_ref,'内容属性':attrs}


def delivery_order(row):
    """Actual timestamps first; same-millisecond fragments keep their lineage."""
    p=row['projection'];v=row.get('visible',{})
    return (p['committed_at_ms'],v.get('parent_event_id',p['source_event_id']),v.get('sequence',0),row['delivery']['delivery_id'])
