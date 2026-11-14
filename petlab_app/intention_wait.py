"""Actor-owned settlement while a source-bound Writer transport is pending."""
from concurrent.futures import FIRST_COMPLETED,ThreadPoolExecutor,wait
from .delivery_admission import current_input,reply_conflict


class ReplanIntention(Exception):
    def __init__(self,replay=None):
        self.replay=replay
        super().__init__('Writer planning state changed')


class QualifiedTakeover(Exception):
    def __init__(self,delivery):
        self.deliveries=(delivery,)
        super().__init__('new accepted qualification superseded pending realization')


def _finish_current(work):
    from product_adapters.desktop_pet_v1.prepared_writer import StaleWriterPosition
    try:
        return work.finish()
    except StaleWriterPosition:
        work.archive()
        source=getattr(work,'replay_source',None)
        raise ReplanIntention(None if source is None else source()) from None


def _prepare_cancellable(product,event_id,is_cancelled,kwargs):
    """Keep physical cancellation responsive without admitting a peer input."""
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
        return _finish_current(work)
    except ReplanIntention:
        raise
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
    from .intention_continuation import current
    from .intention_source import enabled
    from .peer_reaction import is_peer,assess,react
    from .idle_opening import is_idle_tick
    def prepare_without_intake():
        if callable(getattr(link,'playback_intake',None)):
            return _prepare_cancellable(product,event_id,is_cancelled,kwargs)
        return product.writer.prepare(event_id,**kwargs)

    idle=is_idle_tick(app,event_id)
    if not (is_peer(app,event_id) or (idle and enabled(app))) or current(app,event_id) is None:
        return prepare_without_intake()
    from .multi_pet import _key,_immutable
    from .participant_input import prepare as prepare_input
    from .retained_intention import retain
    from .intention_progress import planned
    from product_adapters.desktop_pet_v1.quiet_realization import QuietRealization
    from .delivery_admission import retained_delivery,source_delivery
    old=None
    if idle:
        from .destinations import destination
        chosen=destination(app,event_id)
        conversation=chosen['conversation']['conversation_id'] if chosen else None
    else:
        if not event_id.startswith('participant:'):return prepare_without_intake()
        try:old=source_delivery(link,event_id)
        except FileNotFoundError:return prepare_without_intake()
        conversation=old['projection']['conversation_id']
    try:latest_input=retained_delivery(link,event_id,old)
    except FileNotFoundError:return prepare_without_intake()
    work=product.writer.begin(event_id,**kwargs)
    incoming=None;measurement=None;mf=None;grant=None;intake_open=True
    detached=False
    settled_input=False
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='pet-provider')
    try:
        wf=pool.submit(work.transport)
        while True:
            cancelled=is_cancelled()
            if mf is None and (cancelled or (grant is not None and grant['eligible'])):
                from .retired_transport import retain as retain_transport,WriterInterrupted
                retain_transport(product,work,wf);detached=True
                if cancelled:raise WriterInterrupted()
                raise QualifiedTakeover(incoming['delivery']['delivery_id'])
            if wf.done() and mf is None:break
            can_prepare=intake_open and incoming is None and not cancelled and not wf.done()
            notice=getattr(link,'notice_waiting_speech',None)
            if can_prepare or (notice is not None and not cancelled):
                link._refresh()
                if notice is not None:notice()
            if can_prepare:
                incoming=next((r for r in link._pending
                    if reply_conflict(r,conversation,reference=old)
                    and current_input(r,latest_input,getattr(link,'_human_frontier',None))
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
                    settled_input=True
                    latest_input=incoming
                    incoming=None
                mf=None
            pending=tuple(future for future in (wf,mf) if future is not None and not future.done())
            if pending:wait(pending,timeout=.02,return_when=FIRST_COMPLETED)
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
    if not settled_input:return _finish_current(work)
    archived=work.archive()
    if not isinstance(archived,QuietRealization):planned(app,event_id,archived,writer=product.writer)
    # Physical cancellation has its own cause. A new accepted assessment does
    # not relabel a user interruption as a psychological takeover.
    if is_cancelled():return archived
    raise ReplanIntention(work.replay_source())
