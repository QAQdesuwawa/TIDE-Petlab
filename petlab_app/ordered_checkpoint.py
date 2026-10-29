"""Ordered background checkpoint publication; no model or execution authority.

The current whole profile contains every prior fact/receipt. Pending snapshots
may coalesce, but a paid call/effect barrier flushes the preceding logical state.
"""
from collections.abc import Mapping
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import wraps
from types import MappingProxyType
import threading
from framework.model_api.immutable_record import ImmutableModelRecord


_ACTIVE_CHECKPOINT=ContextVar('petlab_checkpoint_publication',default=None)


def flush_active_checkpoint():
    publisher=_ACTIVE_CHECKPOINT.get()
    if publisher is not None:publisher.flush()


def _snapshot(value,memo):
    if type(value) is ImmutableModelRecord:return value
    if value is None or type(value) in (str,bool,int,float):return value
    identity=id(value)
    if identity in memo:return memo[identity]
    if isinstance(value,Mapping):
        result=MappingProxyType({key:_snapshot(item,memo) for key,item in value.items()})
    elif isinstance(value,(tuple,list)):
        result=tuple(_snapshot(item,memo) for item in value)
    else:raise TypeError('checkpoint snapshot must contain model data')
    memo[identity]=result
    return result


class OrderedCheckpoint:
    def __init__(self,write):
        self._write=write;self._condition=threading.Condition()
        self._executor=None;self._pending=None;self._running=False;self._error=None
        self.latest_profile=None;self.closed=False;self.accepted=0;self.written=0;self.coalesced=0

    @property
    def pending(self):
        with self._condition:return self._running or self._pending is not None

    def submit(self,session):
        snapshot=replace(session,profile=_snapshot(session.profile,{}))
        with self._condition:
            if self.closed:raise RuntimeError('checkpoint publisher is closed')
            if self._error is not None:raise self._error
            self.accepted+=1
            if self._pending is not None:self.coalesced+=1
            self._pending=snapshot;self.latest_profile=snapshot.profile
            if not self._running:
                if self._executor is None:self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-checkpoint')
                self._running=True;self._executor.submit(self._run)

    def _run(self):
        while True:
            with self._condition:
                if self._pending is None:
                    self._running=False;self._condition.notify_all();return
                session=self._pending;self._pending=None
            try:self._write(session)
            except BaseException as error:
                with self._condition:
                    self._error=error;self._running=False;self._condition.notify_all()
                return
            with self._condition:
                self.written+=1;self._condition.notify_all()

    def flush(self):
        with self._condition:
            while True:
                if self._error is not None:raise self._error
                if not self._running and self._pending is None:return
                self._condition.wait()

    def close(self):
        try:self.flush()
        finally:
            with self._condition:self.closed=True
            if self._executor is not None:self._executor.shutdown(wait=True)


def product_scope(method):
    @wraps(method)
    def scoped(self,*args,**kwargs):
        with self.application._lock:
            with self.application.store.background_saves():return method(self,*args,**kwargs)
    return scoped
