"""Owner-thread input settlement while the executor waits for actual playback."""
from concurrent.futures import ThreadPoolExecutor
from .multi_pet import _key,_immutable,_read,_verify


def prefetch_world_for_writer(link, source_ref):
    """Start one actual scene receipt while Writer owns the current position."""
    app=link.product.application
    saved=getattr(app,'_writer_world_intake',None)
    if saved is not None:
        if writer_world_input(app,saved[0]) is not None:return False
        app._writer_world_intake=None
    from .desktop_routines import next_world
    world=next_world(link)
    if world is None:return False
    intake=PlaybackIntake(link,None)
    intake.take_world(world)
    intake.pool.shutdown(wait=False)
    intake.pool=None
    if not hasattr(link.product.semantic,'_prefetched'):link.product.semantic._prefetched={}
    link.product.semantic._prefetched[intake.event.event_id]=(intake.work,intake.future)
    app._writer_world_intake=(source_ref,intake)
    return True


def writer_world_input(app, source_ref):
    saved=getattr(app,'_writer_world_intake',None)
    if saved is None or saved[0]!=source_ref:return None
    intake=saved[1]
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    if intake.event.event_id in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):return None
    app._verify_source(intake.event)
    return intake


def for_action(link, action_id, is_cancelled=lambda:False):
    app=link.product.application
    saved=getattr(app,'_writer_world_intake',None)
    if saved is not None and saved[1].action_id==action_id:
        app._writer_world_intake=None
        intake=saved[1]
        intake.cancelled=is_cancelled
        link.product.semantic._prefetched.pop(intake.event.event_id,None)
        return intake
    return PlaybackIntake(link,action_id,is_cancelled)


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


