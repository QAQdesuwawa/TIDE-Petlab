"""UI capture batching only. No provider, root state or send qualification."""
import uuid
from model_packages.desktop_pet_v1.mouse_batch import build_batch

class MouseBatchBuffer:
    def __init__(self,*,quiet_ms,interval_ms):
        if any(type(v) is not int or v<=0 for v in (quiet_ms,interval_ms)):raise ValueError('batch timing invalid')
        self.quiet_ms=quiet_ms;self.interval_ms=interval_ms;self.reset()
    def begin(self,gesture_id,coordinate):
        if self.batch_id is None:
            self.batch_id='batch:'+uuid.uuid4().hex;self.origin=coordinate;self.tick_at=coordinate+self.interval_ms
            self.capture_tick_at=self.tick_at
        elif self.capture_tick_at is None:
            self.capture_tick_at=self.tick_at if coordinate<self.tick_at else coordinate+self.interval_ms
        if self.deferred_reason=='quiet':self.deferred_reason=None
        self.held=True;self.quiet_at=None
        return self.batch_id
    def release(self,coordinate):self.held=False;self.quiet_at=coordinate+self.quiet_ms
    @property
    def deadline(self):
        values=[v for v in (self.capture_tick_at,self.quiet_at) if v is not None]
        return min(values) if values else None
    def due(self,coordinate):
        if self.deadline is None or coordinate<self.deadline:return None
        if not self.held and self.quiet_at is not None and (self.capture_tick_at is None or self.quiet_at<self.capture_tick_at):return 'quiet'
        if self.capture_tick_at is not None and coordinate>=self.capture_tick_at:return 'tick'
        if not self.held and self.quiet_at is not None and coordinate>=self.quiet_at:return 'quiet'
        return None
    def defer(self,reason,coordinate):
        # Keep the original published Tick immutable. Further capture boundaries
        # partition only local evidence; they do not enqueue model requests.
        if reason not in {'tick','quiet'}:raise ValueError('only due observations can be deferred')
        if reason=='tick' and coordinate<self.tick_at:raise ValueError('deferred Tick is early')
        if reason=='quiet' and (self.held or self.quiet_at is None or coordinate<self.quiet_at):raise ValueError('deferred quiet boundary is early')
        self.deferred_reason=reason
        self.capture_tick_at=coordinate+self.interval_ms if self.held else None
        if self.quiet_at is not None and coordinate>=self.quiet_at:self.quiet_at=None
    def ready_reason(self,coordinate):
        if self.deferred_reason=='tick' and coordinate>=self.tick_at:return 'tick'
        if self.deferred_reason=='quiet' and not self.held:return 'quiet'
        return None
    def reset(self):
        self.batch_id=None;self.parts=[];self.held=False;self.quiet_at=None;self.tick_at=None;self.origin=None
        self.capture_tick_at=None;self.deferred_reason=None


def sampling_advice(app,bounds_ms):
    """Read the accepted state, invoke original projection/plan stages; never commit."""
    from framework.model_api import model_data_fingerprint,thaw_model_data
    from framework.model_api.invocation_v2 import ModelInvocationContextV2
    from framework.model_api.observation import read_observation_plan
    from product_adapters.desktop_pet_v1.settlement_view import KEY,read_settlement
    session=app.runtime.session
    context=ModelInvocationContextV2(session_id=session.session_binding.session_id,scope_ref=app.definition.scope_ref,
        subject_ref=app.owner_ref,event_ref='batch-advice:'+session.position.fingerprint,action_ref=None,
        temporal_position=session.position.position,runtime_ordinal=session.position.runtime_ordinal)
    row=session.profile.get(KEY)
    if row is not None:
        accepted=read_settlement(app,historical=True)
        if accepted[0].state!=session.state or accepted[0].position!=session.position:raise ValueError('sampling settlement is stale')
        settlement=accepted[4].payload['settlement']
    else:
        settled=app.runtime.model_runtime.invoke('settle_state',payload={'state':session.state.state,'progress_contribution':None,'event_contribution':None},context=context)
        settlement=settled.payload['settlement']
    shared=app.runtime.model_runtime.invoke('project_shared_semantics',payload={'parameters':session.parameters.parameters,'settlement':settlement},context=context)
    planned=app.runtime.model_runtime.invoke('plan_observation',payload={'shared_projection':shared.payload['shared_projection'],'min_interval_ms':bounds_ms[0],'max_interval_ms':bounds_ms[1]},context=context)
    advice=read_observation_plan(planned,expected_context=context,shared_projection_execution=shared)
    row={'version':'petlab-batch-sampling-v1','interval_ms':advice.observation_advice['after_ms'],'bounds_ms':list(bounds_ms),'source_position':session.position.fingerprint,
         'temporal_advice':thaw_model_data(advice.observation_advice),'shared':thaw_model_data(shared.payload),'stage':planned.receipt.to_record(),'payload':thaw_model_data(planned.payload)}
    return {**row,'fingerprint':model_data_fingerprint(row)}

