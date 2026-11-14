"""Owner-thread input settlement while the executor waits for actual playback."""
from concurrent.futures import ThreadPoolExecutor
from .multi_pet import _key,_immutable,_read,_verify


def _same_reply_slot(app, publisher, action_id, row, *, reference=None):
    from .destinations import destination
    parent=getattr(app,'_prefetch_reply_source',None)
    target=destination(app,parent) if parent is not None else None
    from .delivery_admission import reply_conflict
    if (target is None or not reply_conflict(row,target['conversation']['conversation_id'],reference=reference)
            or ('membership_version' in target['conversation']
                and target['conversation']['membership_version']!=row['projection'].get('membership_version'))):
        return False
    if publisher is not None:
        try:meta=_verify(_read(publisher.directory(action_id)/'source.json'))
        except FileNotFoundError:return True  # Legacy executors have no visible metadata.
        if (meta['parent_action_id']!=action_id or meta['owner_ref']!=app.owner_ref
                or meta['target_ref']!=target['primary']):
            raise ValueError('speech takeover differs from sealed playback destination')
        return tuple(meta.get('speech_to',(target['primary'],)))==(target['primary'],)
    return True


def _human_followup(app,row,reference,*,allow_invitation=False):
    """Match text from the same human conversation at the appropriate boundary."""
    if reference is None or reference.get('sender_kind')!='human' or row['sender_kind']!='human':
        return False
    p=row['projection'];old=reference['projection']
    if row['delivery']['delivery_scope']!='addressed' or any(p.get(key)!=old.get(key) for key in
            ('conversation_id','membership_version','recipient_participant_id','sender_participant_id')):
        return False
    policy=row.get('response_policy')
    if policy!='required' and not (allow_invitation and policy=='eligible' and p['conversation_id']=='petlab:public'):
        return False
    source=app.multi_workspace.source_event(p['source_event_id'])
    from .multi_pet import OPEN_INVITATION_POLICY
    return ('human_content' not in source and
            (policy=='required' or source.get('origin',{}).get('opening_policy')==OPEN_INVITATION_POLICY))


