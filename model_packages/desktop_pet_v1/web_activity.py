"""Body activity declaration; preparation is distinct from observing a webpage."""
from urllib.parse import urlsplit
from .events import Action
from .behavior import VERSION as BEHAVIOR_VERSION
VERSION='desktop-pet-web-activity-v1'
KEY='read_web'


def content(url,user_ref):
 if not isinstance(url,str) or not url or len(url)>8192 or any(c.isspace() for c in url):raise ValueError('invalid activity reading URL')
 parsed=urlsplit(url)
 if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('invalid activity reading URL')
 return Action(content_id='activity',semantic_key=KEY,description='低头拿起手机，准备阅读网页',recipient_refs=(user_ref,),
  parameters={'behavior_version':BEHAVIOR_VERSION,'web_activity_version':VERSION,'reading_url':url})
