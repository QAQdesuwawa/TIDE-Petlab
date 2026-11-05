"""Owner-thread input settlement while the executor waits for actual playback."""
from concurrent.futures import ThreadPoolExecutor
from .multi_pet import _key,_immutable


class PlaybackIntake:
    def __init__(self,link,action_id,is_cancelled=lambda:False):
        self.link=link;self.product=link.product;self.app=self.product.application
        self.action_id=action_id;self.cancelled=is_cancelled
        self.pool=None;self.future=None;self.row=None;self.proof=None;self.error=None;self.closed=False
        from .intention_source import enabled
        self.enabled=enabled(self.app)
        self.detachable=bool(getattr(link,'_defer_playback_settlement',False))
    def __enter__(self):return self
    def __exit__(self,*args):
        try:self.finish()
        finally:
            if self.pool is not None:self.pool.shutdown(wait=not self.detachable)
    def poll(self,*,admit=True):
        if self.closed or not self.enabled:return self.proof
        try:
            if self.row is None and admit and not self.cancelled():
                self.link._refresh()
                from .delivery_admission import current_input
                self.row=next((r for r in self.link._pending
                    if r['sender_kind']=='npc' and (r.get('visible') or r['delivery']['delivery_scope']=='addressed')
                    and current_input(r,None,getattr(self.link,'_human_frontier',None))
                    and 'participant:'+_key(r['delivery']['delivery_id']) not in self.app._product()['sources']
                    and not (self.link.local/'failed'/(_key(r['delivery']['delivery_id'])[:24]+'.json')).exists()),None)
                if self.row is not None:
                    gate=getattr(self.link,'admit_waiting_input',None)
                    if gate is not None and not gate(self.row):self.closed=True;return None
                    from .participant_input import prepare
                    args=prepare(self.app,self.row,self.link.workspace)
                    self.event=self.app.accept_input(scope_ref=self.app.definition.scope_ref,
                        actor_ref=args['input_source']['actor_ref'],**args)
                    self.work=self.product.semantic.prepare(self.event)
                    self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-playback-provider')
                    self.future=self.pool.submit(self.work.transport)
            if self.future is not None and self.future.done():
                self.future.result();payload=self.work.finish()
                self.app.settle_received(self.event.event_id,measure=lambda _:payload)
                from .peer_reaction import assess,react
                from threading import get_ident
                prior=getattr(self.app,'_recovery_projection',None)
                if self.app._product()['executions'].get(self.action_id,{}).get('status')=='executed':
                    self.app._recovery_projection=(self.action_id,self.event.event_id,get_ident())
                try:grant=assess(self.app,self.event.event_id)
                finally:self.app._recovery_projection=prior
                identity=self.row['delivery']['delivery_id']
                if grant['eligible']:
                    from .execution_inputs import remember
                    # Playback may already be durably sealed. The same accepted
                    # input keeps its original linkage; it does not cancel an
                    # effect retroactively or receive a new permission.
                    from threading import get_ident
                    prior=getattr(self.app,'_recovery_projection',None)
                    if self.app._product()['executions'].get(self.action_id,{}).get('status')=='executed':
                        self.app._recovery_projection=(self.action_id,self.event.event_id,get_ident())
                    try:remember(self.app,self.action_id,self.event.event_id)
                    finally:self.app._recovery_projection=prior
                    self.proof={'event_id':self.event.event_id,'delivery_id':identity,'grant_fingerprint':grant['fingerprint']}
                else:
                    react(self.product,self.event.event_id,executor=None)
                    _immutable(self.link.local/'accepted'/(_key(identity)[:24]+'.json'),
                        {'delivery_id':identity,'status':'settled','sender_kind':self.row['sender_kind']})
                    self.link._pending=[r for r in self.link._pending if r['delivery']['delivery_id']!=identity]
                self.closed=True
        except Exception as error:
            self.error=error;self.closed=True
            if self.row is not None:
                identity=self.row['delivery']['delivery_id']
                _immutable(self.link.local/'failed'/(_key(identity)[:24]+'.json'),
                    {'delivery_id':identity,'during_execution':self.action_id,'error_type':type(error).__name__})
        return self.proof
    def finish(self):
        if self.future is not None and not self.closed:
            if self.detachable and not self.future.done():
                previous=getattr(self.link,'_pending_playback_intake',None)
                if previous is not None and previous is not self:
                    raise RuntimeError('another playback settlement is already pending')
                self.link._pending_playback_intake=self
                self.app._pending_playback_intake=self
                return self.proof
            # Do not discard the UI outcome if a provider failed. The accepted
            # source and failed receipt retain the recovery obligation.
            try:self.future.result()
            except Exception:pass
            self.poll(admit=False)
        if self.closed and getattr(self.link,'_pending_playback_intake',None) is self:
            self.link._pending_playback_intake=None
            self.app._pending_playback_intake=None
        return self.proof


def waiting(link):
    work=getattr(link,'_pending_playback_intake',None)
    return work is not None and not work.future.done()


def finish_pending(link):
    """Owner-thread continuation; never redispatch or replay the sealed action."""
    work=getattr(link,'_pending_playback_intake',None)
    if work is None:return True
    if not work.future.done():return False
    try:
        work.finish()
        if work.error is not None:raise work.error
        link.product.resume(executor=None)
        link.finish_suspended_playback(work.action_id)
        idle=getattr(link,'_suspended_idle',None)
        if idle is not None:idle.finish_playback(work.action_id)
    except Exception as error:
        identity=getattr(link,'_suspended_delivery',None)
        if identity is not None:
            _immutable(link.local/'failed'/(_key(identity)[:24]+'.json'),
                       {'delivery_id':identity,'error_type':type(error).__name__,
                        'during_execution':work.action_id})
        raise
    link._pending_playback_intake=None
    link.product.application._pending_playback_intake=None
    link._suspended_delivery=None
    link._suspended_idle=None
    return True


def archive_pending(product):
    """Orderly shutdown saves the received measurement, without new effects."""
    work=getattr(product.application,'_pending_playback_intake',None)
    if work is None or work.closed:return
    try:work.future.result()
    except Exception:pass  # finish persists the original provider failure.
    from .measurement_pipeline import stash
    stash(product.application,work.event.event_id,work.work.finish())
