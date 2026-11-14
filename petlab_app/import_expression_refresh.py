"""Rebuild presentation only from an already published pair's accepted source receipts."""
from pathlib import Path

from framework.model_api import model_data_fingerprint as fingerprint
from model_packages.desktop_pet_v1 import expression_profile, import_persona, persona_resources
from product_adapters.desktop_pet_v1.import_provider import estimate
from .composition import PetDefinition, background_creation_context
from .desktop_app import configuration_from_record
from .history_import import ImportWorkspace
from .multi_pet import _immutable, _key, _read, _signed, _verify
from .paired_history_import import LABELS, pair_status
from .providers import ProviderGateway

VERSION='petlab-paired-import-expression-refresh-v1'
FILENAME='imported-expression-refresh.json'
RESOURCE_FILENAME='imported-persona-resources.json'


def refresh_pair(work,*,world,render_resources=False):
    status=pair_status(work)
    if any(not row['formal_history_published'] for row in status['participants'].values()):
        raise ValueError('expression refresh requires both published import histories')
    root=Path(world).resolve()
    manifest=_read(root/'workspace.json')
    records={};resources={}
    for label in LABELS:
        definition=PetDefinition.from_record(manifest['definitions'][label])
        actor=root/'actors'/_key(definition.owner_ref)[:16]
        config=_read(actor/'config.json')
        if fingerprint(config['definition'])!=definition.fingerprint:
            raise ValueError('expression refresh target differs from published role')
        with ImportWorkspace.restore(Path(work)/label) as w:
            published=w.setting('published')
            if (Path(published['session_path']).resolve()!=actor/'session.json'
                    or published['definition_ref']!=definition.fingerprint):
                raise ValueError('expression refresh must use the exact published role')
            cfg=configuration_from_record(w.setting('configuration'))
            gateway=ProviderGateway(cfg.transport,trace_dir=w.path/'providers')
            try:
                records[label]=_refresh(w,gateway,definition,config.get('expression_profile'))
                if render_resources:
                    resources[label]=_render_resources(w,gateway,definition,records[label])
            finally:gateway.close()
    row=_signed({'version':VERSION,'roles':records})
    _immutable(root/FILENAME,row)
    if not render_resources:return row
    rendered=_signed({'version':persona_resources.VERSION,'roles':resources})
    _immutable(root/RESOURCE_FILENAME,rendered)
    return rendered


