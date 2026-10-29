"""Product-only process host. Tk never owns the runtime, provider or checkpoint."""
from __future__ import annotations
from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import Future
import multiprocessing as mp
import queue,threading,os,uuid,json,time
from .session import ADMISSION_VERSION,POINT_ADMISSION_VERSION
from framework.model_api import thaw_model_data,model_data_fingerprint
from model_packages.desktop_pet_v1.events import Content
from model_packages.desktop_pet_v1.composite import CompositePlan
from product_adapters.desktop_pet_v1.execution import ExecutionOutcome,ExecutionCancelled,ExecutionUncertain
from .product import PetProduct,wall_clock_ms
from .providers import ChatTransport,ProviderGateway
from .composition import PetDefinition
from .ingress import ContinuousMouseInputAdapter


class ProcessDesktopActor:
    is_isolated=True
    startup_timeout_seconds=120

    def __init__(self,*,configuration,session_path,trace_dir,checkpoint_storage_mode="snapshot-v1",visual_capabilities=None,defer_startup=False,multi_workspace=None):
        if checkpoint_storage_mode not in {"snapshot-v1","delta-v1"}:raise ValueError("unknown checkpoint storage mode")
        if type(defer_startup) is not bool:raise ValueError("startup deferral must be boolean")
        self._deferred_startup=defer_startup;self._ready_received=False;self._activated=not defer_startup
        self._expected_visual_fingerprint=None if visual_capabilities is None else visual_capabilities['fingerprint']
        self.application=SimpleNamespace(definition=configuration.definition)
        self.observer=object() if configuration.observe_during_hold else None
        self.trace_dir=Path(trace_dir).resolve()
        self._stopping=False
        self._input_generation=0;self._activity_generation=0
        self.recovering=False
        self.opening_mode=configuration.opening_mode
        context=mp.get_context('spawn');self.incoming=context.Queue();self.outgoing=context.Queue()
        spec={'multi_workspace':multi_workspace,'defer_startup':defer_startup,'writer_model':configuration.writer_model,'idle_behavior':configuration.idle_behavior,'visual_capabilities':thaw_model_data(visual_capabilities),'batch_quiet_ms':configuration.mouse_batch_quiet_ms,'checkpoint_storage_mode':checkpoint_storage_mode,'definition':configuration.definition.to_record(),'transport':asdict(configuration.transport),
            'session_path':str(Path(session_path).resolve()),'trace_dir':str(Path(trace_dir).resolve()),
            'private_thought':configuration.private_thought,'temperature':configuration.writer_temperature,'numbered':configuration.numbered_memory,
            'links':configuration.memory_links,'budget':configuration.writer_budget_chars,
            'reading_sources':list(configuration.reading_sources),'reading_searches':list(configuration.reading_searches),'web_reading':thaw_model_data(configuration.web_reading) if configuration.web_reading is not None else None,
            'expression_profile':thaw_model_data(configuration.expression_profile),'expression_codes':configuration.expression_codes,'memory_recall_version':configuration.memory_recall_version,
            'mouse_definitions':thaw_model_data(configuration.mouse_definitions),
            'observe':configuration.observe_during_hold,'bounds':configuration.observation_bounds_ms,
            'response_policy':configuration.mouse_response_policy,'opening_mode':configuration.opening_mode}
        self.process=context.Process(target=_actor_main,args=(spec,self.incoming,self.outgoing),name='pet-serial-actor')
        self.process.start();self._startup_started=time.monotonic()
        if not defer_startup:self.wait_until_ready()

    def wait_until_ready(self):
        if self._ready_received:return self.startup_receipt
        try:
            # This is host liveness observation, never a psychological Tick.
            while True:
                remaining=self.startup_timeout_seconds-(time.monotonic()-self._startup_started)
                if remaining<=0:raise TimeoutError('desktop actor startup timed out')
                try:
                    kind,value=self.outgoing.get(timeout=min(.1,remaining))
                    break
                except queue.Empty:
                    if self.process.is_alive():continue
                    # Honor an already published error/receipt before diagnosing
                    # process death; multiprocessing delivery can race the wait.
                    try:kind,value=self.outgoing.get_nowait()
                    except queue.Empty:
                        raise RuntimeError('desktop actor exited before ready (exit code '+str(self.process.exitcode)+')') from None
                    break
            if kind!='ready':raise RuntimeError('desktop actor startup failed: '+str(value))
            if value['definition_fingerprint']!=self.application.definition.fingerprint:
                raise ValueError('desktop actor role differs from UI configuration')
            if value.get('visual_capabilities_fingerprint')!=self._expected_visual_fingerprint:
                raise ValueError('desktop actor visual capabilities differ from the host')
            self.startup_receipt=value
            self._input_generation=value.get('input_generation',0)
            self.recovery_pending=value.get('pending_ingress_count',0)
            self._ready_received=True
            return value
        except BaseException:
            self.request_stop();self.process.join(5);raise

    def activate(self):
        if self._stopping:raise RuntimeError('desktop actor startup was cancelled')
        self.wait_until_ready()
        if not self._activated:
            self.incoming.put(('startup_activate',None));self._activated=True

    def _require_active(self):
        process=getattr(self,'process',None)
        if process is not None and not process.is_alive():
            raise RuntimeError('desktop actor is not running; restart PetLab to restore retained work')
        if not getattr(self,'_ready_received',True) or not getattr(self,'_activated',True):
            raise RuntimeError('desktop actor requires visual activation before accepting input')

    def send_job(self,job):
        self._require_active()
        if getattr(self,'recovery_pending',0) and not getattr(self,'recovering',False) and job['kind']!='resume':
            raise RuntimeError('retained ingress requires explicit continuation first')
        if job['kind']=='resume' and getattr(self,'recovery_pending',0):self.recovering=True
        if job['kind'] in {'input','web_read','resume'} and not job.get('batch_id'):self.note_batch_activity()
        copied=dict(job)
        if self.opening_mode!='legacy':copied['opening_mode']=self.opening_mode
        if 'contents' in copied:copied['contents']=[content.to_record() for content in copied['contents']]
        self._input_generation+=1
        copied['_input_generation']=self._input_generation
        self.incoming.put(('job',copied))

    def resume_reading(self,event_id):
        self._require_active()
        self.incoming.put(('reading_resume',{'event_id':event_id}))

    def capture(self,operation,gesture_id,point=None,**fields):
        self._require_active()
        self.incoming.put(('capture',{'operation':operation,'gesture_id':gesture_id,'point':point,**fields}))

    def update_scene(self,value):
        from model_packages.desktop_pet_v1.spatial import validate
        if value is not None:
            value=validate(value)
            if value['owner_ref']!=self.application.definition.owner_ref:raise ValueError('scene actor differs')
        if not self._stopping:self.incoming.put(('scene_snapshot',value))

    def update_meal(self,value):
        from model_packages.desktop_pet_v1.shared_meal import validate
        if value is not None:
            value=validate(value)
            if self.application.definition.owner_ref not in value['participants']:raise ValueError('meal actor differs')
        if not self._stopping:self.incoming.put(('meal_snapshot',value))

    def note_batch_activity(self):
        self._require_active()
        self._activity_generation+=1
        self.incoming.put(('batch_activity',{'generation':self._activity_generation}))

    def note_participant_activity(self):
        self._require_active();self._activity_generation+=1
        self.incoming.put(('participant_activity',{'generation':self._activity_generation}))

    def record_batch(self,value):
        self._require_active()
        if value['operation']=='begin' and not value.get('continued'):self.note_batch_activity()
        self.incoming.put(('batch_capture',value))

    def make_tick_job(self,plan,mouse):
        if getattr(self,'recovering',False):return None
        if plan.get('kind')=='idle' and getattr(self,'_multi_processing',False):return None
        if plan.get('kind')=='idle':
            current=mouse.current_gesture_id
            if current is not None and mouse.is_active(current):return None
            return {'kind':'idle_tick','plan_fingerprint':plan['fingerprint'],'coordinate':wall_clock_ms()}
        gesture=plan['gesture_id'];info=mouse.capture_info(gesture)
        if info is None or info['phase']!='ongoing' or mouse.current_gesture_id!=gesture:return None
        last=info['last_point'];mouse.move(x=last[0],y=last[1],coordinate=wall_clock_ms())
        return {'kind':'interaction_tick','gesture_id':gesture,'plan_fingerprint':plan['fingerprint'],
            'point':mouse.capture_info(gesture)['last_point']}

    def complete_capture(self,request,mouse):
        # Called only by Tk. This marker orders the actual stationary observation
        # after all earlier UI pointer callbacks, before any later callbacks.
        gesture=request['gesture_id'];info=mouse.capture_info(gesture)
        point=None
        if info is not None and info['phase']=='ongoing':
            last=info['last_point'];mouse.move(x=last[0],y=last[1],coordinate=wall_clock_ms())
            point=mouse.capture_info(gesture)['last_point']
        self.incoming.put(('marker',{'id':request['id'],'gesture_id':gesture,'point':point}))

    def request_stop(self):
        if not self._stopping:self._stopping=True;self.incoming.put(('close',None))

    def close(self):
        self.request_stop();self.process.join(self.startup_timeout_seconds if self._deferred_startup and not self._activated else 10)

    def run_window(self,*,results,executor,mouse,is_closed,speech_generation):
        late_slots={}
        def execute_message(value,late=None):
            try:
                gesture=value['gesture_id'];generation=value.get('input_generation',0)
                cancelled=lambda:(is_closed() or (not value.get('batch_scope',False) and getattr(self,'_input_generation',0)!=generation)
                    or (gesture is not None and not mouse.is_active(gesture))
                    or value.get('activity_generation',0)!=getattr(self,'_activity_generation',0))
                contents=tuple(Content.from_record(x) for x in value['contents'])
                if value.get('execution_context') is not None:
                    observer=(lambda effect:self.incoming.put(('response',{'id':value['id'],'visible_effect':effect}))) if value.get('observe_visible') else None
                    executor.set_execution_context(value['action_id'],value['execution_context'],observer=observer)
                if 'composite' in value:
                    plan=CompositePlan.create(value['composite'])
                    if late is not None:
                        outcome=executor.execute_staged(value['action_id'],contents,plan=plan,late_speech=late,is_cancelled=cancelled)
                    else:outcome=executor.execute_composite(value['action_id'],contents,plan=plan,is_cancelled=cancelled)
                else:outcome=executor.execute_guarded(value['action_id'],contents,is_cancelled=cancelled)
                response={'outcome':outcome.to_record()}
            except ExecutionCancelled:response={'error':'cancelled'}
            except BaseException:response={'error':'uncertain'}
            finally:
                late_slots.pop(value['id'],None)
                if value.get('execution_context') is not None:executor.clear_execution_context(value['action_id'])
            self.incoming.put(('response',{'id':value['id'],**response}))
        try:
            while True:
                if is_closed():self.request_stop()
                try:kind,value=self.outgoing.get(timeout=.03)
                except queue.Empty:
                    if not self.process.is_alive():
                        if not is_closed():results.put(('actor_exited','后台进程已退出；请退出桌宠后重新运行START.cmd恢复原存档，未完成记录已保留。'))
                        break
                    continue
                if kind=='stopped':break
                if kind=='ingress_accepted':
                    self.last_ingress_receipt=value;continue
                if kind=='multi_delivery':self.last_multi_receipt=value;continue
                if kind=='multi_processing':
                    self._multi_processing=bool(value);results.put((kind,bool(value)));continue
                if kind=='phone_status':self.phone_status=value;results.put(('phone_status',value));continue
                if kind=='reading_error':results.put(('error',value));continue
                if kind=='recovery_required':self.recovery_pending=value;self.recovering=False;continue
                if kind=='ingress_recovered':
                    self.recovery_pending=0;self.recovering=False;continue
                if kind=='recovery_failed':
                    self.recovering=False;continue
                if kind=='execute':
                    if value.get('staged'):
                        late=Future();late_slots[value['id']]=late
                        threading.Thread(target=execute_message,args=(value,late),name='pet-bridge-playback',daemon=True).start()
                    else:execute_message(value)
                elif kind=='late_speech':
                    late=late_slots.get(value['id'])
                    # A terminated parent must never be recreated by a late result.
                    if late is not None and not late.done():
                        try:
                            if value.get('quiet') is not None:
                                from product_adapters.desktop_pet_v1.quiet_realization import QuietRealization,SpeechNotSelected
                                raise SpeechNotSelected(QuietRealization(**value['quiet']))
                            if value.get('failed'):raise RuntimeError('language preparation failed')
                            late.set_result(tuple(Content.from_record(c) for c in value['contents']) if value.get('expression_children_version')=='desktop-pet-expression-children-v1' else Content.from_record(value['content']))
                        except Exception as error:late.set_exception(error)
                elif kind=='capture_request':
                    if is_closed():self.incoming.put(('marker',{'id':value['id'],'gesture_id':value['gesture_id'],'point':None}))
                    else:results.put(('capture',value))
                elif kind=='typing':
                    if isinstance(value,dict) and value.get('input_generation')==getattr(self,'_input_generation',0):
                        results.put((kind,{'input_generation':value['input_generation'],'speech_generation':speech_generation()}))
                else:results.put((kind,value))
        finally:
            for late in tuple(late_slots.values()):
                if not late.done():late.set_exception(RuntimeError('UI connection closed'))
            self.request_stop();self.process.join(10)