class PlaybackIntake:
    def __init__(self,link,action_id,is_cancelled=lambda:False):
        self.link=link;self.product=link.product;self.app=self.product.application
        self.action_id=action_id;self.cancelled=is_cancelled
        self.pool=None;self.future=None;self.row=None;self.proof=None;self.error=None;self.closed=False
        self.output_work=None;self.output_future=None
        self.on_speech_hold=None
        self.prefetch_writer=True
        self.detachable=bool(getattr(link,'_defer_playback_settlement',False))
    def __enter__(self):return self
    def __exit__(self,*args):
        try:self.finish()
        finally:
            if self.pool is not None:self.pool.shutdown(wait=not self.detachable)
    def poll(self,*,admit=True):
        if self.closed:return self.proof
        if getattr(self.link,'_following_effect',None) is not None:return None
        try:
            if admit and not self.cancelled():
                notice=getattr(self.link,'notice_waiting_speech',None)
                if self.row is None or notice is not None:
                    self.link._refresh()
                    if notice is not None:notice()
            if self.row is None and admit and not self.cancelled():
                from .delivery_admission import current_input,retained_delivery,source_delivery
                if not self.link._pending:return None
                try:
                    source=getattr(self.app,'_prefetch_reply_source',None)
                    active=source_delivery(self.link,source)
                    latest=retained_delivery(self.link,source,active)
                except FileNotFoundError:
                    self.closed=True
                    return None
                self.active_delivery=active
                self.row=next((r for r in self.link._pending
                    if ((r['sender_kind']=='npc' and (r.get('visible') or r['delivery']['delivery_scope']=='addressed'))
                        or _human_followup(self.app,r,active,allow_invitation=True))
                    and current_input(r,latest,getattr(self.link,'_human_frontier',None))
                    and not self.link._covered_start(r)
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
                    if self.on_speech_hold is not None and (_human_followup(self.app,self.row,self.active_delivery) or _same_reply_slot(
                            self.app,getattr(self.product,'visible_execution_publisher',None),
                            self.action_id,self.row,reference=self.active_delivery)):
                        self.on_speech_hold()
                    self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-playback-provider')
                    self.future=self.pool.submit(self.work.transport)
            if self.future is not None and self.future.done():
                self.future.result();payload=self.work.finish()
                self.app.settle_received(self.event.event_id,measure=lambda _:payload)
                if _human_followup(self.app,self.row,self.active_delivery):
                    from .destinations import destination
                    target=destination(self.app,self.app._prefetch_reply_source)
                    self.proof={'parent_action_id':self.action_id,'playback_target_ref':target['primary'],
                        'event_id':self.event.event_id,'delivery_id':self.row['delivery']['delivery_id'],
                        'input_fact':self.app.runtime.memory_instance(self.event.event_id).fact.fingerprint,
                        'actor_ref':self.event.actor_ref,'kind':'required_human_followup'}
                    self.closed=True
                    if self.detachable:
                        self.link._settled_playback_followup=(self.action_id,self.event.event_id,self.row)
                    if self.on_speech_hold is not None:
                        self._prefetch_followup(admit)
                    return self.proof
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
                    executed=self.app._product()['executions'][self.action_id]['status']=='executed'
                    if executed:
                        self.app._recovery_projection=(self.action_id,self.event.event_id,get_ident())
                    try:
                        remember(self.app,self.action_id,self.event.event_id)
                        if _human_followup(self.app,self.row,self.active_delivery,allow_invitation=True):
                            if self.detachable and not executed:
                                self.link._settled_playback_followup=(self.action_id,self.event.event_id,self.row)
                            elif executed:
                                self._prefetch_followup(True)
                    finally:self.app._recovery_projection=prior
                    from .delivery_admission import can_interrupt_peer_reply
                    if can_interrupt_peer_reply(self.row) and _same_reply_slot(
                            self.app,getattr(self.product,'visible_execution_publisher',None),self.action_id,self.row,reference=getattr(self,'active_delivery',None)):
                        from .destinations import destination
                        playback_target=destination(self.app,getattr(self.app,'_prefetch_reply_source',None))['primary']
                        self.proof={'parent_action_id':self.action_id,'playback_target_ref':playback_target,'event_id':self.event.event_id,'delivery_id':identity,
                                    'grant_fingerprint':grant['fingerprint'],
                                    'actor_ref':self.row['projection']['sender_participant_id']}
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
    def _prefetch_followup(self,admit):
        if not self.prefetch_writer or not admit or self.cancelled() or self.product.writer is None:
            return
        try:
            self.product.writer.prefetch_followup(self.product,self.event.event_id,self.action_id)
        except Exception:
            # A draft cannot invalidate the settled input or its qualification.
            from logging import getLogger
            getLogger(__name__).exception('follow-up Writer preparation failed')

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

    def prepare_output(self,event):
        """Start only transport, after the actor has archived the actual effect."""
        if self.output_work is not None or event.event_id!=self.action_id:
            raise ValueError('playback output preparation differs from its receipt')
        self.output_work=self.product.semantic.prepare(event)
        self.output_position=self.app.runtime.session.position.to_record()
        pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-playback-output')
        try:
            self.output_future=pool.submit(self.output_work.transport)
        finally:
            pool.shutdown(wait=False)

    def archive_output(self):
        if self.output_future is None:
            return
        self.output_future.result()
        from .measurement_pipeline import stash_playback_output
        stash_playback_output(self.app,self.action_id,self.event,self.output_work.finish(playback_output=True),
            prepared_position=self.output_position)


def finish_pending(link):
    """Owner-thread continuation; never redispatch or replay the sealed action."""
    work=getattr(link,'_pending_playback_intake',None)
    if work is None:return True
    if not work.future.done():return False
    if work.output_future is not None and not work.output_future.done():
        # Settle the already received supplement on the owner thread. The
        # preceding actual effect is sealed and its transport can keep running.
        if work.row.get('response_policy')=='eligible' and _human_followup(
                work.app,work.row,work.active_delivery,allow_invitation=True):
            work.poll(admit=False)
        return False
    try:
        work.archive_output()
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
    if work is None:return
    work.archive_output()
    if work.closed:return
    try:work.future.result()
    except Exception:pass  # finish persists the original provider failure.
    from .measurement_pipeline import stash
    stash(product.application,work.event.event_id,work.work.finish())


def continue_after_playback(link,effect,*,executor,completion_coordinate,is_cancelled,on_reply_ready):
    """Use the settled follow-up while the sealed preceding effect is measured."""
    from .measurement_pipeline import admission
    from product_adapters.desktop_pet_v1.realization import WriterRealizationFailed

    app=link.product.application
    product=link.product
    preceding=getattr(link,'_following_effect',None)
    if preceding is not None:
        previous,work,future=preceding
        future.result()
        app._settle(previous,lambda _:work.finish())
        return

    retained=getattr(link,'_settled_playback_followup',None)
    if retained is None or retained[0]!=effect.event_id:return
    link._settled_playback_followup=None
    _,event_id,row=retained
    def newest():
        from .delivery_admission import after_source
        from .peer_reaction import newly_qualified_delivery
        link._refresh()
        return not any(
            after_source(incoming,row) and (_human_followup(app,incoming,row)
                or (_human_followup(app,incoming,row,allow_invitation=True)
                    and newly_qualified_delivery(app,incoming['delivery']['delivery_id'])))
            for incoming in link._pending)
    if executor is None or is_cancelled() or not newest():return

    app._verify_source(effect)
    app.on_execution_committed(effect)
    work=product.semantic.prepare(effect)
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-preceding-effect')
    try:future=pool.submit(work.transport)
    finally:pool.shutdown(wait=False)
    link._following_effect=(effect,work,future)
    try:
        with admission(app,effect.event_id):
            if row.get('response_policy')=='eligible':
                from .peer_reaction import react
                outcome=react(product,event_id,executor=executor,completion_coordinate=completion_coordinate,
                    is_cancelled=lambda:is_cancelled() or not newest(),on_reply_ready=on_reply_ready)
                if outcome['status'] not in {'executed','quiet','technical_failure'}:return
                status=outcome['status'] if outcome['status']=='technical_failure' else None
            else:
                try:
                    result=product._reply(event_id,executor=executor,completion_coordinate=completion_coordinate,
                        is_cancelled=is_cancelled,admit_reply=newest,on_reply_ready=on_reply_ready)
                except WriterRealizationFailed:
                    status='technical_failure'
                else:
                    if result is None and product.last_quiet_realization is None:return
                    status=None
        identity=row['delivery']['delivery_id']
        receipt={'delivery_id':identity,'status':'settled','sender_kind':row['sender_kind']}
        if status is not None:receipt['reply_status']=status
        _immutable(link.local/'accepted'/(_key(identity)[:24]+'.json'),receipt)
        link._pending=[r for r in link._pending if r['delivery']['delivery_id']!=identity]
    finally:
        try:
            future.result()
            app._settle(effect,lambda _:work.finish())
        finally:
            link._following_effect=None
