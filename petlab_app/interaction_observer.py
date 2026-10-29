"""Serial, focused interaction observation using original model/temporal stages.

An actual planned due proof wakes collection. The newly received physical event
is measured and accepted before action assessment; eligibility is never a timer.
"""
from petlab_app.owned_journals import publish_session
from dataclasses import replace
from framework.model_api import model_data_fingerprint,thaw_model_data
from framework.model_api.action import read_action_assessment
from framework.model_api.observation import read_observation_plan
from framework.action_protocol import ActionAuthorization
from interaction_extensions.conversation_action_protocol import RuntimeActionAuthorizationAuthority
from ops.runtime_progress_transaction import execute_runtime_progress_transaction
from product_adapters.desktop_pet_v1.expression import PetExpressionService
from .product import wall_clock_ms
from model_packages.desktop_pet_v1.observation_policy import accelerate,effective_advice

KEY="desktop_pet_interaction_observations_v1"

def _signed(record):return dict(record,fingerprint=model_data_fingerprint(record))
def _verify(record):
    if not isinstance(record,dict) or model_data_fingerprint({k:v for k,v in record.items() if k!="fingerprint"})!=record.get("fingerprint"):
        raise ValueError("interaction tick receipt integrity differs")

def _journal(app):
    old=app.runtime.session.profile.get(KEY,{"version":KEY,"active_plan":None,"ticks":{}})
    return {**old,"active_plan":thaw_model_data(old["active_plan"]),"ticks":dict(old["ticks"])}
def _save(app,journal):
    # The current session is already durable; unchanged control metadata adds no fact.
    if KEY in app.runtime.session.profile and app.runtime.session.profile[KEY] == journal:
        return
    profile=dict(app.runtime.session.profile);profile[KEY]=journal
    session=replace(app.runtime.session,profile=profile);publish_session(app,session)

def _assess(app,event_id,bounds):
    lifecycle=PetExpressionService(application=app).project(event_id)
    expression=lifecycle.expression
    payload={"shared_projection":expression.shared_projection_execution.payload["shared_projection"],**bounds}
    execution=app.runtime.model_runtime.invoke("assess_action",payload=payload,context=expression.context)
    assessment=read_action_assessment(execution,source_fact_ref=event_id,source_position=app.runtime.session.position.position,
        source_runtime_ordinal=app.runtime.session.position.runtime_ordinal)
    return lifecycle,execution,assessment

