"""Tool availability facts, independent of character state and recall mathematics."""
from urllib.parse import urlsplit
from urllib.error import HTTPError

class UnavailableWebPage(ValueError):
 def __init__(self,evidence):
  super().__init__('网页已删除或不可用')
  self.evidence=evidence

def unavailable(page):
 # A verified site response signature, not a topic/keyword classifier.
 host=urlsplit(page.get('url','')).hostname or ''
 if (host=='bilibili.com' or host.endswith('.bilibili.com')) and page.get('title','').strip()=='什么都没找到' and page.get('content','').strip()=='页面不存在或已被删除':
  return {'classification':'bilibili-deleted-page-v1','stage':'content_validation'}
 return None

def failure(error,stage):
 result={'stage':stage,'exception_type':type(error).__name__}
 if isinstance(error,HTTPError):result['http_status']=error.code
 # Never serialize exception messages, credentials, response headers or URLs.
 return result

def paused(state):
 return bool(state and state.get('status')=='failed' and not state.get('reading_resumed',False))
