"""Publish completed jointly revised estimates through original formal memory.

The final parameter estimate is frozen once, then numeric state is reconstructed
once using the original full-memory transaction. No repeated prefix replay is
performed during estimation and no model is called during publication.
"""
from dataclasses import replace
from pathlib import Path
from framework.model_api import model_data_fingerprint as fingerprint,thaw_model_data
from petlab_app.composition import PetDefinition,open_application
from petlab_app.setup_dialog import save_configuration
from petlab_app.imported_source import build,ingest

VERSION='desktop-pet-import-publication-v1'


def publish(workspace,*,configuration_record,destination,max_records=None):
    status=workspace.status()
    if status['next_index']!=status['message_count']:raise ValueError('finish reconstruction estimates before publication')
    if max_records is not None and (type(max_records) is not int or max_records<1):
        raise ValueError('positive publication step budget required')
    from petlab_app.desktop_app import configuration_from_record
    configuration_from_record(configuration_record,require_api_key=False)
    settings=workspace.setting('reconstruction')
    seed=PetDefinition.from_record(settings['definition'])
    if fingerprint(configuration_record['definition'])!=seed.fingerprint:
        raise ValueError('publication configuration differs from reconstruction role')
    terminal=workspace.step(status['message_count']-1)
    fit=terminal['fit']['payload']
    plan={'version':VERSION,'source_digest':status['source_digest'],'seed_definition_ref':seed.fingerprint,
        'terminal_step_ref':fingerprint(terminal),'final_estimate_ref':fit['fingerprint']}
    inference=workspace.setting('persona_inference')
    profile=None
    pair=workspace.setting('npc_pair')
    if pair is not None:
        from model_packages.desktop_pet_v1 import expression_profile,import_persona
        observed=fit['expression_observations']
        if observed or not seed.background:
            derived=import_persona.describe(observed)
            description=(seed.background+'\n\n【从旧对话确认的表达习惯】\n'+derived if seed.background else derived)
            profile={'version':expression_profile.VERSION,'source_ref':terminal['fit']['parse_ref'],
                     'description':description}
        plan['npc_pair']=pair
    elif inference is not None:
        from model_packages.desktop_pet_v1 import expression_profile, import_persona
        inferred=workspace.setting('initial_background')
        import_persona.revision_policy(inference)
        if inferred is None or inferred['payload']['version']!=import_persona.VERSION:
            raise ValueError('inferred persona publication requires its accepted estimate')
        plan['inferred_persona_ref']=inferred['parse_ref']
        profile={'version':expression_profile.VERSION, 'source_ref':inferred['parse_ref'],
                 'description':inferred['payload']['persona_card']}
    plan_ref=fingerprint(plan)
    definition=replace(seed,base_constants=fit['base_constants'],base_constants_version='import-joint:'+plan_ref)
    root=Path(destination).resolve()
    selected={**plan,'plan_ref':plan_ref,'destination':str(root),'definition':definition.to_record()}
    workspace.setting('publication',selected)
    completed=workspace.setting('published')
    if completed is not None:
        import json
        if not (root/'session.json').is_file() or not (root/'config.json').is_file():
            raise ValueError('published role files are missing; keep the original import workspace for recovery')
        saved=json.loads((root/'config.json').read_text('utf8'))
        if fingerprint(saved['definition'])!=definition.fingerprint:
            raise ValueError('published role definition differs')
        if profile is not None and fingerprint(saved.get('expression_profile')) != fingerprint(profile):
            raise ValueError('published inferred persona differs')
        return completed
    root.mkdir(parents=True,exist_ok=True)
    config=thaw_model_data(configuration_record)
    config['definition']=definition.to_record();config.pop('expression_profile',None)
    if profile is not None:
        config['expression_profile']=profile
    config['memory_recall_version']='desktop-pet-property-address-recall-v8'
    config_path=root/'config.json';session_path=root/'session.json'
    if config_path.exists():
        import json
        if fingerprint(json.loads(config_path.read_text('utf8')))!=fingerprint(config):
            raise ValueError('publication destination already contains another configuration')
    else:save_configuration(config_path,config)
    def no_current_execution(*args):
        raise ValueError('import publication cannot verify or execute a current Writer action')
    model_package=None
    if pair is not None:
        from model_packages.desktop_pet_v1.relation_package import MultiPetPackage
        from model_packages.cx_six_seven_v1.reference_parameters import reference_full_parameter_package
        model_package=MultiPetPackage(parameter_package=reference_full_parameter_package(),
                                      owner_ref=definition.owner_ref,human_ref=definition.user_ref)
    app=open_application(definition=definition,session_path=session_path,action_verifier=no_current_execution,
        initial_coordinate=settings['coordinates'][0],model_package=model_package)
    try:
        app.store.enable_incremental(app.runtime.session)
        count=len(app.runtime.events)
        if count>status['message_count']:raise ValueError('published destination has later events; cannot resume import into it')
        if count:
            previous=workspace.message(count-1)
            digest,original_index=workspace.source_location(count-1)
            prior_event,_=build(definition=definition,message=previous,source_digest=digest,
                coordinate=settings['coordinates'][count-1],estimate_ref='reconstruction:'+str(count-1),pair=pair,
                original_index=original_index if original_index!=count-1 else None)
            if app.runtime.events[-1].fingerprint!=prior_event.fingerprint:
                raise ValueError('publication checkpoint differs from expected transcript prefix')
        end=status['message_count'] if max_records is None else min(status['message_count'],count+max_records)
        parent=None if count==0 else fingerprint(workspace.step(count-1))
        for index in range(count,end):
            step=workspace.step(index);message=workspace.message(index)
            if (step['previous_step_fingerprint']!=parent
                    or step['source_message_fingerprint']!=fingerprint(message.record())):
                raise ValueError('reconstruction estimate chain differs from current source')
            digest,original_index=workspace.source_location(index)
            event,evidence=build(definition=definition,message=message,source_digest=digest,
                coordinate=settings['coordinates'][index],estimate_ref='reconstruction:'+str(index),pair=pair,
                original_index=original_index if original_index!=index else None)
            if step['event_fingerprint']!=event.fingerprint:raise ValueError('reconstruction event identity changed before publication')
            fit_result=step['fit'];candidate=fit_result['payload']
            if candidate['fingerprint']!=fingerprint({k:v for k,v in candidate.items() if k!='fingerprint'}):
                raise ValueError('joint estimate fingerprint differs')
            numeric=thaw_model_data(candidate['measurement'])
            measurement_result=fit_result
            if candidate.get('revision_disposition') in {'retained_no_revision_evidence','retained_unbacked_measurement'}:
                measurement_result=step['initial']
            numeric['source']='provider-wire:'+measurement_result['wire_fingerprint']
            numeric['model']=measurement_result['actual_model']
            measurement={'event_kind':event.event_kind,'committed_measurement':numeric,
                'measurement_source':{'version':'cx67-committed-measurement-source-v1',
                    'source_event_id':event.event_id,'local_event_id':event.event_id,
                    'projection_fingerprint':fingerprint(numeric)}}
            from .calibration import reference,KEY as CALIBRATION_KEY
            from model_packages.desktop_pet_v1.measurement_constraint import VERSION as CALIBRATION_VERSION
            measurement[CALIBRATION_KEY]=reference(app.runtime,event,calibration_version=CALIBRATION_VERSION)
            if candidate['properties'] is not None:measurement['pet_memory_recall']=candidate['properties']
            ingest(app,event=event,evidence=evidence,measurement=measurement)
            parent=fingerprint(step)
        result={'version':VERSION,'plan_ref':plan_ref,'definition_ref':definition.fingerprint,
            'config_path':str(config_path),'session_path':str(session_path),'trace_dir':str(root/'traces'),
            'published_records':len(app.runtime.events),'complete':end==status['message_count']}
        if result['complete']:workspace.setting('published',result)
        return result
    finally:app.close()