def authorize_interaction_tick(app,event_id):
    record=app.runtime.session.profile.get(KEY,{}).get("ticks",{}).get(event_id)
    if record is None:raise ValueError("required policy or a formal eligible interaction tick is needed")
    record=thaw_model_data(record)
    _verify(record);plan=record["plan"];_verify(plan)
    session=app.runtime.session
    if plan["planning_stage"]["input_fingerprint"]!=model_data_fingerprint({"shared_projection":plan["shared_projection"],**plan["bounds"]}):raise ValueError("tick planning input differs")
    if plan["planning_stage"]["output_fingerprint"]!=model_data_fingerprint(plan["planning_output"]):raise ValueError("tick planning output differs")
    if plan["observation_advice"]!=plan["planning_output"]["observation_plan"]["observation_advice"]:raise ValueError("tick advice differs from model planning")
    progress_rows=session.profile.get("runtime_progress_transactions",())
    if not any(row["settlement_acceptance_fingerprint"]==plan["planning_acceptance_fingerprint"] and row["final_position_fingerprint"]==plan["origin_runtime_position_fingerprint"] for row in progress_rows):raise ValueError("tick plan lacks an accepted progress source")
    if record["session_binding_fingerprint"]!=session.session_binding.fingerprint:raise ValueError("tick session differs")
    temporal=app.runtime.temporal_runtime
    origin=temporal.normalize_position(plan["origin_coordinate"])
    source=app.runtime.memory_instance(event_id)
    params=source.event.contents[0].parameters
    gesture_origin=source.event.position.coordinate-params["total_duration_ms"]
    target=temporal.resolve_observation_target(origin,effective_advice(plan,gesture_origin_ms=gesture_origin))
    if target.to_record()!=plan["target"]:raise ValueError("tick plan target differs")
    source=app.runtime.memory_instance(event_id)
    params=source.event.contents[0].parameters
    if params.get("capture_version") not in {"petlab-mouse-observation-v2","petlab-mouse-observation-v3"} or params.get("phase")!="ongoing" or params.get("gesture_id")!=record["gesture_id"] or params.get("sequence")!=record["sequence"] or record["event_id"]!=event_id:raise ValueError("tick source is not its captured ongoing interval")
    due=temporal.prove_observation_due(target,source.event.position)
    if due.to_record()!=record["due_proof"] or not due.due:raise ValueError("interaction tick is not formally due")
    lifecycle,execution,assessment=_assess(app,event_id,plan["bounds"])
    if record["eligible"]!=assessment.eligible or execution.receipt.to_record()!=record["assessment_stage"] or model_data_fingerprint(execution.payload)!=record["assessment_output_fingerprint"]:
        raise ValueError("interaction assessment differs from accepted state")
    proof=RuntimeActionAuthorizationAuthority(session_binding=session.session_binding).authorize_autonomous_opening(
        current_position=session.position,due_proof=due,assessment=assessment)
    if proof.fingerprint!=record["opening_proof_fingerprint"]:raise ValueError("interaction eligibility proof differs")
    return ActionAuthorization.create(session_binding=session.session_binding,current_position=session.position,
        action_kind="autonomous_opening",subject_ref=app.owner_ref,target_ref=app.user_ref,source_fact_ref=event_id,
        source_settlement_fingerprint=source.fact.settlement_ref["acceptance_fingerprint"],
        package_eligibility_evidence={"version":"desktop-pet-interaction-assessment-v1","eligible":True,
            "assessment_receipt_fingerprint":model_data_fingerprint(execution.receipt.to_record())},
        host_authorization_evidence={"origin":"dynamic_interaction_tick","tick_receipt_fingerprint":record["fingerprint"],
            "opening_proof_fingerprint":proof.fingerprint,"due_proof_fingerprint":due.fingerprint})