def _bound_speech_ids(product, action_id, opening_source, target):
    """Bind selected speech children to one dispatched Writer and direct listener."""
    publisher=getattr(product,'visible_execution_publisher',None)
    if publisher is None:return None
    path=publisher.directory(action_id)/'source.json'
    if not path.is_file():return None
    meta=_verify(_read(path))
    if meta['parent_action_id']!=action_id or meta['target_ref']!=target:
        raise ValueError('opening revision and playback target differ')
    if 'speech_to' not in meta:return None
    if tuple(meta['speech_to'])!=(target,):return []
    from product_adapters.desktop_pet_v1.composite_journal import ParentJournal
    state=ParentJournal(product.gateway.trace_dir/'parents',action_id).state()
    source=state.get('source',{})
    if not source:return None
    if source['prepared_fingerprint']!=meta['prepared_fingerprint']:
        raise ValueError('opening revision and dispatched candidate differ')
    if source['source_event_id']!=opening_source:return []
    # Late candidates with their own Writer have no prepared child here.
    bound=[]
    for key,row in state['children'].items():
        content=row.get('prepared',{}).get('content',{})
        if content.get('kind')=='speech' and target in content['recipient_refs']:
            bound.append(key)
    return bound


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
        self.world=None
        self.output_work=None;self.output_future=None
        self.on_speech_hold=None
        self.on_takeover=None
        self.prefetch_writer=True
        self.detachable=bool(getattr(link,'_defer_playback_settlement',False))
    def __enter__(self):return self
    def __exit__(self,*args):
        try:self.finish()
        finally:
            if self.pool is not None:self.pool.shutdown(wait=not self.detachable)
    def _measure(self,args):
        self.event=self.app.accept_input(scope_ref=self.app.definition.scope_ref,
            actor_ref=args['input_source']['actor_ref'],**args)
        self.work=self.product.semantic.prepare(self.event)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-playback-provider')
        self.future=self.pool.submit(self.work.transport)
    def take_world(self,world):
        from .screen_perception import prepare_input
        self.world=world
        self._measure(prepare_input(self.app,world[1]))
    def poll_next(self,*,admit=True):
        """After a settled input, admit the next input during the same playback.

        Never replace in-flight transport, failed work, or output settlement.
        Completed inputs already own durable source/assessment bindings.
        """
        if (admit and self.closed and self.error is None and self.future is not None
                and self.future.done() and self.output_work is None and not self.cancelled()
                and self.app._product()['executions'].get(self.action_id,{}).get('status')=='started'):
            callbacks=(self.on_speech_hold,self.on_takeover,self.prefetch_writer,self.detachable)
            completed=(*getattr(self,'settled_inputs',()),self.event.event_id)
            if self.pool is not None:self.pool.shutdown(wait=False)
            link,action,cancelled=self.link,self.action_id,self.cancelled
            self.__init__(link,action,cancelled)
            self.on_speech_hold,self.on_takeover,self.prefetch_writer,self.detachable=callbacks
            self.settled_inputs=completed
            for name in ('event','work','active_delivery'):
                self.__dict__.pop(name,None)
        return self.poll(admit=admit)
    def poll(self,*,admit=True):
        if self.closed:return self.proof
        if getattr(self.link,'_following_effect',None) is not None:return None
        try:
            if admit and not self.cancelled():
                notice=getattr(self.link,'notice_waiting_speech',None)
                if self.row is None or notice is not None:
                    self.link._refresh()
                    if notice is not None:notice()
            if self.future is None and admit and not self.cancelled():
                from .delivery_admission import current_input,retained_delivery,source_delivery
                active=latest=None
                if self.link._pending:
                    try:
                        source=getattr(self.app,'_prefetch_reply_source',None)
                        active=source_delivery(self.link,source)
                        latest=retained_delivery(self.link,source,active)
                    except FileNotFoundError:
                        self.closed=True
                        return None
                self.active_delivery=active
                from .visible_measurement import measured_parent
                self.row=next((r for r in self.link._pending
                    if ((r['sender_kind']=='npc' and (r.get('visible') or r['delivery']['delivery_scope']=='addressed'))
                        or _human_followup(self.app,r,active,allow_invitation=True))
                    and current_input(r,latest,getattr(self.link,'_human_frontier',None))
                    and not measured_parent(self.link,r,include_inflight=True)
                    and not self.link._covered_start(r)
                    and 'participant:'+_key(r['delivery']['delivery_id']) not in self.app._product()['sources']
                    and not (self.link.local/'failed'/(_key(r['delivery']['delivery_id'])[:24]+'.json')).exists()),None)
                if self.row is not None:
                    gate=getattr(self.link,'admit_waiting_input',None)
                    if gate is not None and not gate(self.row):self.closed=True;return None
                    from .participant_input import prepare
                    args=prepare(self.app,self.row,self.link.workspace)
                else:
                    from .desktop_routines import next_world
                    world=next_world(self.link)
                    if world is None:return None
                    self.take_world(world)
                if self.row is not None:self._measure(args)
            if self.future is not None and self.future.done():
                self.future.result();payload=self.work.finish()
                self.app.settle_received(self.event.event_id,measure=lambda _:payload)
                required_followup=self.row is not None and _human_followup(self.app,self.row,self.active_delivery)
                if required_followup or (self.row is not None and _same_reply_slot(
                        self.app,getattr(self.product,'visible_execution_publisher',None),
                        self.action_id,self.row,reference=self.active_delivery)):
                    from .destinations import destination
                    from .execution_inputs import speech_revision
                    source=getattr(self.app,'_prefetch_reply_source',None)
                    target=destination(self.app,source) if source is not None else None
                    entry=self.app._product()['executions'].get(self.action_id,{})
                    if target is not None and entry.get('status')=='started':
                        revision=speech_revision(self.app,self.event.event_id,target['primary'],opening_source=source)
                        if revision is not None:
                            self.proof={**revision,'parent_action_id':self.action_id,
                                'playback_target_ref':target['primary'],'event_id':self.event.event_id,
                                'delivery_id':self.row['delivery']['delivery_id'],'actor_ref':self.event.actor_ref}
                            if revision['cause']=='opening_support_reversed':
                                ids=_bound_speech_ids(self.product,self.action_id,source,target['primary'])
                                if ids is not None:self.proof['bound_speech_ids']=ids
                            if self.on_takeover is not None:
                                self.on_takeover(self.proof)
                if required_followup:
                    self.closed=True
                    if self.detachable:
                        self.link._settled_playback_followup=(self.action_id,self.event.event_id,self.row)
                    if self.app._product()['executions'][self.action_id]['status']=='executed':
                        from threading import get_ident
                        prior=getattr(self.app,'_recovery_projection',None)
                        self.app._recovery_projection=(self.action_id,self.event.event_id,get_ident())
                        try:self._prefetch_followup(True)
                        finally:self.app._recovery_projection=prior
                    elif self.on_speech_hold is not None:
                        self._prefetch_followup(admit)
                    return self.proof
                from .peer_reaction import assess,react
                from threading import get_ident
                prior=getattr(self.app,'_recovery_projection',None)
                if self.app._product()['executions'].get(self.action_id,{}).get('status')=='executed':
                    self.app._recovery_projection=(self.action_id,self.event.event_id,get_ident())
                try:grant=assess(self.app,self.event.event_id)
                finally:self.app._recovery_projection=prior
                if self.world is not None:
                    if grant['eligible']:
                        from .execution_inputs import remember
                        if self.app._product()['executions'][self.action_id]['status']=='executed':
                            self.app._recovery_projection=(self.action_id,self.event.event_id,get_ident())
                        try:remember(self.app,self.action_id,self.event.event_id)
                        finally:self.app._recovery_projection=prior
                    else:
                        from .screen_perception import settlement_receipt
                        path,material=self.world
                        outcome=react(self.product,self.event.event_id,executor=None)
                        _immutable(path.parent/'settled'/path.name,
                                   settlement_receipt(self.event.event_id,material,outcome))
                    self.closed=True
                    return None
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
        if work.row is not None and _human_followup(
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