def verify_batch_job(journal,job,*,temporal_runtime=None):
    """A submitted batch must exactly cover its durably frozen raw parts."""
    from framework.model_api import model_data_fingerprint
    from model_packages.desktop_pet_v1.events import Content
    from model_packages.desktop_pet_v1.mouse_batch import validate_batch
    batch=job.get('batch_id')
    if batch is None:return
    rows=[r['value'] for r in journal.rows if r['kind']=='batch' and r['value'].get('batch_id')==batch]
    parts=[r['content'] for r in rows if r['operation']=='part']
    if not rows or not parts or len(job['contents'])!=1:raise ValueError('batch input lacks captured evidence')
    content=Content.from_record(job['contents'][0]);p=validate_batch(content.parameters)
    if p['batch_id']!=batch or p['parts']!=parts or job['coordinate']!=p['trajectory'][-1][2]:raise ValueError('batch input differs from captured parts')
    begin=next(r for r in rows if r['operation']=='begin')
    if p['quiet_ms']!=begin['quiet_ms'] or job['response_policy']!=begin['response_policy'] or p['window_origin_ms']!=begin['origin_ms'] or p['advice_fingerprint']!=begin['advice_fingerprint'] or p['planned_tick_at_ms']!=begin['tick_at_ms']:raise ValueError('batch plan differs from original window')
    advice=next((r['value']['advice'] for r in journal.rows if r['kind']=='batch' and r['value']['operation']=='advice' and r['value']['advice']['fingerprint']==p['advice_fingerprint']),None)
    if advice is None or advice['fingerprint']!=model_data_fingerprint({k:v for k,v in advice.items() if k!='fingerprint'}):raise ValueError('batch sampling lacks original advice')
    from model_packages.desktop_pet_v1.observation_policy import accelerate
    policy=accelerate(base_interval_ms=advice['interval_ms'],min_interval_ms=advice['bounds_ms'][0],gesture_origin_ms=begin['capture_origin_ms'],planning_coordinate=p['window_origin_ms'])
    if begin.get('sampling_policy')!=policy or p['planned_tick_at_ms']!=p['window_origin_ms']+policy['effective_advice']['after_ms']:raise ValueError('batch Tick differs from published interval')
    if p['reason']=='tick' and temporal_runtime is not None:
        target=temporal_runtime.resolve_observation_target(temporal_runtime.normalize_position(p['window_origin_ms']),policy['effective_advice'])
        if target.payload['at_ms']!=p['planned_tick_at_ms']:raise ValueError('batch target differs from temporal advice')
        if not temporal_runtime.prove_observation_due(target,temporal_runtime.normalize_position(p['sealed_at_ms'])).due:raise ValueError('batch observation is not formally due')
    # One sealed window cannot be consumed by another event ID.
    for r in journal.rows:
        if r['kind']=='job' and r['value'].get('batch_id')==batch and r['value'].get('event_id')!=job['event_id']:raise ValueError('batch already belongs to another input')

def apply_part(mouse,value):
    content=value['content'];p=content['parameters'];gesture=p['gesture_id']
    if p['phase']=='ended':observation=mouse.observe_finished(gesture)
    else:
        boundary=mouse.freeze_boundary(gesture)
        if boundary is None:raise ValueError('ongoing part lacks an active capture')
        observation=mouse.observe_boundary(boundary)
    if observation is None or observation.content.to_record()!=content:raise ValueError('batch part differs from physical capture')
    mouse.accept(observation)

def recover_batches(journal,product,*,through_sequence,is_stopping):
    from framework.model_api import thaw_model_data
    from model_packages.desktop_pet_v1.mouse_batch import build_batch
    for batch,rows in journal.pending_batches(through_sequence=through_sequence).items():
        if is_stopping():raise InterruptedError('host closed; batch retained')
        parts=[r['content'] for r in rows if r['operation']=='part']
        if not parts:
            journal.append('batch',{'operation':'abandon','batch_id':batch,'reason':'host_loss_without_frozen_interval'})
            continue
        begin=next(r for r in rows if r['operation']=='begin');at=parts[-1]['parameters']['trajectory'][-1][2]
        content=build_batch(batch_id=batch,parts=parts,recipient_ref=product.application.owner_ref,reason='recovery',sealed_at_ms=at,
            window_origin_ms=begin['origin_ms'],planned_tick_at_ms=begin['tick_at_ms'],advice_fingerprint=begin['advice_fingerprint'],quiet_ms=begin['quiet_ms'])
        job={'kind':'input','event_id':'input:'+batch,'batch_id':batch,'contents':[content.to_record()],'coordinate':at,'response_policy':begin['response_policy']}
        verify_batch_job(journal,job)
        from .product import wall_clock_ms
        product.interact(event_id=job['event_id'],contents=(content,),coordinate=at,response_policy=job['response_policy'],executor=None,is_cancelled=lambda:True,retry_failed=True,admission_coordinate=wall_clock_ms())
        # Original source is now durable; a retained required reply stays in the common recovery path.
        journal.append('batch',{'operation':'recovered','batch_id':batch,'event_id':job['event_id']})
