"""Actor-owned settlement while a source-bound Writer transport is pending."""
from concurrent.futures import ThreadPoolExecutor,wait
from .delivery_admission import current_input


class ReplanIntention(Exception):pass


class QualifiedTakeover(Exception):
    def __init__(self,delivery):
        self.deliveries=(delivery,)
        super().__init__('new accepted qualification superseded pending realization')


def _prepare_for_playback(product,event_id,is_cancelled,kwargs):
    """Keep the authorized source stable until the real executor starts.

    Its existing PlaybackIntake owns concurrent input settlement. Accepting
    another source here would invalidate this Writer before it can be shown.
    Physical cancellation still retires the actual transport without waiting.
    """
    from .retired_transport import retain,WriterInterrupted
    work=product.writer.begin(event_id,**kwargs)
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-writer-provider')
    detached=False
    try:
        future=pool.submit(work.transport)
        while not future.done():
            if is_cancelled():
                retain(product,work,future);detached=True
                raise WriterInterrupted()
            wait((future,),timeout=.02)
        future.result()
        if is_cancelled():
            retain(product,work,future);detached=True
            raise WriterInterrupted()
        return work.finish()
    except Exception:
        if not detached:
            # Preserve the dispatched attempt on failure; never resend it.
            try:work.archive()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('writer_archive_failed source=%s',event_id)
        raise
    finally:
        pool.shutdown(wait=not detached)


def prepare(link,event_id,*,is_cancelled=lambda:False,**kwargs):
    product=link.product;app=product.application
    if callable(getattr(link,'playback_intake',None)):
        return _prepare_for_playback(product,event_id,is_cancelled,kwargs)
    from .intention_continuation import current
    from .intention_source import enabled
    from .peer_reaction import is_peer,assess,react
    from .idle_opening import is_idle_tick
    idle=is_idle_tick(app,event_id)
    if not enabled(app) or not (is_peer(app,event_id) or idle) or current(app,event_id) is None:
        return product.writer.prepare(event_id,**kwargs)
    from .multi_pet import _key,_immutable
    from .participant_input import prepare as prepare_input
    from .retained_intention import retain
    from .intention_progress import planned
    from product_adapters.desktop_pet_v1.quiet_realization import QuietRealization
    old=next((r for r in link.workspace.inbox(link.alias)
              if 'participant:'+_key(r['delivery']['delivery_id'])==event_id),None)
    if idle:
        from .destinations import destination
        chosen=destination(app,event_id)
        conversation=chosen['conversation']['conversation_id'] if chosen else None
    else:
        if old is None:return product.writer.prepare(event_id,**kwargs)
        conversation=old['projection']['conversation_id']
    work=product.writer.begin(event_id,**kwargs)
    incoming=None;measurement=None;mf=None;grant=None;intake_open=True
    detached=False
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='pet-provider')
    try:
        wf=pool.submit(work.transport)
        while not wf.done() or mf is not None:
            if mf is None and not wf.done() and (is_cancelled() or (grant is not None and grant['eligible'])):
                from .retired_transport import retain as retain_transport,WriterInterrupted
                retain_transport(product,work,wf);detached=True
                if is_cancelled():raise WriterInterrupted()
                raise QualifiedTakeover(incoming['delivery']['delivery_id'])
            if intake_open and incoming is None and not is_cancelled() and not wf.done():
                link._refresh()
                incoming=next((r for r in link._pending
                    if r['sender_kind']=='npc' and (r.get('visible') or r['delivery']['delivery_scope']=='addressed')
                    and current_input(r,old,getattr(link,'_human_frontier',None))
                    and (conversation is None or r['projection']['conversation_id']==conversation)
                    and 'participant:'+_key(r['delivery']['delivery_id']) not in app._product()['sources']
                    and not (link.local/'failed'/(_key(r['delivery']['delivery_id'])[:24]+'.json')).exists()),None)
                if incoming is not None:
                    admit=getattr(link,'admit_waiting_input',None)
                    if admit is not None and not admit(incoming):
                        # Capacity admission precedes source acceptance. The
                        # original intention drains; this input stays queued.
                        incoming=None;intake_open=False
                if incoming is not None:
                    args=prepare_input(app,incoming,link.workspace)
                    event=app.accept_input(scope_ref=app.definition.scope_ref,actor_ref=args['input_source']['actor_ref'],**args)
                    measurement=product.semantic.prepare(event)
                    mf=pool.submit(measurement.transport)
            if mf is not None and mf.done():
                mf.result()
                payload=measurement.finish()
                app.settle_received(event.event_id,measure=lambda _:payload)
                grant=assess(app,event.event_id)
                if not grant['eligible']:
                    retain(app,event_id,event.event_id)
                    react(product,event.event_id,executor=None)
                    identity=incoming['delivery']['delivery_id']
                    _immutable(link.local/'accepted'/(_key(identity)[:24]+'.json'),
                        {'delivery_id':identity,'status':'settled','sender_kind':incoming['sender_kind']})
                    link._pending=[r for r in link._pending if r['delivery']['delivery_id']!=identity]
                mf=None
            if not wf.done() or mf is not None:
                wait((mf,) if wf.done() else (wf,),timeout=.02)
        wf.result()
    except Exception as error:
        if detached:raise
        # Executor shutdown drains already started calls. Preserve their results
        # without retrying or executing the abandoned suffix.
        import traceback
        if incoming is not None:
            identity=incoming['delivery']['delivery_id']
            _immutable(link.local/'failed'/(_key(identity)[:24]+'.json'),
                {'delivery_id':identity,'waiting_intention':event_id,'error_type':type(error).__name__,
                 'frames':[{'file':__import__('pathlib').Path(f.filename).name,
                            'line':f.lineno,'function':f.name}
                           for f in traceback.extract_tb(error.__traceback__)]})
        try:
            wf.result()
            archived=work.archive()
            if not isinstance(archived,QuietRealization):planned(app,event_id,archived,writer=product.writer)
        except Exception as archive_error:
            import logging
            logging.getLogger(__name__).error('pending_writer_archive_failed source=%s error_type=%s',
                event_id,type(archive_error).__name__)
        raise
    finally:
        pool.shutdown(wait=not detached)
    if incoming is None:return work.finish()
    archived=work.archive()
    if not isinstance(archived,QuietRealization):planned(app,event_id,archived,writer=product.writer)
    # Physical cancellation has its own cause. A new accepted assessment does
    # not relabel a user interruption as a psychological takeover.
    if is_cancelled():return archived
    if grant['eligible']:raise QualifiedTakeover(incoming['delivery']['delivery_id'])
    if isinstance(archived,QuietRealization):return archived
    raise ReplanIntention()
