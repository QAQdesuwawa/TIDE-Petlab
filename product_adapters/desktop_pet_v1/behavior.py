"""Local realization of a package-selected action through the common contract."""
import json
from framework.model_api import model_data_fingerprint, freeze_model_payload
from framework.realizer_protocol import RealizerRequest, RealizerAttempt, RealizerCandidate
from framework.realizer_host import execute_realizer
from model_packages.desktop_pet_v1.behavior import VERSION, select_idle, content, selected_action, staged_selection
from .execution import PreparedExecution
from petlab_app.destinations import destination as participant_destination


def request_for(app, event_id, *, private_thought=False):
    from petlab_app.idle_opening import source,is_idle_tick
    from petlab_app.reaction_action import selection as selection_for,authorization as authorize
    tick=source(app,event_id,current=True)[0] if is_idle_tick(app,event_id) else None
    selection=selection_for(app,event_id)
    staged=staged_selection(selection)
    combined=staged or selection==select_idle(eligible=True,mode='play_tail_composite')
    key=selected_action(selection)
    combined=combined or key=="move_near"
    if key is None:
        raise ValueError('idle tick did not select this behavior')
    authorization = authorize(app, event_id)
    control={'version': VERSION, 'selected_action_keys': [key], 'allow_speech': False}
    facts={'selection': selection, **({'tick_fingerprint':tick.row['fingerprint']} if tick is not None else {'input_event_id':event_id})}
    chosen=participant_destination(app,event_id)
    if chosen is not None:facts['participant_destination']=chosen
    from model_packages.desktop_pet_v1.shared_meal import AUDIENCE_CATALOG_VERSIONS
    if chosen is not None and selection.get('catalog_version') in AUDIENCE_CATALOG_VERSIONS:
        facts['meal_appearance_audience']=chosen['audience']
    scene=tick.row.get('scene_context') if tick is not None else None
    if scene is not None and key=='move_near':
        from model_packages.desktop_pet_v1 import spatial
        scene=spatial.validate(scene)
        if scene['owner_ref']!=app.owner_ref:raise ValueError('movement scene belongs to another actor')
        if authorization.target_ref in spatial.targets(scene):
            facts['scene_context']=scene;control['gaze_contract']=spatial.GAZE

    if combined:
        from .settled_presentation import snapshot
        from model_packages.desktop_pet_v1.settled_presentation import expression_from_snapshot
        from model_packages.desktop_pet_v1.composite import VERSION as COMPOSITE_VERSION
        # Reconstructing a sealed candidate after a failed commit reads only
        # the same accepted settlement; it does not authorize pending effects.
        pending=any(row['status']!='executed' for row in app._product()['executions'].values())
        state=snapshot(app,allow_pending=pending,upgrade_legacy=False)
        code=expression_from_snapshot(state) if state is not None else 'normal'
        control['composite']={'version':COMPOSITE_VERSION,'children':{
            'face':{'kind':'expression','key':code},'activity':{'kind':'action','key':key}}}
        facts['settled_presentation']=state
        if staged:
            control['allow_speech']=True
            control['staged_version']='desktop-pet-staged-v1'
            control['composite']['children']['voice']={'kind':'speech','key':'reply'}
            if private_thought:
                from model_packages.desktop_pet_v1.composite import THOUGHT_VERSION
                control['allow_thought']=True
                control['staged_version']='desktop-pet-staged-v2-private-thought'
                control['composite']['version']=THOUGHT_VERSION
                control['composite']['children']['inner']={'kind':'thought','key':'inner_monologue'}
    request = RealizerRequest.create(session_binding=app.runtime.session.session_binding,
        current_position=app.runtime.session.position, authorization=authorization,
        control=control,readonly_facts=facts)
    return authorization, request