class InteractionObserver:
    def __init__(self,*,product,mouse,bounds_ms=(2000,5000),response_policy="eligible",on_plan=None):
        if (not isinstance(bounds_ms,(list,tuple)) or len(bounds_ms)!=2 or any(isinstance(x,bool) or not isinstance(x,int) or x<=0 for x in bounds_ms)
                or bounds_ms[0]>bounds_ms[1]):raise ValueError("interaction observation engineering bounds are invalid")
        if response_policy not in {"required","eligible"}:raise ValueError("gesture finish response policy is invalid")
        self.response_policy=response_policy
        self.on_plan=on_plan
        self.product=product;self.mouse=mouse;self.bounds={"min_interval_ms":bounds_ms[0],"max_interval_ms":bounds_ms[1]}
        self.plan=None;self.pending=None;self.deferred_end=None;self.finishing=None;self.finishing_policy=None;self.deferred_end_policy=None
        app=product.application
        with app._lock:
            journal=_journal(app)
            if journal["active_plan"] is not None:
                journal["active_plan"]=None;_save(app,journal) # A restarted process has no ongoing physical gesture.

    @property
    def target_coordinate(self):return None if self.plan is None else self.plan["target"].payload["at_ms"]
    def wait_seconds(self):return None if self.plan is None else max(0,(self.target_coordinate-wall_clock_ms())/1000)

    def start(self,gesture_id,*,retry_failed=False):
        info=self.mouse.capture_info(gesture_id)
        if info is None or not self.mouse.definitions:return None
        if self.pending is not None:raise RuntimeError('pending observation requires explicit resume')
        if self.plan is not None and self.plan['record']['gesture_id']==gesture_id:return self.plan['record']
        snapshot=self.mouse.observe_started(gesture_id)
        if snapshot is not None:
            self.plan=None
            self.pending={'snapshot':snapshot,'event_id':f'input:{gesture_id}:interval:{snapshot.sequence}','accepted':False}
            return self._run_pending(None,wall_clock_ms,lambda:False,lambda:None,retry_failed=retry_failed)
        return self._arm(gesture_id,info["start_point"][2])

    def _arm(self,gesture_id,coordinate):
        if not self.mouse.is_active(gesture_id):self.plan=None;return None
        app=self.product.application
        with app._lock:
            app._require_ready();session=app.runtime.session
            progress=execute_runtime_progress_transaction(session=session,model_runtime=app.runtime.model_runtime,
                temporal_runtime=app.runtime.temporal_runtime,target_coordinate=coordinate,scope_ref=app.definition.scope_ref,
                subject_ref=app.owner_ref,event_ref="interaction-plan:"+gesture_id+":"+str(session.position.runtime_ordinal),
                action_ref=None,context_metadata={"committed_event_kind":"interaction_observation_planning"})
            context=replace(progress.context,temporal_position=progress.session.position.position,runtime_ordinal=progress.session.position.runtime_ordinal)
            shared=app.runtime.model_runtime.invoke("project_shared_semantics",payload={"parameters":progress.session.parameters.parameters,
                "settlement":progress.settlement_execution.payload["settlement"]},context=context)
            execution=app.runtime.model_runtime.invoke("plan_observation",payload={"shared_projection":shared.payload["shared_projection"],**self.bounds},context=context)
            advice=read_observation_plan(execution,expected_context=context,shared_projection_execution=shared)
            info=self.mouse.capture_info(gesture_id)
            policy=accelerate(base_interval_ms=advice.observation_advice['after_ms'],
                min_interval_ms=self.bounds['min_interval_ms'],gesture_origin_ms=info['start_point'][2],
                planning_coordinate=coordinate)
            target=app.runtime.temporal_runtime.resolve_observation_target(progress.session.position.position,policy['effective_advice'])
            record=_signed({"version":"desktop-pet-interaction-plan-v2","gesture_id":gesture_id,"interaction_policy":policy,
                "session_binding_fingerprint":session.session_binding.fingerprint,"origin_coordinate":coordinate,
                "bounds":self.bounds,"observation_advice":thaw_model_data(advice.observation_advice),"target":target.to_record(),
                "progress_receipt_fingerprint":model_data_fingerprint(progress.receipt),"planning_acceptance_fingerprint":progress.acceptance.fingerprint,
                "origin_runtime_position_fingerprint":progress.session.position.fingerprint,"shared_projection":thaw_model_data(shared.payload["shared_projection"]),
                "planning_output":thaw_model_data(execution.payload),"planning_stage":execution.receipt.to_record()})
            from product_adapters.desktop_pet_v1.settlement_view import attach as attach_settlement_view
            viewed=attach_settlement_view(progress.session,previous_state=session.state,
                previous_position=progress.progress_acceptance.state,context=progress.context,
                execution=progress.settlement_execution,acceptance=progress.acceptance,
                source_kind='progress',source_ref=progress.acceptance.fingerprint)
            profile=dict(viewed.profile);journal=_journal(app);journal["active_plan"]=record;profile[KEY]=journal
            updated=replace(viewed,profile=profile);publish_session(app,updated)
            self.plan={"target":target,"record":record}
            if self.on_plan is not None:self.on_plan(record)
            return record

    def sample_frozen(self,*,boundary_id,plan_fingerprint,executor,completion_coordinate=wall_clock_ms,
            is_cancelled=lambda:False,on_reply_ready=lambda:None):
        if self.pending is not None:raise RuntimeError('pending observation requires explicit resume')
        if self.plan is None or self.plan['record']['fingerprint']!=plan_fingerprint:
            self.mouse.discard_boundary(boundary_id)
            return {'observed':False,'reason':'superseded_plan'}
        info=self.mouse.boundary_info(boundary_id)
        if info['gesture_id']!=self.plan['record']['gesture_id']:
            raise ValueError('frozen Tick belongs to another gesture')
        temporal=self.product.application.runtime.temporal_runtime
        due=temporal.prove_observation_due(self.plan['target'],temporal.normalize_position(info['end_point'][2]))
        if not due.due:raise ValueError('frozen Tick is not formally due')
        snapshot=self.mouse.observe_boundary(boundary_id)
        if snapshot is None:return {'observed':False,'reason':'empty_interval'}
        self.pending={'snapshot':snapshot,'plan':self.plan,'due':due,
            'event_id':f'input:{snapshot.gesture_id}:interval:{snapshot.sequence}','accepted':False}
        self.plan=None
        return self._run_pending(executor,completion_coordinate,is_cancelled,on_reply_ready)

    def restore_plan(self,record):
        """Restore a captured operation, without restarting a physical gesture."""
        _verify(record);app=self.product.application;session=app.runtime.session
        if record['session_binding_fingerprint']!=session.session_binding.fingerprint or record['bounds']!=self.bounds:
            raise ValueError('persisted observation plan binding differs')
        if not any(row['settlement_acceptance_fingerprint']==record['planning_acceptance_fingerprint']
                and row['final_position_fingerprint']==record['origin_runtime_position_fingerprint']
                for row in session.profile.get('runtime_progress_transactions',())):
            raise ValueError('persisted observation plan lacks accepted progress')
        if (record['planning_stage']['input_fingerprint']!=model_data_fingerprint({'shared_projection':record['shared_projection'],**record['bounds']})
                or record['planning_stage']['output_fingerprint']!=model_data_fingerprint(record['planning_output'])
                or record['observation_advice']!=record['planning_output']['observation_plan']['observation_advice']):
            raise ValueError('persisted observation plan stage differs')
        temporal=app.runtime.temporal_runtime
        info=self.mouse.capture_info(record['gesture_id'])
        target=temporal.resolve_observation_target(temporal.normalize_position(record['origin_coordinate']),
            effective_advice(record,gesture_origin_ms=None if info is None else info['start_point'][2]))
        if target.to_record()!=record['target']:raise ValueError('persisted observation target differs')
        self.plan={'target':target,'record':record}

    def sample(self,*,executor,coordinate=None,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False,on_reply_ready=lambda:None):
        if self.plan is None:return None
        gesture_id=self.plan["record"]["gesture_id"]
        if not self.mouse.is_active(gesture_id):self.plan=None;return None
        now=wall_clock_ms() if coordinate is None else coordinate
        temporal=self.product.application.runtime.temporal_runtime
        if not temporal.prove_observation_due(self.plan["target"],temporal.normalize_position(now)).due:return None
        snapshot=self.mouse.observe(coordinate=coordinate,gesture_id=gesture_id)
        if snapshot is None:
            info=self.mouse.capture_info(gesture_id)
            if info is not None and info["phase"]=="ongoing":self._arm(gesture_id,info["last_point"][2])
            else:self.plan=None
            return None
        due=temporal.prove_observation_due(self.plan["target"],temporal.normalize_position(snapshot.end_point[2]))
        event_id=f"input:{snapshot.gesture_id}:interval:{snapshot.sequence}"
        self.pending={"snapshot":snapshot,"plan":self.plan,"due":due,"event_id":event_id,"accepted":False}
        self.plan=None
        return self._run_pending(executor,completion_coordinate,is_cancelled,on_reply_ready)

    def _run_pending(self,executor,completion_coordinate,is_cancelled,on_reply_ready,*,retry_failed=False):
        pending=self.pending;app=self.product.application;snapshot=pending["snapshot"];event_id=pending["event_id"]
        with app._lock:
            result=app.receive(event_id=event_id,scope_ref=app.definition.scope_ref,actor_ref=app.user_ref,
                contents=(snapshot.content,),coordinate=snapshot.end_point[2],
                measure=self.product.semantic.retry_measure if retry_failed else self.product.semantic.measure,response_policy='eligible')
            if not pending["accepted"]:self.mouse.accept(snapshot);pending["accepted"]=True
            if snapshot.content.parameters['phase']=='started':
                # Reception/measurement has no send authority. Plan from the
                # newly settled state; a late query never invents a held gesture.
                journal=_journal(app);journal['active_plan']=None;_save(app,journal)
                if self.mouse.is_active(snapshot.gesture_id) and not is_cancelled():
                    self._arm(snapshot.gesture_id,app.runtime.session.position.position.coordinate)
                self.pending=None
                return {'observed':True,'phase':'started','replied':False,'event_id':event_id}
            lifecycle,execution,assessment=_assess(app,event_id,self.bounds)
            proof=None
            if assessment.eligible:
                proof=RuntimeActionAuthorizationAuthority(session_binding=app.runtime.session.session_binding).authorize_autonomous_opening(
                    current_position=app.runtime.session.position,due_proof=pending["due"],assessment=assessment)
            record=_signed({"version":"desktop-pet-interaction-tick-v1","session_binding_fingerprint":app.runtime.session.session_binding.fingerprint,
                "event_id":event_id,"gesture_id":snapshot.gesture_id,"sequence":snapshot.sequence,"response_policy":'eligible',"plan":pending["plan"]["record"],
                "due_proof":pending["due"].to_record(),"assessment_stage":execution.receipt.to_record(),
                "assessment_output_fingerprint":model_data_fingerprint(execution.payload),"eligible":assessment.eligible,
                "opening_proof_fingerprint":None if proof is None else proof.fingerprint})
            journal=_journal(app);journal["active_plan"]=None;journal["ticks"][event_id]=record;_save(app,journal)
            replied=False
            from product_adapters.desktop_pet_v1.continuation import ContinuationGuard
            guard=ContinuationGuard(product=self.product,mouse=self.mouse,event_id=event_id,
                clock=wall_clock_ms,is_cancelled=is_cancelled)
            if proof is not None and executor is not None:
                outcome=self.product._reply(event_id,executor=executor,completion_coordinate=completion_coordinate,
                    is_cancelled=lambda:not guard.check(),continuation=guard,on_reply_ready=on_reply_ready,
                    on_prepared=lambda prepared:pending.__setitem__("prepared",prepared))
                replied=outcome is not None
            else:
                guard.check('before_writer')
            self.pending=None
            outcome={"due":True,"observed":True,"eligible":assessment.eligible,"replied":replied,"event_id":event_id}
            if guard.last['applicable'] and self.mouse.is_active(snapshot.gesture_id) and not is_cancelled():
                self._arm(snapshot.gesture_id,app.runtime.session.position.position.coordinate)
            return outcome

    def finish(self,gesture_id,*,executor,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False,retry_failed=False,response_policy=None):
        self.plan=None
        if self.pending is not None:
            self.deferred_end=gesture_id;self.deferred_end_policy=response_policy;return {"deferred":True}
        app=self.product.application
        with app._lock:
            journal=_journal(app);journal["active_plan"]=None;_save(app,journal)
            observation=self.mouse.observe_finished(gesture_id)
            if observation is None:return None
            self.finishing=gesture_id;self.finishing_policy=response_policy
            result=self.product.interact(event_id=f"input:{gesture_id}:interval:{observation.sequence}",contents=(observation.content,),
                coordinate=observation.end_point[2],response_policy=self.response_policy if response_policy is None else response_policy,executor=executor,
                completion_coordinate=completion_coordinate,is_cancelled=is_cancelled,retry_failed=retry_failed)
            self.mouse.accept(observation);self.finishing=None;return result

    def resume(self,*,executor,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False,on_reply_ready=lambda:None):
        if self.finishing is not None:
            return self.finish(self.finishing,executor=executor,completion_coordinate=completion_coordinate,is_cancelled=is_cancelled,retry_failed=True,response_policy=self.finishing_policy)
        if self.pending is None:
            return self.product.resume(executor=executor,completion_coordinate=completion_coordinate,is_cancelled=is_cancelled)
        app=self.product.application
        with app._lock:
            prepared=self.pending.get("prepared")
            if prepared is not None and any(entry["prepared_fingerprint"]==prepared.fingerprint for entry in app._product()["executions"].values()):
                # The common execute entry settles its existing executed source;
                # uncertain effects still raise and must be reconciled.
                result=app.execute(prepared,executor=executor,coordinate=completion_coordinate,measure=self.product.semantic.measure)
                gesture_id=self.pending["snapshot"].gesture_id
                self.pending=None
                if self.mouse.is_active(gesture_id) and not is_cancelled():
                    self._arm(gesture_id,app.runtime.session.position.position.coordinate)
            else:
                result=self._run_pending(executor,completion_coordinate,is_cancelled,on_reply_ready,retry_failed=True)
        if self.deferred_end is not None:
            gesture=self.deferred_end;policy=self.deferred_end_policy;self.deferred_end=None;self.deferred_end_policy=None
            return self.finish(gesture,executor=executor,completion_coordinate=completion_coordinate,is_cancelled=is_cancelled,response_policy=policy)
        return result

    def stop(self):
        self.plan=None
        app=self.product.application
        with app._lock:
            journal=_journal(app)
            if journal["active_plan"] is not None:journal["active_plan"]=None;_save(app,journal)
