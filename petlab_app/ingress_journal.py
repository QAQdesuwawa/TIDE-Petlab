"""Host-only FIFO evidence. Never a semantic result or a committed event."""
from pathlib import Path
import json,os,threading,time
from .session import ADMISSION_VERSION,POINT_ADMISSION_VERSION
from framework.model_api import model_data_fingerprint
from product_adapters.desktop_pet_v1.temporal_measurement import capture_position


class IngressJournal:
    KINDS=frozenset({'capture','job','marker','plan','complete','host_stop','batch'})
    def __init__(self,path,*,definition_fingerprint,session_binding_fingerprint,capture_configuration=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self._lock=threading.RLock();self.rows=[]
        self.header={'version':'desktop-pet-ingress-v1','definition_fingerprint':definition_fingerprint,
            'session_binding_fingerprint':session_binding_fingerprint}
        if capture_configuration is not None:self.header['capture_configuration']=capture_configuration
        self.header=json.loads(json.dumps(self.header,ensure_ascii=False))
        self._previous=model_data_fingerprint(self.header)
        if self.path.exists():
            data=self.path.read_bytes();end=data.rfind(b'\n')+1
            if end==0:raise ValueError('ingress journal lacks a complete binding header')
            lines=data[:end].splitlines()
            if json.loads(lines[0])!=self.header:raise ValueError('ingress journal binding differs')
            for wire in lines[1:]:
                row=json.loads(wire)
                if (isinstance(row.get('sequence'),bool) or row.get('sequence')!=len(self.rows)+1
                        or row.get('kind') not in self.KINDS or not isinstance(row.get('value'),dict) or row.get('previous')!=self._previous
                        or model_data_fingerprint({k:v for k,v in row.items() if k!='fingerprint'})!=row.get('fingerprint')):
                    raise ValueError('ingress journal integrity differs')
                self.rows.append(row);self._previous=row['fingerprint']
            self.pending_jobs()  # Verify completions/generations before modifying a partial tail.
            if end!=len(data):
                with self.path.open('r+b') as stream:stream.truncate(end);stream.flush();os.fsync(stream.fileno())
        else:
            with self.path.open('xb') as stream:
                stream.write(self._wire(self.header));stream.flush();os.fsync(stream.fileno())
        self._stream=self.path.open('ab')

    @staticmethod
    def _wire(value):return (json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')

    def append(self,kind,value,*,durable=True):
        if kind not in self.KINDS or not isinstance(value,dict):raise ValueError('unknown ingress evidence kind or value')
        with self._lock:
            value=json.loads(json.dumps(value,ensure_ascii=False))  # Independent of mutable reader arguments.
            row={'sequence':len(self.rows)+1,'previous':self._previous,'kind':kind,'value':value}
            row['fingerprint']=model_data_fingerprint(row)
            self._stream.write(self._wire(row));self._stream.flush()
            if durable:os.fsync(self._stream.fileno())
            self.rows.append(row);self._previous=row['fingerprint'];return row

    def pending_jobs(self):
        with self._lock:
            jobs={};completed=set();generation=0
            for row in self.rows:
                if row['kind']=='job':
                    actual=row['value'].get('_input_generation')
                    if isinstance(actual,bool) or not isinstance(actual,int) or actual!=generation+1:
                        raise ValueError('ingress job generation differs')
                    generation=actual;jobs[row['sequence']]=row
                elif row['kind']=='complete':
                    sequence=row['value'].get('job_sequence')
                    if isinstance(sequence,bool) or sequence not in jobs or sequence in completed:
                        raise ValueError('ingress completion source differs')
                    completed.add(sequence)
            return tuple(row for sequence,row in jobs.items() if sequence not in completed)

    def pending_batches(self, *, through_sequence=None):
        with self._lock:
            batches={};sealed=set()
            for row in self.rows:
                if through_sequence is not None and row['sequence']>through_sequence:break
                value=row['value']
                if row['kind']=='batch':
                    operation=value.get('operation')
                    if operation=='advice':continue
                    batch=value.get('batch_id')
                    if not isinstance(batch,str):raise ValueError('batch evidence identity invalid')
                    if operation=='begin':batches.setdefault(batch,[]).append(value)
                    elif operation=='part':
                        if batch not in batches:raise ValueError('batch component lacks its start')
                        batches[batch].append(value)
                    elif operation in {'recovered','abandon'}:sealed.add(batch)
                    else:raise ValueError('batch evidence operation invalid')
                elif row['kind']=='job' and value.get('batch_id'):sealed.add(value['batch_id'])
            return {batch:rows for batch,rows in batches.items() if batch not in sealed}

    @property
    def generation(self):
        with self._lock:
            return max((row['value']['_input_generation'] for row in self.rows if row['kind']=='job'),default=0)

    def complete(self,sequence):
        with self._lock:
            if sequence not in {row['sequence'] for row in self.pending_jobs()}:raise ValueError('ingress job is not pending')
            return self.append('complete',{'job_sequence':sequence})

    def close(self):
        with self._lock:
            if not self._stream.closed:self._stream.flush();os.fsync(self._stream.fileno());self._stream.close()


def apply_capture(mouse,value):
    operation=value['operation'];gesture=value['gesture_id'];point=value['point']
    if operation=='press':
        x,y,at=point
        return mouse.press(x=x,y=y,coordinate=at,gesture_id=gesture,
            source_kind=value['source_kind'],body_target=value.get('body_target'),drag_slop_px=value.get('drag_slop_px',(0,0)))
    if gesture!=mouse.current_gesture_id:raise ValueError('ingress capture belongs to a different gesture')
    if operation=='cancel':return mouse.cancel()
    if operation not in {'move','release'}:raise ValueError('unknown ingress capture operation')
    x,y,at=point
    return (mouse.release if operation=='release' else mouse.move)(x=x,y=y,coordinate=at)


def rebuild_capture(journal,mouse,*,through_sequence=None):
    """Rebuild factual capture only; no phantom held/released state on restart."""
    boundaries={};plans={}
    for row in journal.rows:
        if through_sequence is not None and row['sequence']>through_sequence:break
        value=row['value']
        if row['kind']=='capture':apply_capture(mouse,value)
        elif row['kind']=='job' and value['kind']=='interaction_tick':
            marker=mouse.accept_marker({'gesture_id':value['gesture_id'],'point':value['point']})
            boundaries[row['sequence']]=marker['boundary_id']
        elif row['kind']=='marker':
            marker=mouse.accept_marker(value);boundaries[row['sequence']]=marker['boundary_id']
        elif row['kind']=='plan':plans[value['fingerprint']]=value
        elif row['kind']=='batch' and value['operation']=='part':
            from .mouse_batch import apply_part
            apply_part(mouse,value)
        elif row['kind']=='host_stop':mouse.cancel()
    # No physical gesture survives process loss. This does not invent release.
    mouse.cancel()
    return boundaries,plans


def restore_accepted_cursors(product,mouse,boundaries):
    """Original formal sources, rather than completion flags, own consumption."""
    app=product.application;accepted={}
    # One validated history snapshot for this locked recovery pass.
    history=app.runtime._history()
    events=sorted(app.runtime.events,key=lambda event:history.fact(event.event_id).accepted_runtime_ordinal)
    for event in events:
        if event.event_kind!='peer_input' or len(event.contents)!=1:continue
        content=event.contents[0];params=content.parameters;gesture=params.get('gesture_id')
        if content.kind!='interaction' or gesture not in mouse._captures:continue
        phase=params.get('phase')
        if phase=='started':observation=mouse.observe_started(gesture)
        elif phase=='ended':observation=mouse.observe_finished(gesture)
        elif phase=='ongoing':
            candidates=[identity for identity in boundaries.values() if identity in mouse._boundaries
                and mouse.boundary_info(identity)=={'gesture_id':gesture,'end_point':tuple(params['trajectory'][-1])}]
            if not candidates:raise ValueError('accepted ongoing source lacks its persisted boundary')
            observation=mouse.observe_boundary(candidates[0])
        else:continue
        app._verify_source(event)
        if (observation is None or observation.content.to_record()!=content.to_record()
                or observation.end_point[2]!=capture_position(app,event).coordinate):
            raise ValueError('persisted capture differs from accepted interaction source')
        mouse.accept(observation);accepted[(gesture,phase,observation.end_point[2])]=(event.event_id,observation)
    return accepted


def recover_ingress(journal,*,product,mouse,observer,current_sequence,is_stopping=lambda:False):
    """Explicit continuation only. Retained captures never grant live output."""
    if is_stopping():raise InterruptedError("host closed; recovery sources retained")
    # First settle the original pending source/actual effect, never produce speech.
    product.resume(executor=None,is_cancelled=lambda:True)
    boundaries,plans=rebuild_capture(journal,mouse,through_sequence=current_sequence)
    accepted=restore_accepted_cursors(product,mouse,boundaries)
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from model_packages.desktop_pet_v1.events import Content
    app=product.application
    for row in journal.pending_jobs():
        if row['sequence']>current_sequence:continue
        if is_stopping():raise InterruptedError('host closed; remaining recovery sources retained')
        job=row['value'];kind=job['kind'];gesture=job.get('gesture_id')
        if kind=='resume':
            if row['sequence']!=current_sequence:journal.complete(row['sequence'])
            continue
        if kind=='idle_tick':
            # Host loss ends this sampling opportunity. Never replay an old autonomous send.
            journal.complete(row['sequence']);continue
        if kind=='web_read':
            from .web_reading import verify_job
            if job['event_id'] not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
                product.read_page(event_id=job['event_id'],url=job['url'],executor=None,respond=False,retry_failed=True,is_cancelled=is_stopping)
            verify_job(product,job)
        elif kind=='input':
            from .mouse_batch import verify_batch_job
            verify_batch_job(journal,job,temporal_runtime=app.runtime.temporal_runtime)
            if job['event_id'] not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
                product.interact(event_id=job['event_id'],contents=tuple(Content.from_record(x) for x in job['contents']),
                    coordinate=job['coordinate'],response_policy=job['response_policy'],executor=None,
                    admission_coordinate=time.time_ns()//1_000_000 if job.get('batch_id') or job['coordinate']<app.runtime.session.position.position.coordinate else None,
                    admission_version=ADMISSION_VERSION if job.get('batch_id') else POINT_ADMISSION_VERSION,
                    is_cancelled=lambda:True,retry_failed=True)
            else:
                event=app.runtime.memory_instance(job['event_id']).event;app._verify_source(event)
                if ([x.to_record() for x in event.contents]!=job['contents'] or capture_position(app,event).coordinate!=job['coordinate']
                        or app._product()['sources'][job['event_id']]['evidence']['response_policy']!=job['response_policy']):
                    raise ValueError('persisted job differs from its accepted input')
        elif kind=='gesture_begin':
            point=mouse.capture_info(gesture)['start_point']
            if (gesture,'started',point[2]) not in accepted:observer.start(gesture,retry_failed=True)
        elif kind=='interaction_tick':
            saved=accepted.get((gesture,'ongoing',job['point'][2]))
            if saved is None:
                plan=plans.get(job['plan_fingerprint'])
                if plan is None:raise ValueError('persisted Tick lacks its original published plan')
                published=[item for item in plans.values() if item['gesture_id']==gesture]
                if published[-1]['fingerprint']!=job['plan_fingerprint']:
                    mouse.discard_boundary(boundaries[row['sequence']]);journal.complete(row['sequence']);continue
                observer.restore_plan(plan)
                observer.sample_frozen(boundary_id=boundaries[row['sequence']],plan_fingerprint=job['plan_fingerprint'],
                    executor=None,is_cancelled=lambda:True)
            elif saved[0] not in app.runtime.session.profile.get('desktop_pet_interaction_observations_v1',{}).get('ticks',{}):
                observer.restore_plan(plans[job['plan_fingerprint']])
                temporal=app.runtime.temporal_runtime
                observer.pending={'snapshot':saved[1],'event_id':saved[0],'accepted':True,'plan':observer.plan,
                    'due':temporal.prove_observation_due(observer.plan['target'],temporal.normalize_position(saved[1].end_point[2]))}
                observer.plan=None
                from .product import wall_clock_ms
                observer._run_pending(None,wall_clock_ms,lambda:True,lambda:None)
        elif kind=='gesture_end':
            point=mouse.capture_info(gesture)['last_point']
            if (gesture,'ended',point[2]) not in accepted:
                observer.finish(gesture,executor=None,is_cancelled=lambda:True,retry_failed=True,
                    response_policy=job.get('release_response_policy',
                            'eligible' if job.get('opening_mode')=='shared-opening-v1' else None))
        else:raise ValueError('unknown persisted ingress job')
        journal.complete(row['sequence'])
    from .mouse_batch import recover_batches
    recover_batches(journal,product,through_sequence=current_sequence,is_stopping=is_stopping)



def complete_accepted_jobs(journal,product,*,current_sequence):
    """A successful live resume also closes its original operational jobs."""
    app=product.application;events=app.runtime.events
    for row in journal.pending_jobs():
        if row['sequence']==current_sequence:continue
        job=row['value'];kind=job['kind'];event=None
        if kind=='web_read':
            event=next((item for item in events if item.event_id==job['event_id']),None)
            if event is not None:
                from .web_reading import verify_job
                verify_job(product,job,event)
        elif kind=='input':
            event=next((item for item in events if item.event_id==job['event_id']),None)
            if event is not None and ([x.to_record() for x in event.contents]!=job['contents']
                    or capture_position(app,event).coordinate!=job['coordinate']
                    or app._product()['sources'][job['event_id']]['evidence']['response_policy']!=job['response_policy']):
                raise ValueError('accepted input job differs')
        elif kind in {'gesture_begin','gesture_end','interaction_tick'}:
            phase={'gesture_begin':'started','gesture_end':'ended','interaction_tick':'ongoing'}[kind]
            event=next((item for item in events if len(item.contents)==1
                and item.contents[0].parameters.get('gesture_id')==job['gesture_id']
                and item.contents[0].parameters.get('phase')==phase
                and (kind!='interaction_tick' or item.position.coordinate==job['point'][2])),None)
            if kind=='interaction_tick' and event is not None:
                tick=app.runtime.session.profile.get('desktop_pet_interaction_observations_v1',{}).get('ticks',{}).get(event.event_id)
                if tick is None or tick['plan']['fingerprint']!=job['plan_fingerprint']:continue
        if event is not None:app._verify_source(event);journal.complete(row['sequence'])


def retained_reply_ids(product,*,requested_jobs,prior_source_ids):
    """Reply work for this recovery operation, not every unanswered old input."""
    direct={job.get('event_id') for job in requested_jobs if job.get('kind') in ('input','web_read')}
    gestures={job.get('gesture_id') for job in requested_jobs if job.get('kind')=='gesture_end'}
    selected=[]
    for identity in product.pending_reply_ids():
        event=product.application.runtime.memory_instance(identity).event
        ended=any(content.kind=='interaction' and content.parameters.get('phase')=='ended'
            and content.parameters.get('gesture_id') in gestures for content in event.contents)
        if identity not in prior_source_ids or identity in direct or ended:selected.append(identity)
    return tuple(selected)



def superseded_reply_ids(journal,product):
    """Read existing host order: user input during in-flight work ends that reply.

    Completion without a later user operation does not mean cancellation.
    Host loss, network failure, idle ticks and later already-completed work do
    not supply a user cancellation cause. This never deletes an input fact.
    """
    app=product.application
    if (journal.header['definition_fingerprint']!=app.definition.fingerprint
        or journal.header['session_binding_fingerprint']!=app.runtime.session.session_binding.fingerprint):
        raise ValueError('reply cancellation journal belongs to another role')
    with journal._lock:rows=tuple(journal.rows)
    jobs={};last_input=last_nonbatch=last_contact=last_legacy_contact=0;cancelled=[]
    sources=app._product()['sources']
    from model_packages.desktop_pet_v1.events import PetEvent
    for row in rows:
        kind=row['kind'];value=row['value'];sequence=row['sequence']
        if kind=='job':
            if value['kind'] in ('input','web_read'):
                jobs[sequence]=value;last_input=sequence
                if not value.get('batch_id'):last_nonbatch=sequence
            elif value['kind']=='gesture_begin':last_legacy_contact=sequence
        elif kind=='batch' and value.get('operation')=='begin' and not value.get('continued'):
            last_contact=sequence
        elif kind=='complete':
            original=value['job_sequence'];job=jobs.pop(original,None)
            if job is None:continue
            cause=max(last_nonbatch,last_contact,0 if job.get('batch_id') else max(last_input,last_legacy_contact))
            if cause<=original:continue
            identity=job['event_id'];source=sources.get(identity)
            if source is None:continue
            event=PetEvent.from_record(source['event'])
            if job['kind']=='web_read':
                from .web_reading import verify_job
                verify_job(product,job,event)
                if identity not in cancelled:cancelled.append(identity)
                continue
            if (source['origin']!='input' or event.event_id!=identity
                or [x.to_record() for x in event.contents]!=job['contents']
                or capture_position(app,event).coordinate!=job['coordinate']
                or source['evidence'].get('response_policy')!=job['response_policy']):
                raise ValueError('superseded reply differs from received input source')
            if identity not in cancelled:cancelled.append(identity)
    return tuple(cancelled)
