"""Exact UTF-8 bounded retrieval passages; the original event is stored once.

The byte bound is conservative for the deployed byte-BPE model's 2048-token
slot, including reranker framing. It is a resource boundary, not a recall cap.
No passage is called a new event and no unexamined passage is a negative hit.
"""
from framework.model_api import model_data_fingerprint as fp
VERSION='desktop-pet-exact-retrieval-passages-v1'
MAX_BYTES=1200


def project(documents,*,version=VERSION):
    if version is None:return documents
    if version!=VERSION:raise ValueError('unsupported retrieval document version')
    result=[]
    for doc in documents:
        text=doc['text']
        if len(text.encode('utf8'))<=MAX_BYTES:
            result.append(doc);continue
        start=0;size=0
        for end,char in enumerate(text):
            n=len(char.encode('utf8'))
            if size+n>MAX_BYTES:
                result.append(_part(doc,start,end));start=end;size=0
            size+=n
        if start<len(text):result.append(_part(doc,start,len(text)))
    return result


def _part(doc,start,end):
    source={'event_id':doc['event_id'],'content_fingerprint':doc['content_fingerprint'],'span':[start,end]}
    identity=fp({'version':VERSION,'source':source})
    return {**doc,'event_id':'rag-passage:'+identity,'text':doc['text'][start:end],
            'content_fingerprint':identity,'source_slice':source}
