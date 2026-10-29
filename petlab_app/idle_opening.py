"""Owner-local formal idle ticks; no invented input, event measurement or math."""
from petlab_app.owned_journals import publish_session
from dataclasses import replace,asdict,dataclass
from product_adapters.desktop_pet_v1.execution import ExecutionUncertain
import uuid
from framework.model_api import model_data_fingerprint,thaw_model_data,freeze_model_payload
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_api.observation import read_observation_plan
from framework.runtime_kernel import RuntimePositionState,RuntimeModelState
from framework.action_protocol import ActionAuthorization
from ops.runtime_progress_transaction import execute_runtime_progress_transaction
from ops.runtime_autonomous_observation import execute_runtime_autonomous_observation_transaction
from ops.runtime_committed_autonomous_grant import restore_committed_autonomous_grant
from ops.runtime_product_destination_authorization import authorize_autonomous_product_destination
from ops.runtime_autonomous_writer_packet_shadow import build_autonomous_product_writer_packet_shadow
from ops.runtime_expression_state import restore_runtime_expression_state
from ops.product_conversation_sessions import ProductConversationSessionRegistry,ProductDirectConversationSession
from ops.conversations import ConversationDescriptor
from ops.participants import make_human_participant,make_npc_profile
from .composition import DEFINITION_KEY,PetDefinition
from .product import wall_clock_ms
from .destinations import destination as participant_destination,primary as destination_primary,autonomous_destination

from product_adapters.desktop_pet_v1.settlement_view import attach as attach_settlement_view

KEY='desktop_pet_idle_opening_v1'


def _plan_source(session):
    return {'version':'petlab-idle-plan-source-v1','state':session.state.fingerprint,
        'position':session.position.fingerprint,'parameters':session.parameters.fingerprint}

def signed(value):return {**value,'fingerprint':model_data_fingerprint(value)}
def verify(value):
    if model_data_fingerprint({k:v for k,v in value.items() if k!='fingerprint'})!=value.get('fingerprint'):
        raise ValueError('idle opening receipt integrity differs')

def _stored(app):
    return app.runtime.session.profile.get(KEY,{'version':KEY,'plan':None,'pending_tick':None,'ticks':{},'outcomes':{}})

def journal(app):
    # Existing rows are read-only: callers replace entries, never edit them.
    old=_stored(app)
    return {**old,'plan':thaw_model_data(old['plan']),
        'ticks':dict(old['ticks']),'outcomes':dict(old['outcomes'])}

def publish(app,value,session=None):
    session=app.runtime.session if session is None else session
    profile=dict(session.profile);profile[KEY]=value
    updated=replace(session,profile=profile);publish_session(app,updated)

def is_idle_tick(app,event_id):return event_id in _stored(app)['ticks']

@dataclass(frozen=True)
class IdleTickSource:
    tick_id:str
    row:dict

def source(app,event_id,*,current=False):
    row=_stored(app)['ticks'].get(event_id)
    if row is None:raise ValueError('idle tick source is missing')
    row=thaw_model_data(row)
    verify(row)
    selection=row.get('behavior_selection')
    if selection is not None and selection.get('mode')=='symbolic':
        from model_packages.desktop_pet_v1.action_candidates import validate
        if validate(selection)['source']!=row['source_acceptance_fingerprint']:
            raise ValueError('action selection differs from formal token source')
    snapshot=row['source_snapshot'];session=app.runtime.session;binding=session.session_binding
    position=RuntimePositionState.create(session_binding=binding,
        position=app.runtime.temporal_runtime.normalize_position(snapshot['position']['position']['coordinate']),
        runtime_ordinal=snapshot['position']['runtime_ordinal'])
    if position.to_record()!=snapshot['position']:raise ValueError('idle tick source position differs')
    state=RuntimeModelState(**{**snapshot['state'],'state':freeze_model_payload({'state':snapshot['state']['state']})['state']})
    state.verify_integrity(binding)
    if session.parameters.fingerprint!=snapshot['parameters_fingerprint']:raise ValueError('idle tick parameters differ')
    frozen=replace(session,position=position,state=state)
    grant=restore_committed_autonomous_grant(session=frozen,row=row['grant'])
    if not any(x['settlement_acceptance_fingerprint']==row['source_acceptance_fingerprint']
        and x['final_position_fingerprint']==position.fingerprint for x in session.profile.get('runtime_progress_transactions',())):
        raise ValueError('idle tick lacks accepted progress lineage')
    target=app.runtime.temporal_runtime.resolve_observation_target(
        app.runtime.temporal_runtime.normalize_position(row['plan']['origin_coordinate']),row['plan']['advice'])
    due=app.runtime.temporal_runtime.prove_observation_due(target,position.position)
    if (target.to_record()!=row['plan']['target'] or not due.due
            or due.fingerprint!=grant.authorization.observation_due_fingerprint):raise ValueError('idle tick due lineage differs')
    if current and (session.position!=position or session.state!=state):raise ValueError('idle tick grant is stale')
    return IdleTickSource(event_id,row),grant

