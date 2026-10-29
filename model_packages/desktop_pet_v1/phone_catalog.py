"""Optional phone binding over existing published tokens, not new state math."""
from copy import deepcopy
from .action_candidates import CATALOG
from .web_activity import content
VERSION='desktop-pet-phone-action-catalog-v2'


def catalog(url=None):
 value=deepcopy(CATALOG)
 if url is None:return value
 content(url,'user:resource-validation')
 phone={'key':'read_web','description':'低头拿起手机，准备阅读网页',
        'tool':{'kind':'read_web','url':url},'fallback':True,
        'all':[{'kind':'change','text':'[惯性延续]'}],
        'any':[{'kind':'self','text':'[行为高度同向]'},{'kind':'self','text':'[行为中等同向]'}],
        'none':[{'kind':'pain'}]}
 value['version']=VERSION;value['candidates'].append(phone)
 return value
