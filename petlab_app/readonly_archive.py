"""Deferred duplicate views of already-durable canonical receipts.

A missing view never loses a paid response or an execution candidate. Only
immutable snapshots enter this worker; it owns no app, model, clock or effects.
"""
from concurrent.futures import ThreadPoolExecutor
import logging,threading
from framework.model_api import freeze_model_payload,thaw_model_data


class ReadOnlyArchives:
    def __init__(self):
        self._pending=None;self._executor=None;self._futures=[]
        self.errors=[];self._lock=threading.RLock()

    def defer(self,path,record):
        with self._lock:
            if self._pending is not None:
                old_path,old=self._pending
                if old_path==path and old['fingerprint']==record['fingerprint']:return
            previous=self._pending
            self._pending=(path,freeze_model_payload(record))
        if previous is not None:self._submit(previous)

    def _submit(self,item):
        # At most two queued/running duplicate views; backpressure affects
        # operational publication only, never model time or recall selection.
        with self._lock:
            self._futures[:]=[f for f in self._futures if not f.done()]
            blocker=self._futures[0] if len(self._futures)>=2 else None
        if blocker is not None:blocker.result()
        with self._lock:
            self._futures[:]=[f for f in self._futures if not f.done()]
            if self._executor is None:self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-readonly-archive')
            self._futures.append(self._executor.submit(self._write,*item))

    def _write(self,path,record):
        from .providers import _save_trace
        try:_save_trace(path,thaw_model_data(record))
        except Exception as error:
            with self._lock:
                self.errors.append({'path':str(path),'error_type':type(error).__name__})
                self.errors[:]=self.errors[-32:]
            logging.getLogger(__name__).error('readonly_archive_failed error_type=%s',type(error).__name__)

    def publish(self,*,wait=False):
        with self._lock:item=self._pending;self._pending=None
        if item is not None:self._submit(item)
        with self._lock:futures=tuple(self._futures)
        if wait:
            for future in futures:future.result()
        with self._lock:self._futures[:]=[f for f in self._futures if not f.done()]

    def close(self):
        self.publish(wait=True)
        if self._executor is not None:self._executor.shutdown(wait=True)