def _refresh(work,gateway,definition,original):
    status=work.status()
    sources={i:{'role':m.role,'text':m.text} for i in range(status['next_index'])
             if (m:=work.message(i)).event_tag is None}
    own=[i for i,r in sources.items() if r['role']=='assistant']
    # Bound the refresh to two points along the accepted dialogue. There is no
    # new measurement or Writer request in this pass.
    checkpoints=sorted({own[len(own)//2-1],own[-1]}) if len(own)>=4 else own[-1:] if len(own)>=2 else []
    prior=[]
    trail=[]
    revised_ref=None
    for index in checkpoints:
        steps={i:work.step(i) for i in own if i<=index}
        hints={i:[token['text'] for token in step['initial']['payload']['measurement']['tokens']]
               for i,step in steps.items()}
        coordinate=work.setting('timeline')['coordinates'][index]
        seed=work.setting('configuration')['definition']
        binding,position=background_creation_context(session_id=seed['session_id'],coordinate=coordinate)
        selected={i:sources[i] for i in steps}
        # This is a bounded repair of imported presentation. Refuse oversized
        # source material instead of silently clipping evidence.
        if sum(len(row['text']) for row in selected.values())>16000:
            raise ValueError('expression refresh source exceeds its supported context')
        request=import_persona.refresh_request(selected,hints,index=index,previous=prior,
                                             author_background=definition.background)
        source_refs=tuple(step['initial']['parse_ref'] for step in steps.values())
        if trail:source_refs=(*source_refs,trail[-1]['parse_ref'])
        result=estimate(work,gateway,operation_key=f'expression-refresh-{import_persona.REFRESH_VERSION}:{index}',session_binding=binding,
            current_position=position,source_ref=f'profile:{definition.owner_ref}:{index}',
            codec_contract=import_persona.REFRESH_VERSION,messages=request,
            parse=lambda text:{'observations':import_persona.parse_refresh(text,selected,index=index,previous=prior)},
            recover_parse=lambda text:import_persona.recover_refresh(text,selected,index=index,previous=prior),
            source_receipt_refs=source_refs)
        observations=result['payload']['observations']
        if observations!=prior:revised_ref=result['parse_ref']
        prior=observations
        trail.append({'source_index':index,'parse_ref':result['parse_ref'],'trace_ref':result['trace_ref'],
                      'observations':observations,'rejected':result['payload'].get('rejected',[]),
                      'disposition':result['payload'].get('disposition'),
                      'deduplicated':result['payload'].get('deduplicated',[])})
    profile=original
    if revised_ref is not None:
        derived=import_persona.describe(prior)
        author=definition.background
        profile={'version':expression_profile.VERSION,'source_ref':revised_ref,
                 'description':author+'\n\n【从旧对话确认的表达习惯】\n'+derived if author else derived}
    return {'definition_ref':definition.fingerprint,'previous_profile_ref':fingerprint(original),
            'profile':profile,'steps':trail}


def load_profile_refresh(root,label,definition,original):
    path=Path(root)/RESOURCE_FILENAME
    version=persona_resources.VERSION
    if not path.is_file():
        path=Path(root)/FILENAME
        version=VERSION
    if not path.is_file():return original
    record=_verify(_read(path))
    role=record['roles'][label]
    if (record['version']!=version or role['definition_ref']!=definition.fingerprint
            or role['previous_profile_ref']!=fingerprint(original)):
        raise ValueError('expression refresh differs from original profile binding')
    return expression_profile.validate_expression_profile(role['profile'])


def _render_resources(work,gateway,definition,refresh):
    end=work.status()['next_index']-1
    terminal=work.step(end)['fit']
    own={i:m.text for i in range(end+1)
         if (m:=work.message(i)).role=='assistant' and m.event_tag is None}
    if sum(map(len,own.values()))>16000:
        raise ValueError('persona resource evidence exceeds its supported context')
    trail=refresh['steps']
    observations=trail[-1]['observations'] if trail else terminal['payload'].get('expression_observations',[])
    system,user=persona_resources.request(terminal['payload']['weights'],author=definition.background,
                                          observations=observations,own_lines=own)
    config=work.setting('configuration')['definition']
    coordinate=work.setting('timeline')['coordinates'][end]
    binding,position=background_creation_context(session_id=config['session_id'],coordinate=coordinate)
    refs=(terminal['parse_ref'],*(step['parse_ref'] for step in trail),
          *(work.step(i)['initial']['parse_ref'] for i in own))
    result=estimate(work,gateway,operation_key=f'expression-refresh-{persona_resources.VERSION}:{end}',
        session_binding=binding,current_position=position,source_ref=f'persona-resources:{definition.owner_ref}',
        codec_contract=persona_resources.VERSION,messages=[{'role':'system','content':system},{'role':'user','content':user}],
        parse=lambda text:persona_resources.parse(text,own),source_receipt_refs=tuple(dict.fromkeys(refs)))
    rendered=result['payload']
    profile={'version':expression_profile.RESOURCE_VERSION,'source_ref':result['parse_ref'],
             'description':definition.background,
             'resources':{'语气':rendered['语气'],'文风':rendered['文风'],
                          '口癖':[row['phrase'] for row in rendered['口癖']]}}
    expression_profile.validate_expression_profile(profile)
    return {'definition_ref':definition.fingerprint,'previous_profile_ref':refresh['previous_profile_ref'],
            'weights_source_ref':terminal['parse_ref'],'profile':profile,'render_receipt':result}
