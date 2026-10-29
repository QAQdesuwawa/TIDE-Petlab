"""One source-verified ingress envelope; no state, math or tool dispatch."""
from pathlib import Path
import hashlib,json
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
SOURCE_VERSION='pet-external-source-receipt-v1'
ACTIVITY_SOURCE_VERSION='pet-external-source-receipt-v2'
READING_SOURCE_VERSION='pet-external-source-receipt-v3'
INPUT_VERSION='desktop-pet-received-external-input-v1'


def archive_root(app):return app.store.path.with_suffix('.resources')


def validate(app,receipt,contents,*,event_id=None):
 from .participant_input import VERSIONS as PARTICIPANT_VERSIONS,validate as validate_participant
 if receipt.get('version') in PARTICIPANT_VERSIONS:return validate_participant(app,receipt,contents,event_id=event_id)
 from model_packages.desktop_pet_v1.web_observation import content
 row=thaw_model_data(receipt)
 extras=set()
 if row.get('version')==ACTIVITY_SOURCE_VERSION:extras.add('activity_source')
 if row.get('version')==READING_SOURCE_VERSION:
  extras.add('reading_checkpoint')
  if 'activity_source' in row:extras.add('activity_source')
 if set(row)!={'version','kind','actor_ref','reader_ref','requested_by','session_binding_fingerprint','event_id','origin','provider','artifact','artifact_sha256','source_chars','read_span','coverage','fetched_at_ms','fingerprint'}|extras:raise ValueError('external source fields differ')
 if set(row['origin'])!={'url','title','author'}:raise ValueError('external origin fields differ')
 if (not isinstance(row['origin']['title'],str) or len(row['origin']['title'])>500
  or row['origin']['author'] is not None and (not isinstance(row['origin']['author'],str) or len(row['origin']['author'])>500)
  or not isinstance(row['provider'],str) or not 0<len(row['provider'])<=128):raise ValueError('external metadata exceeds boundary')
 if event_id is not None and row.get('event_id')!=event_id:raise ValueError('external source event identity differs')
 if (row.get('version') not in (SOURCE_VERSION,ACTIVITY_SOURCE_VERSION,READING_SOURCE_VERSION) or row.get('kind')!='web_page'
  or row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'})
  or row.get('session_binding_fingerprint')!=app.runtime.session.session_binding.fingerprint
  or row.get('reader_ref')!=app.owner_ref or row.get('requested_by')!=(app.owner_ref if 'activity_source' in row else app.user_ref)):raise ValueError('external source binding differs')
 from .web_reading import validate_url
 url=validate_url(row['origin']['url'])
 if 'activity_source' in row:

  from .activity_sources import validate as validate_activity
  validate_activity(app,row['activity_source'],url=url)
 from urllib.parse import urlsplit
 if row['actor_ref']!='web:'+urlsplit(url).hostname:raise ValueError('external source actor differs')
 root=archive_root(app).resolve();artifact=(root/row['artifact']).resolve()
 if not artifact.is_relative_to(root) or artifact.suffix!='.json':raise ValueError('external artifact escapes source archive')
 raw=artifact.read_bytes()
 if len(raw)>1100000 or hashlib.sha256(raw).hexdigest()!=row['artifact_sha256']:raise ValueError('external artifact content differs')
 page=json.loads(raw)
 if page['url']!=url or page['title']!=row['origin']['title'] or page.get('author')!=row['origin'].get('author'):raise ValueError('external origin differs')
 text=page['content'];span=row['read_span']
 if (not isinstance(span,(list,tuple)) or len(span)!=2 or type(span[0])is not int or span[0]<0 or (row['version']!=READING_SOURCE_VERSION and span[0]!=0) or type(span[1])is not int
  or not span[0]<span[1]<=min(4096,len(text)) or row['source_chars']!=len(text)
  or row['coverage']!=('complete' if list(span)==[0,len(text)] else 'interval' if row['version']==READING_SOURCE_VERSION else 'prefix')):raise ValueError('external reading range differs')
 if row['version']==READING_SOURCE_VERSION:
  from .reading_progress import validate_checkpoint
  cp=validate_checkpoint(app,row['reading_checkpoint'],event_id=row['event_id'],url=url)
  if cp['artifact_sha256']!=row['artifact_sha256'] or list(cp['read_span'])!=list(span) or cp.get('activity_source')!=row.get('activity_source'):raise ValueError('reading checkpoint differs from source')
 expected=content(row,text[span[0]:span[1]],app.owner_ref)
 if tuple(contents)!=(expected,):raise ValueError('external observation differs from actual source range')
 return row


def allowed_policies(receipt):
 from .participant_input import NPC_VERSIONS
 return {'eligible'} if receipt.get('version') in {READING_SOURCE_VERSION,*NPC_VERSIONS} else {'required'}