def authorize(app,event_id):
    tick,grant=source(app,event_id,current=True);role=PetDefinition.from_record(app.runtime.session.profile[DEFINITION_KEY])
    return ActionAuthorization.create(session_binding=app.runtime.session.session_binding,current_position=app.runtime.session.position,
        action_kind='autonomous_opening',subject_ref=app.owner_ref,target_ref=destination_primary(app,event_id),source_fact_ref=event_id,
        source_settlement_fingerprint=tick.row['source_acceptance_fingerprint'],
        package_eligibility_evidence={'version':KEY,'assessment_receipt_fingerprint':grant.authorization.action_assessment_receipt_fingerprint},
        host_authorization_evidence={'origin':'dynamic_idle_tick','tick_receipt_fingerprint':tick.row['fingerprint'],
            'scope_ref':role.scope_ref,'opening_proof_fingerprint':grant.authorization.fingerprint})

def projection(app,event_id,*,writer_request_id,cards_by_lane):
    _tick,grant=source(app,event_id,current=True)
    role=PetDefinition.from_record(app.runtime.session.profile[DEFINITION_KEY])
    registry=ProductConversationSessionRegistry(base_dir=app.store.path.parent/'idle-destinations',durable_writes=True)
    chosen=participant_destination(app,event_id)
    if chosen is None:
        expected=ProductDirectConversationSession(conversation=ConversationDescriptor(conversation_id=role.scope_ref,kind='direct',
            active_member_ids=(app.user_ref,app.owner_ref),membership_version=1),participants=(make_human_participant(app.user_ref),
                make_npc_profile(participant_id=app.owner_ref,profile_id=role.fingerprint,c_base_ref=role.base_constants_version).participant))
        selected_id=role.scope_ref
    else:
        from product_adapters.cx_six_seven_v1.multi_participant.sessions import ProductConversationSession
        from model_packages.cx_six_seven_v1.participants import Participant
        expected=ProductConversationSession(conversation=ConversationDescriptor.from_record(chosen['conversation']),
            participants=tuple(Participant.from_record(p) for p in chosen['participants']))
        selected_id=expected.conversation.conversation_id
    prior=registry.load_session(selected_id)
    if prior is None:
        if chosen is None:registry.register_direct_session(expected)
        else:registry.register_session(expected)
    elif prior.conversation!=expected.conversation or prior.participants!=expected.participants:
        raise ValueError('idle destination differs from frozen participants')
    destination=authorize_autonomous_product_destination(authorization=grant.authorization,current_position=grant.session.position,
        session_binding=grant.session.session_binding,conversation_registry=registry,conversation_id=selected_id)
    if chosen is not None and chosen.get('addressing_policy')=='original-autonomous-destination-v1':
        if tuple(chosen['addressed'])!=destination.addressed_participant_ids or tuple(chosen['audience'])!=destination.audience_participant_ids:
            raise ValueError('idle original destination policy differs from frozen audience')
    state=restore_runtime_expression_state(app.runtime.session.profile,session_binding=app.runtime.session.session_binding)
    return build_autonomous_product_writer_packet_shadow(runtime=app.runtime.model_runtime,observation=grant,destination=destination,
        shadow_request_id=writer_request_id,cards_by_lane=cards_by_lane,committed_remainders=state.committed_remainders)