from .execution_context import ExecutionContexts


class _Bridge(ExecutionContexts):
    def __init__(self,outgoing,responses,closed):
        self.outgoing=outgoing;self.responses=responses;self.closed=closed;self.gesture_id=None
        self.input_generation=0;self.received_generation=0;self.confirmed_effects=[];self.activity_generation=0;self.processing_activity=0;self.batch_scope=False;self.current_scene=None
        self.current_meal=None

    def is_cancelled(self):
        contact=getattr(self,'contact_is_active',None)
        return (self.closed.is_set() or (not self.batch_scope and self.received_generation!=self.input_generation)
            or self.activity_generation!=self.processing_activity
            or (self.gesture_id is not None and contact is not None and not contact(self.gesture_id)))

    def acknowledge_input(self,value):
        if not self.is_cancelled():
            self.outgoing.put(('input_feedback',{**value,'input_generation':self.input_generation,
                'activity_generation':self.processing_activity}))

    def rpc(self,kind,*,_late_speech=None,**fields):
        identity=uuid.uuid4().hex;self.outgoing.put((kind,{'id':identity,**fields}))
        if _late_speech is not None:
            def deliver(future):
                try:
                    result=future.result()
                    value={'expression_children_version':'desktop-pet-expression-children-v1','contents':[c.to_record() for c in result]} if isinstance(result,tuple) else {'content':result.to_record()}
                except BaseException as error:
                    from product_adapters.desktop_pet_v1.quiet_realization import SpeechNotSelected
                    value={'quiet':{'request_id':error.decision.request_id,'evidence':error.decision.evidence}} if isinstance(error,SpeechNotSelected) else {'failed':True}
                self.outgoing.put(('late_speech',{'id':identity,**value}))
            _late_speech.add_done_callback(deliver)
        while True:
            try:response=self.responses.get(timeout=.1)
            except queue.Empty:
                if self.closed.is_set():
                    if kind=='capture_request':return {'point':None}
                    raise ExecutionUncertain('UI connection closed before execution was confirmed')
                continue
            if response['id']!=identity:raise ExecutionUncertain('desktop response identity differs')
            if 'visible_effect' in response:
                observer=self.execution_observer(fields.get('action_id'))
                if observer is None:raise ExecutionUncertain('unexpected visible effect')
                observer(response['visible_effect']);continue
            return response

    def __call__(self,action_id,contents):return self.execute_guarded(action_id,contents)

    def execute_guarded(self,action_id,contents,*,is_cancelled=None):
        return self._execute(action_id,contents,is_cancelled=is_cancelled)

    def execute_composite(self,action_id,contents,*,plan,is_cancelled=None):
        return self._execute(action_id,contents,plan=plan,is_cancelled=is_cancelled)

    def execute_staged(self,action_id,contents,*,plan,late_speech,is_cancelled=None):
        return self._execute(action_id,contents,plan=plan,late_speech=late_speech,is_cancelled=is_cancelled)

    def _execute(self,action_id,contents,*,plan=None,late_speech=None,is_cancelled=None):
        if self.is_cancelled() or is_cancelled is not None and is_cancelled():
            raise ExecutionCancelled('new ingress or cancellation before UI request')
        extra={} if plan is None else {'composite':thaw_model_data(plan.children)}
        context=self.execution_context(action_id)
        if context is not None:extra['execution_context']=context
        if self.execution_observer(action_id) is not None:extra['observe_visible']=True
        if late_speech is not None:extra['staged']=True
        response=self.rpc('execute',action_id=action_id,contents=[x.to_record() for x in contents],
            gesture_id=self.gesture_id,input_generation=self.input_generation,activity_generation=self.processing_activity,batch_scope=self.batch_scope,
            _late_speech=late_speech,**extra)
        if response.get('error')=='cancelled':raise ExecutionCancelled('UI cancelled before any effect')
        if 'error' in response:raise ExecutionUncertain('UI effect lacks a confirmed outcome')
        self.confirmed_effects.append({'action_id':action_id,'confirmed_at_unix_ms':wall_clock_ms()})
        return ExecutionOutcome(**response['outcome'])


