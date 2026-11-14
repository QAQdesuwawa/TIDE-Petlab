"""One frozen semantic request; only its provider transport leaves the owner."""
from threading import get_ident


class PreparedMeasurement:
    def __init__(self,app,event,steps):
        self._app=app;self._event=event;self._steps=steps;self._owner=get_ident()
        self._done=False;self._error=None;self._value=None;self._call=None
        try:self._call=next(steps)
        except StopIteration as result:
            self._value=result.value;self._done=True
        # Temporal request reservation may publish metadata, but not new roots.
        self._position=app.runtime.session.position.to_record()

    def transport(self):
        if self._call is not None:self._call.transport()

    def finish(self,*,playback_output=False):
        if get_ident()!=self._owner:raise RuntimeError('measurement finish requires owner thread')
        if self._error is not None:raise self._error
        if self._done:return self._value
        if self._app.runtime.session.position.to_record()!=self._position:
            from petlab_app.measurement_pipeline import after_parent,follows_playback_input
            if not ((playback_output and follows_playback_input(self._app,self._event,self._position))
                    or after_parent(self._app,self._event,self._position)):
                raise ValueError('measurement prepared position is stale')
        self._app._verify_source(self._event)
        result=self._call.finish()
        try:
            self._steps.send(result)
        except StopIteration as finished:
            self._value=finished.value;self._done=True
            return self._value
        except BaseException as error:
            self._error=error
            raise
        else:
            self._error=RuntimeError('one measurement unexpectedly requested multiple providers')
            raise self._error