def recalled_memories(app,event_id):
    _tick,grant=source(app,event_id,current=True)
    from product_adapters.desktop_pet_v1.shared_tick_memory import select
    selection=select(app,grant)
    return selection,app.runtime.read_memory(selection.history_read,event_ids=selection.recall.recalled_event_ids)


class IdleOpeningHost:
    def __init__(self,*,product,bounds_ms=(2000,300000),on_plan=None):
        if len(bounds_ms)!=2 or any(isinstance(x,bool) or not isinstance(x,int) or x<=0 for x in bounds_ms) or bounds_ms[0]>bounds_ms[1]:
            raise ValueError('idle observation engineering bounds are invalid')
        self.on_plan=on_plan
        self.product=product;self.app=product.application;self.bounds={'min_interval_ms':bounds_ms[0],'max_interval_ms':bounds_ms[1]}

    @property
    def target_coordinate(self):
        plan=_stored(self.app)['plan'];return None if plan is None else plan['target']['payload']['at_ms']

    def wait_seconds(self):
        at=self.target_coordinate;return None if at is None else max(0,(at-wall_clock_ms())/1000)

    def cancel(self,reason='external_activity'):
        with self.app._lock:
            data=journal(self.app)
            if data['pending_tick'] is not None:data['outcomes'][data['pending_tick']]={'status':'cancelled_before_effect','reason':reason}
            data['plan']=None;data['pending_tick']=None;publish(self.app,data)

    def arm(self,*,coordinate=None):
        with self.app._lock:
            app=self.app;data=journal(app)
            # A real effect/input awaiting settlement is the next event in
            # this owner's history. Planning must not advance past it.
            try:app._require_ready()
            except (RuntimeError,ExecutionUncertain):return None
            if data['pending_tick'] is not None:return None
            # Reconcile after an event; do not keep postponing an unchanged Tick.
            if data['plan'] is not None:
                verify(data['plan'])
                if coordinate is None and data['plan'].get('plan_source')==_plan_source(app.runtime.session):
                    return data['plan']
            coordinate=wall_clock_ms() if coordinate is None else coordinate
            from .idle_targets import choose
            target_selection=choose(app,coordinate=coordinate,bounds=self.bounds)
            metadata={'committed_event_kind':'tick'}
            if target_selection is not None:metadata['desktop_pet_counterparty_ref']=target_selection['peer']
            progress=execute_runtime_progress_transaction(session=app.runtime.session,model_runtime=app.runtime.model_runtime,
                temporal_runtime=app.runtime.temporal_runtime,target_coordinate=coordinate,scope_ref=None,subject_ref=app.owner_ref,
                event_ref='idle-plan:'+uuid.uuid4().hex,action_ref=None,context_metadata=metadata)
            session=attach_settlement_view(progress.session,previous_state=app.runtime.session.state,
                previous_position=progress.progress_acceptance.state,context=progress.context,
                execution=progress.settlement_execution,acceptance=progress.acceptance,
                source_kind='progress',source_ref=progress.acceptance.fingerprint)
            context=replace(progress.context,scope_ref=app.definition.scope_ref,temporal_position=session.position.position,runtime_ordinal=session.position.runtime_ordinal)
            shared=app.runtime.model_runtime.invoke('project_shared_semantics',payload={'parameters':session.parameters.parameters,
                'settlement':progress.settlement_execution.payload['settlement']},context=context)
            # Planning belongs to this explicitly configured product destination.
            # Progress remains owner-local; both read-only planning stages share the destination identity.
            planning_context=replace(context,scope_ref=app.definition.scope_ref)
            execution=app.runtime.model_runtime.invoke('plan_observation',payload={'shared_projection':shared.payload['shared_projection'],**self.bounds},context=planning_context)
            advice=read_observation_plan(execution,expected_context=planning_context,shared_projection_execution=shared)
            target=app.runtime.temporal_runtime.resolve_observation_target(session.position.position,advice.observation_advice)
            data['plan']=signed({**({'target_selection':target_selection} if target_selection is not None else {}),'origin_coordinate':coordinate,'advice':thaw_model_data(advice.observation_advice),
                'target':target.to_record(),'bounds':self.bounds,'plan_source':_plan_source(session),'planning_stage':execution.receipt.to_record(),
                'planning_payload':thaw_model_data(execution.payload),'shared_projection':thaw_model_data(shared.payload['shared_projection'])})
            publish(app,data,session)
            if self.on_plan is not None:self.on_plan(data['plan'])
            return data['plan']

    def sample(self,*,coordinate=None,executor,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False,on_reply_ready=lambda:None):
        with self.app._lock:
            app=self.app;data=journal(app);plan=data['plan']
            try:app._require_ready()
            except (RuntimeError,ExecutionUncertain):return None
            if plan is None:return None
            verify(plan);coordinate=wall_clock_ms() if coordinate is None else coordinate
            if is_cancelled():return None
            if plan.get('plan_source')!=_plan_source(app.runtime.session):
                # A committed event changed the source; an old alarm grants nothing.
                self.arm(coordinate=max(coordinate,app.runtime.session.position.position.coordinate))
                return {'observed':False,'reason':'settlement_changed','plan_rebuilt':True}
            target=app.runtime.temporal_runtime.resolve_observation_target(app.runtime.session.position.position,plan['advice'])
            if target.to_record()!=plan['target']:raise ValueError('idle plan differs from current source position')
            if not app.runtime.temporal_runtime.prove_observation_due(target,app.runtime.temporal_runtime.normalize_position(coordinate)).due:return None
            if is_cancelled():return None
            tick_id='idle-tick:'+uuid.uuid4().hex
            observed=execute_runtime_autonomous_observation_transaction(session=app.runtime.session,model_runtime=app.runtime.model_runtime,
                temporal_runtime=app.runtime.temporal_runtime,observation_target=target,target_coordinate=coordinate,
                participant_id=app.owner_ref,tick_event_id=tick_id,assessment_payload=plan['bounds'])
            progress=observed.progress_transaction
            viewed=attach_settlement_view(observed.session,previous_state=app.runtime.session.state,
                previous_position=progress.progress_acceptance.state,context=progress.context,
                execution=progress.settlement_execution,acceptance=progress.acceptance,
                source_kind='progress',source_ref=progress.acceptance.fingerprint)
            from model_packages.desktop_pet_v1.behavior import select_idle
            mode=getattr(self.product,'idle_behavior','speech')
            chosen=(plan['target_selection']['destination'] if 'target_selection' in plan else autonomous_destination(participant_destination(app)))
            scene=None
            if mode=='symbolic':
                from model_packages.desktop_pet_v1.action_candidates import choose,compact_tokens
                from model_packages.desktop_pet_v1 import movement,spatial
                from product_adapters.desktop_pet_v1.settlement_view import KEY as SETTLEMENT_KEY
                supplier=getattr(app,'scene_snapshot',None)
                scene=supplier() if callable(supplier) else None
                if scene is not None:
                    scene=spatial.validate(scene)
                    if scene['owner_ref']!=app.owner_ref:raise ValueError('action scene belongs to another actor')
                target_ref=chosen['primary'] if chosen is not None else app.user_ref
                catalog=movement.catalog(getattr(self.product,'action_catalog',None),scene=scene,target_ref=target_ref)
                from model_packages.desktop_pet_v1 import shared_meal
                meal_supplier=getattr(app,'meal_snapshot',None)
                meal_scene=meal_supplier() if callable(meal_supplier) else None
                catalog=shared_meal.catalog(catalog,meal_scene,app.owner_ref,target_ref)
                token_plan=viewed.profile[SETTLEMENT_KEY]['reaction']['expression_plan']
                selection=choose(eligible=observed.assessment.eligible,tokens=compact_tokens(token_plan),source=progress.acceptance.fingerprint,catalog=catalog,allow_speech=self.product.writer is not None)
            else:selection=select_idle(eligible=observed.assessment.eligible,mode=mode)
            scene_facts={}
            from model_packages.desktop_pet_v1 import shared_meal
            if selection is not None and selection.get('catalog_version') in shared_meal.SEQUENCE_CATALOG_VERSIONS:
                from model_packages.desktop_pet_v1.action_sequence import offer
                offered=offer(selection,catalog,owner_ref=app.owner_ref,target_ref=target_ref)
                if offered is not None:scene_facts['action_sequence_context']=offered
            if selection is not None and selection.get('action_keys')==['move_near'] and scene is not None:
                scene_facts['scene_context']=scene
            row=signed({**scene_facts,**({'participant_destination':chosen} if chosen is not None else {}),'version':KEY,'tick_id':tick_id,'plan':plan,'behavior_selection':selection,'source_acceptance_fingerprint':observed.progress_transaction.acceptance.fingerprint,
                'source_snapshot':{'position':observed.session.position.to_record(),'state':observed.session.state.to_record(),
                    'parameters_fingerprint':observed.session.parameters.fingerprint},
                'grant':signed({'tick_event_id':tick_id,'participant_id':app.owner_ref,'observation_receipt':thaw_model_data(observed.receipt),
                    'settlement_stage_receipt':observed.progress_transaction.settlement_execution.receipt.to_record(),
                    'settlement_output':thaw_model_data(observed.progress_transaction.settlement_execution.payload),
                    'shared_projection_stage_receipt':observed.shared_projection_execution.receipt.to_record(),
                    'shared_projection_output':thaw_model_data(observed.shared_projection_execution.payload),
                    'assessment_stage_receipt':observed.assessment_execution.receipt.to_record(),
                    'assessment_output':thaw_model_data(observed.assessment_execution.payload),
                    'authorization':None if observed.authorization is None else asdict(observed.authorization)})})
            data['ticks'][tick_id]=row;data['plan']=None
            data['pending_tick']=tick_id if observed.assessment.eligible else None
            publish(app,data,viewed)
            replied=False
            has_content=selection is None or selection.get('mode')!='symbolic' or bool(selection['action_keys']) or selection['allow_speech']
            if observed.assessment.eligible and not has_content:
                data=journal(app);data['pending_tick']=None;data['outcomes'][tick_id]={'status':'no_content_selected'};publish(app,data)
            if observed.assessment.eligible and has_content and executor is not None and not is_cancelled():
                result=self.product._reply(tick_id,executor=executor,completion_coordinate=completion_coordinate,
                    is_cancelled=is_cancelled,on_reply_ready=on_reply_ready)
                replied=result is not None
                data=journal(app);data['pending_tick']=None;data['outcomes'][tick_id]={'status':'executed' if replied else 'no_speech_selected' if getattr(self.product,'last_quiet_realization',None) is not None else 'cancelled_before_effect'};publish(app,data)
            if data['pending_tick'] is None and not is_cancelled():self.arm(coordinate=app.runtime.session.position.position.coordinate)
            return {'observed':True,'tick_id':tick_id,'eligible':observed.assessment.eligible,'replied':replied}

    def resume(self,*,executor,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False):
        with self.app._lock:
            data=journal(self.app);tick=data['pending_tick']
            if tick is None:return None
            result=self.product._reply(tick,executor=executor,completion_coordinate=completion_coordinate,is_cancelled=is_cancelled)
            quiet=getattr(self.product,'last_quiet_realization',None)
            if result is None and quiet is None:return None
            data=journal(self.app);data['pending_tick']=None;data['outcomes'][tick]={'status':'no_speech_selected' if quiet is not None and result is None else 'executed'};publish(self.app,data)
            self.arm(coordinate=self.app.runtime.session.position.position.coordinate);return result