class _MirrorCapture(ContinuousMouseInputAdapter):
    def __init__(self,bridge,**kwargs):super().__init__(**kwargs);self.bridge=bridge

    def accept_marker(self,marker):
        # Reader calls this before it can append any later FIFO capture item.
        if marker['point'] is None:return {**marker,'boundary_id':None}
        with self._capture_lock:
            gesture=marker['gesture_id']
            if gesture!=self.current_gesture_id or not self.is_active(gesture):
                raise ValueError('capture marker differs from active gesture')
            x,y,at=marker['point'];self.move(x=x,y=y,coordinate=at)
            return {**marker,'boundary_id':self.freeze_boundary(gesture)}

    def observe(self,*,coordinate=None,gesture_id=None):
        gesture=gesture_id or self.current_gesture_id
        marker=self.bridge.rpc('capture_request',gesture_id=gesture)
        if marker['point'] is None:return None
        return self.observe_boundary(marker['boundary_id'])


def _actor_main(spec,incoming,outgoing):
    product=None;observer=None;idle=None;journal=None;phone=None;multi=None;closed=threading.Event();jobs=queue.Queue();responses=queue.Queue()
    bridge=_Bridge(outgoing,responses,closed)
    mouse=_MirrorCapture(bridge,recipient_ref=spec['definition']['owner_ref'],definitions=spec['mouse_definitions'])
    bridge.contact_is_active=lambda gesture:mouse.is_active(gesture)
    try:
        transport=ChatTransport(**spec['transport'])
        routes={'writer':replace(transport,model=spec['writer_model'])} if spec.get('writer_model') else {}
        model_package=None
        if spec.get('multi_workspace'):
            from .multi_package import select as select_multi_package
            model_package=select_multi_package(session_path=spec['session_path'],owner_ref=spec['definition']['owner_ref'],human_ref=spec['definition']['user_ref'])
        product=PetProduct.open(model_package=model_package,definition=PetDefinition.from_record(spec['definition']),session_path=spec['session_path'],
            gateway=ProviderGateway(transport,trace_dir=spec['trace_dir'],operation_transports=routes),
            private_thought=spec.get('private_thought',False),writer_temperature=spec['temperature'],numbered_memory=spec['numbered'],memory_links=spec['links'],
            writer_budget_chars=spec['budget'],expression_profile=spec['expression_profile'],expression_codes=spec.get('expression_codes',False),visual_capabilities=spec.get('visual_capabilities'),memory_recall_version=spec.get('memory_recall_version'))
        product.application.scene_snapshot=lambda:bridge.current_scene
        product.application.meal_snapshot=lambda:bridge.current_meal
        product.idle_behavior=spec.get('idle_behavior','speech')
        if spec.get('multi_workspace'):
            from .multi_actor import MultiPetActorLink
            multi=MultiPetActorLink(product,spec['multi_workspace']['root'],spec['multi_workspace']['alias'])
        if spec.get('checkpoint_storage_mode')=='delta-v1':
            product.application.store.enable_incremental(product.application.runtime.session)
        from .ingress_journal import IngressJournal,apply_capture,recover_ingress,complete_accepted_jobs
        journal=IngressJournal(spec['session_path']+'.ingress.jsonl',definition_fingerprint=product.application.definition.fingerprint,
            session_binding_fingerprint=product.application.runtime.session.session_binding.fingerprint,
            capture_configuration={key:spec[key] for key in ('mouse_definitions','observe','bounds','response_policy')})
        pending_jobs=journal.pending_jobs()
        # A prior host's wake/resume opportunity is not a user input fact.
        # Retire only a purely operational backlog, with no unresolved effect.
        if pending_jobs and all(row['value']['kind'] in {'idle_tick','resume'} for row in pending_jobs):
            try:product.application._require_ready()
            except Exception:pass  # Existing uncertain/pending effects still require explicit recovery.
            else:
                for row in pending_jobs:
                    journal.append('complete',{'job_sequence':row['sequence'],'outcome':'cancelled_host_loss'})
        from .ingress_journal import superseded_reply_ids
        product.reply_cancellations=lambda:superseded_reply_ids(journal,product)
        from .reading_progress import restore_host_readings
        recovery_readings=restore_host_readings(product)
        recovery_replies=product.pending_reply_ids()
        pending_input_ids={row['value'].get('event_id') for row in journal.pending_jobs() if row['value']['kind']=='input'}
        pending_reply_count=len(set(recovery_replies)-pending_input_ids)
        ingress_recovery_pending=bool(journal.pending_jobs() or journal.pending_batches())
        participant_recovery_pending=multi is not None and multi.requires_recovery()
        recovery_pending=bool(ingress_recovery_pending or recovery_replies or recovery_readings or participant_recovery_pending)
        capture_lock=threading.RLock();buffered_captures=[];buffered_batch_parts=[]
        buffering_capture=recovery_pending
        # Establish process loss as an operation boundary; never a fake release.
        journal.append('host_stop',{'reason':'new_process'})
        bridge.input_generation=bridge.received_generation=journal.generation
        def publish_plan(record):
            journal.append('plan',record)
            outgoing.put(('observation_plan',{'fingerprint':record['fingerprint'],
                'gesture_id':record['gesture_id'],'at_ms':record['target']['payload']['at_ms']}))
        def make_observer(capture):
            from .interaction_observer import InteractionObserver
            return InteractionObserver(product=product,mouse=capture,bounds_ms=spec['bounds'],
                response_policy=spec['response_policy'],on_plan=publish_plan)
        if spec['observe']:
            observer=make_observer(mouse)
        def publish_idle(record):
            outgoing.put(('observation_plan',{'kind':'idle','fingerprint':record['fingerprint'],
                'at_ms':record['target']['payload']['at_ms']}))
        if spec.get('opening_mode')=='shared-opening-v1':
            from .idle_opening import IdleOpeningHost
            idle=IdleOpeningHost(product=product,on_plan=publish_idle)
        def publish_batch_advice():
            from .mouse_batch import sampling_advice
            advice=sampling_advice(product.application,spec['bounds'])
            journal.append('batch',{'operation':'advice','advice':advice})
            outgoing.put(('batch_advice',advice))
            return advice
        from product_adapters.desktop_pet_v1.settled_presentation import snapshot as presentation_snapshot
        def publish_presentation():
            outgoing.put(('settled_presentation',presentation_snapshot(product.application)))
        product.on_settled_presentation=publish_presentation
        initial_batch_advice=None
        if spec.get('batch_quiet_ms') is not None:
            from .mouse_batch import sampling_advice
            initial_batch_advice=sampling_advice(product.application,spec['bounds'])
            journal.append('batch',{'operation':'advice','advice':initial_batch_advice})
        from model_packages.desktop_pet_v1 import memory_index_query,memory_time_window,memory_direct_readout
        from model_packages.desktop_pet_v1.semantic import DesktopPetConstrainedInteractionSemanticCodec,DesktopPetAtomicGroundedInteractionSemanticCodec
        interaction_codec=DesktopPetAtomicGroundedInteractionSemanticCodec if product.semantic.memory_recall_version else DesktopPetConstrainedInteractionSemanticCodec
        product.web_reader_binding=spec.get('web_reading')
        if spec.get('reading_sources') or spec.get('reading_searches'):
            from .phone_activity import PhoneActivityHost
            phone=PhoneActivityHost(product,sources=spec['reading_sources'],searches=spec.get('reading_searches',()),
                on_ready=lambda identity:jobs.put({'kind':'phone_ready','event_id':identity}),
                on_status=lambda state:outgoing.put(('phone_status',{**state,'input_generation':bridge.input_generation,'activity_generation':bridge.processing_activity})))
            product.application.reading_context_supplier=phone.context
            product.action_catalog=phone.catalog()
        interaction_contract=interaction_codec.request_version
        if product.semantic.memory_recall_version=='desktop-pet-property-address-recall-v8':
            from model_packages.desktop_pet_v1.address_semantics import AddressPropertyCodec
            interaction_contract=AddressPropertyCodec(DesktopPetConstrainedInteractionSemanticCodec()).request_version
        from model_packages.desktop_pet_v1.measurement_constraint import VERSION as calibration_version
        from product_adapters.desktop_pet_v1.prompt_delivery import SPEAKER_VERSION as SHARED_VERSION,SPEAKER_MARKER_VERSION as SHARED_MARKER_VERSION
        from product_adapters.desktop_pet_v1.projection_cache import VERSION as projection_reuse_version
        outgoing.put(('ready',{'provider_models':{'semantic':product.gateway.transport.model,'writer':product.gateway.transport_for('writer').model},'idle_behavior':product.idle_behavior,'checkpoint_storage_mode':('delta-v1' if product.application.store._incremental is not None else 'snapshot-v1'),'opening_mode':spec.get('opening_mode','legacy'),'backend_pid' :os.getpid(),'definition_fingerprint':product.application.definition.fingerprint,
            'restored_events':len(product.application.runtime.events),'writer_attempt_journal_enabled':True,
            'interaction_contracts':{'semantic':product.semantic.codec.request_version,'interaction_semantic':interaction_contract,'recall_model':product.semantic.memory_recall_version,'prompt_delivery':SHARED_MARKER_VERSION if product.semantic.memory_recall_version else SHARED_VERSION,'reference_frame_schema':2,'local_calibration':calibration_version,'projection_reuse':projection_reuse_version},
            'journal_storage_implementation':__import__('petlab_app.owned_journals',fromlist=['VERSION']).VERSION,
            'memory_query_implementation':{'source_validation_plan':memory_index_query.SOURCE_VALIDATION_PLAN,'time_window':None if product.semantic.memory_recall_version else memory_time_window.VERSION,'direct_readout':memory_direct_readout.VERSION},
            'settled_presentation':None if recovery_pending else presentation_snapshot(product.application),
            # A retained peer delivery blocks this Actor just like retained UI
            # ingress. The host must see that same recovery condition on restart.
            'input_generation':journal.generation,'pending_ingress_count':len(journal.pending_jobs())+len(journal.pending_batches())+pending_reply_count+len(recovery_readings)+int(participant_recovery_pending),
            'pending_participant_count':int(participant_recovery_pending),'pending_reading_count':len(recovery_readings),'batch_sampling_advice':initial_batch_advice,
            'local_retrieval':{'enabled':True,'status':product.rag_memory.provider.status,'model':product.rag_memory.provider.model},
            'interaction_observation_policy_version':('desktop-pet-interaction-observation-policy-v1' if spec['observe'] else None),
            'interaction_model_input_version':('desktop-pet-interaction-text-summary-v1' if spec['observe'] else None),
            'visual_capabilities_fingerprint':None if product.writer.visual_capabilities is None else product.writer.visual_capabilities['fingerprint'],
            'expression_profile_fingerprint':None if product.writer.expression_profile is None else model_data_fingerprint(product.writer.expression_profile)}))
        # Restoration may overlap main-thread asset construction. No capture,
        # progress plan or autonomous sampling starts until the actual renderer
        # has passed its checks and the host explicitly activates this process.
        if spec.get('defer_startup',False):
            startup_kind,startup_value=incoming.get()
            if startup_kind=='close':return
            if startup_kind!='startup_activate' or startup_value is not None:
                raise ValueError('desktop startup activation is invalid')
        if phone is not None and phone.state is not None:phone.on_status(phone.state)
        ingress_host_times={};diagnostic_run_id=uuid.uuid4().hex
        def read():
            try:
                while True:
                    kind,value=incoming.get()
                    if kind=='close':
                        if phone is not None:phone.close()
                        closed.set();jobs.put(None);return
                    if kind=='reading_resume':
                        jobs.put({'kind':'phone_resume','event_id':value['event_id']});continue
                    if kind=='scene_snapshot':
                        from model_packages.desktop_pet_v1.spatial import validate
                        if value is not None:
                            value=validate(value)
                            if value['owner_ref']!=product.application.owner_ref:raise ValueError('scene owner differs')
                        bridge.current_scene=value
                        continue
                    if kind=='meal_snapshot':
                        from model_packages.desktop_pet_v1.shared_meal import validate
                        if value is not None:
                            value=validate(value)
                            if product.application.owner_ref not in value['participants']:raise ValueError('meal owner differs')
                        bridge.current_meal=value
                        continue
                    if kind=='participant_activity':
                        if value['generation']!=bridge.activity_generation+1:raise ValueError('participant activity generation differs')
                        bridge.activity_generation=value['generation']
                        if phone is not None:phone.interrupt(reason='user_contact',cause_ref='participant-input:'+str(value['generation']))
                        continue
                    if kind=='batch_activity':
                        if phone is not None:phone.interrupt(reason='user_contact',cause_ref='batch-generation:'+str(value['generation']))
                        if value['generation']!=bridge.activity_generation+1:raise ValueError('batch activity generation differs')
                        bridge.activity_generation=value['generation'];continue
                    if kind=='batch_capture':
                        with capture_lock:
                            if value['operation'] not in {'part','begin'}:raise ValueError('batch capture operation invalid')
                            if buffering_capture:
                                row=journal.append('batch',value);buffered_batch_parts.append((row['sequence'],value))
                            else:
                                if value['operation']=='part':
                                    from .mouse_batch import apply_part
                                    apply_part(mouse,value)
                                journal.append('batch',value)
                        continue
                    if kind=='job':
                        if phone is not None and value['kind'] in {'input','web_read','gesture_begin','gesture_end'}:
                            phone.interrupt(reason='user_contact',cause_ref=value.get('event_id') or value.get('gesture_id') or 'input-generation:'+str(value.get('_input_generation')))
                        generation=value.get('_input_generation')
                        if isinstance(generation,bool) or not isinstance(generation,int) or generation!=bridge.received_generation+1:
                            raise ValueError('desktop input generation is not consecutive')
                        bridge.received_generation=generation
                        if value['kind']=='interaction_tick':
                            marker=mouse.accept_marker({'gesture_id':value['gesture_id'],'point':value['point']})
                            value['boundary_id']=marker['boundary_id']
                        from .mouse_batch import verify_batch_job
                        verify_batch_job(journal,value,temporal_runtime=product.application.runtime.temporal_runtime)
                        value['_activity_generation']=bridge.activity_generation
                        row=journal.append('job',{key:item for key,item in value.items() if key!='boundary_id'})
                        ingress_host_times[row['sequence']]=wall_clock_ms()
                        value['_journal_sequence']=row['sequence']
                        if 'contents' in value:value['contents']=tuple(Content.from_record(x) for x in value['contents'])
                        outgoing.put(('ingress_accepted',{'sequence':row['sequence'],'generation':generation,'kind':value['kind']}))
                        jobs.put(value)
                    elif kind=='response':responses.put(value)
                    elif kind=='marker':
                        marker=mouse.accept_marker(value);journal.append('marker',value);responses.put(marker)
                    elif kind=='capture':
                        if phone is not None and value['operation']=='press':phone.interrupt(reason='user_contact',cause_ref=value['gesture_id'])
                        with capture_lock:
                            row=journal.append('capture',value,durable=False)
                            if buffering_capture:buffered_captures.append((row['sequence'],value))
                            else:apply_capture(mouse,value)
            except BaseException as error:
                outgoing.put(('job_error','desktop input bridge failed: '+type(error).__name__));closed.set();jobs.put(None)
        threading.Thread(target=read,name='pet-capture-reader',daemon=True).start()
        if idle is not None and not recovery_pending:
            from .idle_opening import journal as idle_journal
            data=idle_journal(product.application)
            if data['plan'] is not None or data['pending_tick'] is not None:
                product.application._require_ready()
                idle.cancel('host_loss')
            idle.arm()
            # arm has already accepted wall-clock progress. Publish that exact
            # read-only view now, rather than keeping the pre-arm ready image.
            publish_presentation()
        if spec.get('batch_quiet_ms') is not None and not recovery_pending:publish_batch_advice()
        while not closed.is_set():
            # A delivered source already waiting needs no extra polling sleep.
            # Queued user work still wins; idle discovery keeps its old cadence.
            peer_ready=(multi is not None and not recovery_pending
                and mouse.current_gesture_id is None and not journal.pending_batches()
                and multi.pending_count())
            try:job=jobs.get(timeout=0 if peer_ready else .1 if multi is not None else None)
            except queue.Empty:
                current=mouse.current_gesture_id
                held=current is not None and mouse.is_active(current)
                if multi is not None and not recovery_pending and not held and not journal.pending_batches() and multi.pending_count():
                    outgoing.put(('multi_processing',True))
                    try:
                        bridge.input_generation=bridge.received_generation
                        bridge.processing_activity=bridge.activity_generation;bridge.gesture_id=None
                        if multi.pending_count() and idle is not None:idle.cancel('participant_delivery')
                        changed=multi.pump(executor=bridge,is_cancelled=bridge.is_cancelled,
                            on_reply_ready=lambda:outgoing.put(('typing',{'input_generation':bridge.input_generation})))
                        if changed:
                            publish_presentation()
                            if idle is not None:idle.arm()
                            outgoing.put(('multi_delivery',multi.last_receipt))
                    except Exception as error:
                        recovery_pending=True;outgoing.put(('recovery_required',1))
                        outgoing.put(('error','多角色投递未完成：'+type(error).__name__))
                    finally:outgoing.put(('multi_processing',False))
                continue
            if job is None or closed.is_set():break
            if job['kind']=='phone_resume':
                if phone is not None:
                    phone.resume_reading(job['event_id'])
                    product.action_catalog=phone.catalog()
                continue
            if job['kind']=='phone_ready':
                try:
                    if phone is not None:
                        delivered=phone.deliver(job['event_id'],coordinate=wall_clock_ms())
                        product.action_catalog=phone.catalog()
                        if delivered is not None:
                            if idle is not None:idle.cancel('external_activity')
                            publish_presentation()
                            if idle is not None and not recovery_pending:idle.arm()
                except Exception as error:
                    recovery_readings=restore_host_readings(product)
                    recovery_pending=bool(recovery_readings)
                    if recovery_pending:
                        buffering_capture=True
                        outgoing.put(('recovery_required',len(recovery_readings)))
                    outgoing.put(('reading_error','阅读处理未完成：'+type(error).__name__))
                continue
            bridge.input_generation=job['_input_generation'];bridge.processing_activity=job.get('_activity_generation',bridge.activity_generation)
            bridge.batch_scope=job['kind']=='input' and bool(job.get('batch_id'))
            from model_packages.desktop_pet_v1.mouse_batch import live_gesture_id
            bridge.gesture_id=live_gesture_id(job.get('contents',())) if bridge.batch_scope else None
            if recovery_pending and job['kind']!='resume':
                outgoing.put(('job_error','恢复尚未完成，本次输入已保留，请继续恢复。'));continue
            host_started_ms=wall_clock_ms();host_started=time.perf_counter();host_status='error';host_error=None;bridge.confirmed_effects=[]
            product.semantic.gateway.last_failure=None
            product.semantic.gateway.diagnostic_job={'run_id':diagnostic_run_id,'job_sequence':job['_journal_sequence']}
            product.semantic.gateway.diagnostic_invocations=[]
            try:
                if idle is not None and job['kind']!='idle_tick' and idle.target_coordinate is not None:
                    idle.cancel('external_activity')
                if job['kind']=='gesture_begin':observer.start(job['gesture_id']);kind='armed'
                elif job['kind']=='idle_tick':
                    from .idle_opening import journal as idle_journal
                    plan=None if idle is None else idle_journal(product.application)['plan']
                    current=mouse.current_gesture_id
                    held=current is not None and mouse.is_active(current)
                    if (plan is None or plan['fingerprint']!=job['plan_fingerprint'] or held or journal.pending_batches() or bridge.is_cancelled()):
                        tick_result={'observed':False,'reason':'superseded_plan'}
                    else:
                        before_events=len(product.application.runtime.events)
                        if phone is not None:product.action_catalog=phone.catalog()
                        tick_result=idle.sample(coordinate=job['coordinate'],executor=bridge,is_cancelled=bridge.is_cancelled,
                            on_reply_ready=lambda:outgoing.put(('typing',{'input_generation':bridge.input_generation})))
                        if phone is not None and not bridge.is_cancelled():
                            phone.consume_result(tick_result.get('tick_id'))
                            for event in product.application.runtime.events[before_events:]:
                                if event.event_kind=='npc_output':
                                    if any(c.kind=='action' and c.semantic_key!='read_web' for c in event.contents):
                                        phone.interrupt(reason='source_expired',cause_ref=event.event_id)
                                    phone.start(event.event_id)
                    kind='tick_done' 
                elif job['kind']=='gesture_end':
                    finish_result=observer.finish(job['gesture_id'],executor=bridge,is_cancelled=bridge.is_cancelled,
                        response_policy=job.get('release_response_policy',
                            'eligible' if job.get('opening_mode')=='shared-opening-v1' else None));kind='done'
                elif job['kind']=='interaction_tick':
                    bridge.gesture_id=job['gesture_id']
                    try:
                        tick_result=observer.sample_frozen(boundary_id=job['boundary_id'],plan_fingerprint=job['plan_fingerprint'],
                            executor=bridge,is_cancelled=bridge.is_cancelled,on_reply_ready=lambda:outgoing.put(('typing',{'input_generation':bridge.input_generation})))
                    finally:bridge.gesture_id=None
                    kind='tick_done'
                elif job['kind']=='web_read':
                    product.read_page(event_id=job['event_id'],url=job['url'],executor=bridge,is_cancelled=bridge.is_cancelled);kind='done'
                elif job['kind']=='resume':
                    if multi is not None and multi.requires_recovery():
                        multi.resume(executor=bridge,is_cancelled=bridge.is_cancelled)
                    if recovery_pending:
                        from .ingress_journal import retained_reply_ids
                        recovery_prior_sources=set(product.application._product()['sources'])
                        recovery_jobs=[row['value'] for row in journal.pending_jobs() if row['sequence']<=job['_journal_sequence']]
                        recovery_jobs.extend({'kind':'input','event_id':identity} for identity in recovery_replies)
                        recovery_jobs.extend({'kind':'input','event_id':'input:'+batch}
                            for batch in journal.pending_batches(through_sequence=job['_journal_sequence']))
                        restored_mouse=_MirrorCapture(bridge,recipient_ref=spec['definition']['owner_ref'],definitions=spec['mouse_definitions'])
                        restored_observer=make_observer(restored_mouse) if spec['observe'] else None
                        # Finish an already received fact before unreceived handoffs;
                        # no retained interval grants replay of physical output.
                        if recovery_readings:
                            product.resume(executor=None,is_cancelled=lambda:True)
                            for reading in recovery_readings:
                                if closed.is_set():raise InterruptedError('host closed; reading retained')
                                reading.recover(executor=None)
                            recovery_readings.clear()
                        recover_ingress(journal,product=product,mouse=restored_mouse,observer=restored_observer,
                            current_sequence=job['_journal_sequence'],is_stopping=closed.is_set)
                        if idle is not None:idle.cancel('host_loss')
                        with capture_lock:
                            # Recovery consumes only facts preceding this resume job.
                            # Later pointer callbacks remain real, ordered live input.
                            mouse=restored_mouse;observer=restored_observer
                            operations=sorted([(n,'capture',v) for n,v in buffered_captures]+[(n,'batch',v) for n,v in buffered_batch_parts])
                            for sequence,operation,capture in operations:
                                if sequence<=job['_journal_sequence']:continue
                                if operation=='capture':apply_capture(mouse,capture)
                                elif capture['operation']=='part':
                                    from .mouse_batch import apply_part
                                    apply_part(mouse,capture)
                            buffered_captures.clear();buffered_batch_parts.clear();buffering_capture=False;recovery_pending=False
                        outgoing.put(('ingress_recovered',True))
                        # This branch exists only for the user's explicit resume job.
                        retained=retained_reply_ids(product,requested_jobs=recovery_jobs,prior_source_ids=recovery_prior_sources)
                        if retained and not bridge.is_cancelled():
                            outgoing.put(('typing',{'input_generation':bridge.input_generation}))
                            product._reply(retained[0],executor=bridge,completion_coordinate=wall_clock_ms,
                                is_cancelled=bridge.is_cancelled)
                    else:
                        from .idle_opening import journal as idle_journal
                        if idle is not None and idle_journal(product.application)['pending_tick'] is not None:
                            idle.resume(executor=bridge,is_cancelled=bridge.is_cancelled)
                        else:
                            (observer.resume(executor=bridge,is_cancelled=bridge.is_cancelled,on_reply_ready=lambda:outgoing.put(('typing',{'input_generation':bridge.input_generation})))
                                if observer is not None else product.resume(executor=bridge,is_cancelled=bridge.is_cancelled))
                        complete_accepted_jobs(journal,product,current_sequence=job['_journal_sequence'])
                    kind='done'
                else:
                    product.interact(event_id=job['event_id'],contents=job['contents'],coordinate=job['coordinate'],
                        admission_coordinate=wall_clock_ms() if job.get('batch_id') or job['coordinate']<product.application.runtime.session.position.position.coordinate else None,
                        admission_version=ADMISSION_VERSION if job.get('batch_id') else POINT_ADMISSION_VERSION,
                        response_policy=job['response_policy'],executor=bridge,is_cancelled=bridge.is_cancelled,
                        on_input_settled=publish_batch_advice if spec.get('batch_quiet_ms') is not None else lambda:None);kind='done' 
                if not (job['kind']=='gesture_end' and isinstance(finish_result,dict) and finish_result.get('deferred')):
                    journal.complete(job['_journal_sequence'])
                # A new interaction handles only its own work. Unanswered
                # historical sources remain formal pending work; consume them
                # only through the explicit resume branch above, never by
                # draining them between pointer jobs.
                if idle is not None and not recovery_pending and not bridge.is_cancelled() and observer.pending is None:
                    current=mouse.current_gesture_id
                    if not (current is not None and mouse.is_active(current)) and not journal.pending_batches():
                        # arm reconciles a new formal source and preserves an unchanged plan.
                        idle.arm()
                if multi is not None and not recovery_pending:multi.flush_outputs()
                if not recovery_pending:publish_presentation()
                if spec.get('batch_quiet_ms') is not None and not recovery_pending:publish_batch_advice()
                outgoing.put((kind,tick_result if kind=='tick_done' else True))
                host_status=kind
            except Exception as error:
                # Persist code locations only; never provider text, keys or locals.
                import traceback
                host_error={'type':type(error).__name__,'frames':[
                    {'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
                    for frame in traceback.extract_tb(error.__traceback__)]}
                if recovery_pending:outgoing.put(('recovery_failed',None))
                from .provider_diagnostics import format_product_error
                outgoing.put(('job_error',format_product_error(error,product.semantic.gateway)))
            finally:
                # Operational timing only; this log never participates in model state.
                row={'version':'petlab-host-job-timing-v1','job_sequence':job['_journal_sequence'],
                    'kind':job['kind'],'input_generation':job['_input_generation'],
                    'received_at_unix_ms':ingress_host_times.pop(job['_journal_sequence'],None),
                    'started_at_unix_ms':host_started_ms,'finished_at_unix_ms':wall_clock_ms(),
                    'processing_ms':round((time.perf_counter()-host_started)*1000,3),
                    'status':host_status,'error':host_error if host_status=='error' else None,'newer_ingress_received':bridge.is_cancelled(),
                    'confirmed_effects':bridge.confirmed_effects}
                try:
                    from .response_timing import observe_job
                    row.update(observe_job(product.application,product.semantic.gateway,job,bridge.confirmed_effects))
                except Exception as error:row['diagnostic_error']=type(error).__name__
                finally:
                    product.semantic.gateway.diagnostic_job=None;product.semantic.gateway.diagnostic_invocations=[]
                try:
                    with (Path(spec['trace_dir'])/'host-jobs.jsonl').open('a',encoding='utf-8') as trace:
                        trace.write(json.dumps(row,separators=(',',':'))+'\n')
                except OSError:pass
    except BaseException as error:outgoing.put(('startup_error',type(error).__name__))
    finally:
        if phone is not None:phone.close()
        if observer is not None:observer.stop()
        if product is not None:product.close()
        if journal is not None:
            journal.append('host_stop',{'reason':'closed'});journal.close()
        outgoing.put(('stopped',None))
