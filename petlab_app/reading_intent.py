"""Read-only dynamic query lineage, anchored in a committed body activity."""
from framework.model_api import thaw_model_data

def from_activity(app,event_id,resource):
 from .activity_sources import bind
 from .web_search import decode
 bind(app,event_id=event_id,url=resource)
 topic=decode(resource)
 return _from_verified_activity(app,event_id,topic)


def _from_verified_activity(app,event_id,topic):
 """Project intent after the caller has validated this same committed activity."""
 from .web_search import resources
 from product_adapters.desktop_pet_v1.reading_delivery import VERSION
 if topic is None or 'query' in topic:raise ValueError('not a dynamic search activity')
 source=app._product(source_id=event_id)['sources'][event_id]
 receipt=source['evidence']['fact']['model_payload']['execution_receipt']
 row=receipt['outcome']['details'].get('reading_intent')
 if row is None:return None
 intent=thaw_model_data(row['intent'])
 if intent.get('version')!=VERSION or intent.get('site')!=topic['site']:raise ValueError('reading intent site differs from selected action')
 if intent.get('query') is None:return None
 resources([{'query':intent['query'],'site':topic['site']}])
 return {**intent,'candidate_fingerprint':row['candidate_fingerprint'],'action_id':event_id}