def contents_for(request,user_ref):
    items=[content(user_ref,selection=request.readonly_facts['selection'])]
    if request.readonly_facts.get('meal_appearance_audience') is not None:
        from dataclasses import replace
        items=[replace(items[0],recipient_refs=tuple(request.readonly_facts['meal_appearance_audience']))]
    if 'composite' in request.control:
        from model_packages.desktop_pet_v1.events import Expression
        from model_packages.desktop_pet_v1.face_expression import VERSION as FACE_VERSION,LABELS
        code=request.control['composite']['children']['face']['key']
        face={'version':FACE_VERSION,'expression':code}
        if request.readonly_facts.get('scene_context') is not None:
            from model_packages.desktop_pet_v1.spatial import presentation
            face=presentation(code,request.readonly_facts['scene_context'],user_ref)
        items.insert(0,Expression(content_id='face',description='呈现'+LABELS[code]+'表情',recipient_refs=(user_ref,),
            parameters={'presentation':face}))
    return [item.to_record() for item in items]


def payload_for(request,user_ref):
    payload={'contents':contents_for(request,user_ref)}
    if 'staged_version' in request.control:
        payload['late_speech']={'content_id':'voice'}
        if request.control['staged_version']=='desktop-pet-staged-v2-private-thought':payload['late_speech']['thought_content_id']='inner'
    return payload


def prepare(product, event_id, *, private_thought=None):
    app=product.application
    authorization, request=request_for(app,event_id,private_thought=bool(getattr(product.writer,'private_thought',False)) if private_thought is None else private_thought)
    path=product.gateway.trace_dir/('behavior-'+request.fingerprint+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        record=json.loads(path.read_text(encoding='utf-8'))
        if record['request'] != request.to_record():
            raise ValueError('sealed behavior request differs')
        attempt=RealizerAttempt(**{**record['attempt'],
            'invocation_evidence':freeze_model_payload(record['attempt']['invocation_evidence'])})
        candidate=RealizerCandidate(**{**record['candidate'],
            'candidate_payload':freeze_model_payload(record['candidate']['candidate_payload']),
            'realization_evidence':freeze_model_payload(record['candidate']['realization_evidence'])})
    else:
        result=execute_realizer(request=request, implementation_ref=VERSION,
            invocation_evidence={'selection_fingerprint':model_data_fingerprint(request.readonly_facts)},
            invoke=lambda _: {'candidate_payload':payload_for(request,authorization.target_ref),
                'realization_evidence':{'version':VERSION, 'trace_ref':str(path),
                    'selection_fingerprint':model_data_fingerprint(request.readonly_facts)}})
        if result.status!='candidate_ready':raise RuntimeError('local behavior realization failed')
        attempt,candidate=result.attempt,result.candidate
        with path.open('x',encoding='utf-8') as stream:
            json.dump({'request':request.to_record(),'attempt':attempt.to_record(),
                'candidate':candidate.to_record()},stream,ensure_ascii=False)
    return PreparedExecution(position=app.runtime.session.position,scope_ref=app.definition.scope_ref,
        authorization=authorization,request=request,attempt=attempt,candidate=candidate)


def verify(product, prepared, source):
    event_id=source.tick_id if hasattr(source,'tick_id') else source.event.event_id
    authorization, request=request_for(product.application,event_id,private_thought=prepared.request.control.get('allow_thought',False))
    if request!=prepared.request or authorization!=prepared.authorization:
        raise ValueError('behavior differs from granted package selection')
    path=product.gateway.trace_dir/('behavior-'+request.fingerprint+'.json')
    record=json.loads(path.read_text(encoding='utf-8'))
    if record!={'request':request.to_record(),'attempt':prepared.attempt.to_record(),
                'candidate':prepared.candidate.to_record()}:
        raise ValueError('behavior differs from sealed local realization')
    if model_data_fingerprint(prepared.candidate.candidate_payload)!=model_data_fingerprint(
            payload_for(request,authorization.target_ref)):
        raise ValueError('behavior candidate changed the selected action')
