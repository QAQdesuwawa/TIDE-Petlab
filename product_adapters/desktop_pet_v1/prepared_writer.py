"""Writer network work is independent of its source-bound candidate acceptance."""
from threading import get_ident


class PreparedWriter:
    def __init__(self,writer,event_id,steps):
        self._writer=writer;self._event_id=event_id;self._steps=steps
        self._owner=get_ident();self._done=False;self._value=None;self._error=None;self._call=None
        self._advance(None,initial=True)
        self._position=writer.application.runtime.session.position.to_record()

    def _advance(self,result,*,initial=False):
        writer=self._writer
        with writer.application._lock,writer.attempt_journal.batch(),writer.attempt_journal._readers.scope():
            try:
                call=next(self._steps) if initial else self._steps.send(result)
            except StopIteration as finished:
                self._value=finished.value;self._done=True
                return
            if not initial:raise RuntimeError('Writer unexpectedly requested multiple providers')
            self._call=call

    def transport(self):
        if self._call is not None:self._call.transport()

    def finish(self):
        return self._finish(archive_only=False)

    def archive(self):
        """Seal the original result even when stale; never rebind its position."""
        return self._finish(archive_only=True)

    def _finish(self,*,archive_only):
        if get_ident()!=self._owner:raise RuntimeError('Writer finish requires owner thread')
        if self._error is not None:raise self._error
        app=self._writer.application
        if not archive_only and app.runtime.session.position.to_record()!=self._position:
            raise ValueError('Writer prepared position is stale')
        if self._done:return self._value
        # Persist the actual response even if newer state prevents using it.
        result=self._call.finish()
        app=self._writer.application
        with app._lock:
            if not archive_only and app.runtime.session.position.to_record()!=self._position:
                raise ValueError('Writer prepared position is stale')
            try:self._advance(result)
            except BaseException as error:
                self._error=error
                raise
        return self._value
