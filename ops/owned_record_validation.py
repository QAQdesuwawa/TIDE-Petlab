"""Operational validation reuse for exact factory-owned journal rows.

No digest supplied by a caller grants reuse. Mutable rows are revalidated;
replacement, deletion and reordering are observed on every read. A failed
refresh does not publish a partially validated journal. Each retained scope
holds only its current rows; dropping a scope changes cost, never validity.
"""
from collections import OrderedDict
from threading import RLock
from framework.model_api.immutable_record import ImmutableModelRecord


class OwnedJournalValidation:
    def __init__(self, *, scope_limit=8):
        self._scopes=OrderedDict()
        self._lock=RLock()
        self._scope_limit=scope_limit

    def restore(self, scope, rows, validate):
        with self._lock:
            previous=self._scopes.get(scope,{})
            current={};results=[]
            for row in rows:
                owned=type(row) is ImmutableModelRecord
                cached=current.get(id(row),previous.get(id(row))) if owned else None
                if cached is not None and cached[0] is row:
                    result=cached[1]
                else:
                    result=validate(row)
                if owned:current[id(row)]=(row,result)
                results.append(result)
            self._scopes[scope]=current
            self._scopes.move_to_end(scope)
            while len(self._scopes)>self._scope_limit:
                self._scopes.popitem(last=False)
            return tuple(results)
