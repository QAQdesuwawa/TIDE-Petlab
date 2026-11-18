"""Actual partial-playback decisions; one input never rewrites another's proof."""
from collections import deque
from threading import Lock
from framework.model_api import thaw_model_data


def records(details):
    single=details.get('qualification_takeover')
    multiple=details.get('qualification_takeovers')
    if multiple is None:return () if single is None else (thaw_model_data(single),)
    rows=thaw_model_data(multiple)
    if not isinstance(rows,list) or len(rows)<2 or single is None or rows[-1]!=thaw_model_data(single):
        raise ValueError('takeover history differs from its latest receipt')
    events=set();children=set()
    for row in rows:
        if row['event_id'] in events or children.intersection(row['child_ids']):
            raise ValueError('takeover history repeats an input or affected child')
        events.add(row['event_id']);children.update(row['child_ids'])
    return tuple(rows)


def fields(task):
    latest=task.semantic_takeover
    if latest is None:return {}
    rows=getattr(task,'semantic_takeovers',())
    return {'qualification_takeover':latest,
            **({'qualification_takeovers':list(rows)} if len(rows)>1 else {})}


def verify_acknowledgements(details,sent):
    order={identity:index for index,identity in enumerate(sent)}
    previous=-1
    for row in records(details):
        proof=sent.get(row['event_id'])
        ids=row.get('child_ids',())
        actual={key for key,value in details.get('child_results',{}).items()
                if value['status'] in {'cancelled','interrupted'} and value.get('cause_ref')==row['event_id']}
        if (proof is None or {k:v for k,v in row.items() if k!='child_ids'}!=thaw_model_data(proof)
                or not actual or set(ids)!=actual or len(ids)!=len(actual)):
            raise ValueError('playback takeover acknowledgement differs from its request')
        if order[row['event_id']]<=previous:raise ValueError('playback takeover acknowledgements are out of order')
        previous=order[row['event_id']]


class TakeoverQueue:
    """Ordered bridge mailbox. Unlike a Future, consumes each completed proof."""
    def __init__(self):
        self._rows=deque();self._lock=Lock()
    def set_result(self,value):
        with self._lock:self._rows.append(value)
    def done(self):
        with self._lock:return bool(self._rows)
    def cancelled(self):return False
    def result(self):
        with self._lock:return self._rows.popleft()
