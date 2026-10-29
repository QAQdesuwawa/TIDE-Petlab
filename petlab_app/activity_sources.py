"""Read-only proof that a committed body activity initiated a tool observation."""
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.web_activity import VERSION as ACTIVITY_VERSION,KEY
VERSION='pet-activity-observation-source-v1'


def bind(app,*,event_id,url,content_id='activity'):
 try:memory=app.runtime.memory_instance(event_id)
 except (KeyError,ValueError):raise ValueError('activity is not a committed fact') from None
 event=memory.event;app._verify_source(event)
 if event.event_kind!='npc_output' or event.actor_ref!=app.owner_ref or event.owner_ref!=app.owner_ref:raise ValueError('observation requires this character actual output')
 source=app._product(source_id=event_id)['sources'][event_id]
 if source['origin']!='execution':raise ValueError('activity lacks actual execution')
 items=[c for c in event.contents if c.content_id==content_id]
 if len(items)!=1:raise ValueError('activity content identity differs')
 c=items[0]
 if (c.kind!='action' or c.semantic_key!=KEY or c.parameters.get('web_activity_version')!=ACTIVITY_VERSION
  or c.parameters.get('reading_url')!=url or c.parameters.get('execution_status')!='completed'):raise ValueError('activity reading binding differs')
 value={'version':VERSION,'event_id':event_id,'fact_fingerprint':memory.fact.fingerprint,
  'content_id':content_id,'content_fingerprint':fp(c.to_record())}
 return {**value,'fingerprint':fp(value)}


def validate(app,value,*,url):
 value=thaw_model_data(value)
 if value.get('version')=='pet-activity-observation-source-v2':
  from .web_search import validate_selection
  return validate_selection(app,value,url=url)
 if set(value)!={'version','event_id','fact_fingerprint','content_id','content_fingerprint','fingerprint'}:raise ValueError('activity source fields differ')
 if value!=bind(app,event_id=value['event_id'],content_id=value['content_id'],url=url):raise ValueError('activity source differs from actual committed content')
 return value
