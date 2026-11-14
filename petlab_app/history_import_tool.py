"""User-facing import host over the package codec and original memory runtime."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import uuid
from .history_import import ImportWorkspace,read_transcript


def prepare(*,source,config,work,time_mode,user_id=None,npc_id=None,infer_persona=False):
    transcript=read_transcript(source,user_id=user_id,npc_id=npc_id)
    return _prepare(transcript,config=config,work=work,time_mode=time_mode,infer_persona=infer_persona,
                    user_id=user_id,npc_id=npc_id)


def _prepare(transcript,*,config,work,time_mode,infer_persona,user_id=None,npc_id=None,pair=None,now=None):
    from .setup_dialog import read_configuration_record
    from .composition import PetDefinition
    seed=deepcopy(read_configuration_record(config))
    if infer_persona:
        from model_packages.cx_six_seven_v1.initialization_schema import generate_base_constants
        from model_packages.cx_six_seven_v1.preference_parameters import B0_TARGETS
        if not any(m.role == 'assistant' and m.event_tag is None for m in transcript.messages):
            raise ValueError('inferred persona requires old AI speech')
        neutral = {name: 1 / len(B0_TARGETS) for name in B0_TARGETS}
        seed['definition'].update(name='导入角色', background='', initial_state=None,
            base_constants=generate_base_constants(neutral), base_constants_version='import-neutral-seed-v1',
            b0_binding={'version':'import-disabled-b0-v1','enabled':False,'weights':None})
    elif seed.get('expression_profile') is not None and (pair is None or not seed['definition']['background']):
        seed['definition']['background']=seed['expression_profile']['description']
    token=uuid.uuid4().hex
    seed['definition'].update(session_id='petlab:import:'+token,owner_ref='pet:import:'+token,
        scope_ref='scope:desktop:import:'+token)
    if pair is not None:
        seed['definition'].update(owner_ref=pair['roles'][pair['owner_source_role']],
                                  name=pair['name'],user_ref=pair['host_user_ref'])
    seed.pop('expression_profile',None)
    definition=PetDefinition.from_record(seed['definition'])
    if now is None:now=time.time_ns()//1_000_000
    if time_mode=='original':
        coordinates=[m.timestamp_ms for m in transcript.messages]
        if (any(x is None for x in coordinates) or any(a>b for a,b in zip(coordinates,coordinates[1:]))
                or any(x>now for x in coordinates)):
            raise ValueError('original mode requires nondecreasing Unix-ms timestamps, none missing or in the future')
    elif time_mode=='order':coordinates=list(range(now-len(transcript.messages)+1,now+1))
    else:raise ValueError('choose original or order time mode explicitly')
    with ImportWorkspace.create(work,transcript,definition_ref=definition.fingerprint) as workspace:
        workspace.setting('configuration',seed)
        if pair is not None:
            workspace.setting('npc_pair',pair)
            workspace.setting('pair_expression_contract','independent-expression-v2')
        if infer_persona and pair is None:
            from model_packages.desktop_pet_v1.import_persona import KEEP_POLICY, VERSION
            workspace.setting('persona_inference',{'version':VERSION,'revision_policy':KEEP_POLICY})
        if user_id is not None:
            workspace.setting('source_speakers',{'user':user_id,'assistant':npc_id})
        workspace.setting('timeline',{'mode':time_mode,'coordinates':coordinates,
            'created_at_ms':now,'synthetic_positions':time_mode=='order'})
        status=workspace.status()
    return {**status,'workspace':str(Path(work).resolve()),
        'time_mode':time_mode,'estimated_model_calls':(0 if pair is not None and infer_persona else 1)+2*len(transcript.messages),
        'api_called':False,'new_owner_ref':definition.owner_ref}


def advance(work,*,max_records,reuse_only=False):
    from .desktop_app import configuration_from_record
    from .providers import ProviderGateway
    from product_adapters.desktop_pet_v1.import_reconstruction import Reconstruction
    with ImportWorkspace.restore(work) as workspace:
        configuration=configuration_from_record(workspace.setting('configuration'))
        gateway=ProviderGateway(configuration.transport,trace_dir=workspace.path/'providers')
        try:
            return Reconstruction(workspace,gateway,definition=configuration.definition,
                coordinates=workspace.setting('timeline')['coordinates'],reuse_only=reuse_only).advance(max_records=max_records)
        finally:gateway.close()


def publish(work,*,output,max_records=None):
    from product_adapters.desktop_pet_v1.import_publication import publish as commit
    with ImportWorkspace.restore(work) as workspace:
        return commit(workspace,configuration_record=workspace.setting('configuration'),
            destination=output,max_records=max_records)


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf8')
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('prepare',help='validate sources, clone identity, preview call count; no network')
    create.add_argument('--source',required=True);create.add_argument('--config',required=True)
    create.add_argument('--work',required=True);create.add_argument('--time-mode',choices=('original','order'),required=True)
    create.add_argument('--user-id',help='source ID of the human speaker in a raw export')
    create.add_argument('--npc-id',help='source ID of the AI speaker in a raw export')
    create.add_argument('--peer-config',help='also import the source user as a separate NPC using this configuration')
    create.add_argument('--infer-persona',action='store_true',help='estimate presentation from old AI utterances')
    run=sub.add_parser('run',help='measure and jointly refine at most this many source records; paid API calls')
    run.add_argument('work');run.add_argument('--max-records',type=int,default=1)
    commit=sub.add_parser('publish',help='freeze final estimate and build formal memory, without model calls')
    commit.add_argument('work');commit.add_argument('--output',required=True)
    commit.add_argument('--max-records',type=int)
    status=sub.add_parser('status',help='read local progress without calling a model');status.add_argument('work')
    profile=sub.add_parser('refresh-profile',help='render persona resources for a published NPC pair')
    profile.add_argument('work');profile.add_argument('--world',required=True)
    args=parser.parse_args(argv)
    try:
        from .paired_history_import import prepare_pair,advance_pair,publish_pair,pair_status,_pair_root
        if args.command=='prepare' and args.peer_config:
            if args.user_id is not None or args.npc_id is not None:
                raise ValueError('use attributed role rows for paired import')
            result=prepare_pair(source=args.source,config=args.config,peer_config=args.peer_config,
                work=args.work,time_mode=args.time_mode,infer_persona=args.infer_persona)
        elif args.command=='prepare':result=prepare(source=args.source,config=args.config,work=args.work,time_mode=args.time_mode,
            user_id=args.user_id,npc_id=args.npc_id,infer_persona=args.infer_persona)
        elif args.command=='refresh-profile':
            from .import_expression_refresh import refresh_pair
            result=refresh_pair(args.work,world=args.world,render_resources=True)
        elif _pair_root(args.work).is_file():
            if args.command=='run':result=advance_pair(args.work,max_records=args.max_records)
            elif args.command=='publish':result=publish_pair(args.work,output=args.output,max_records=args.max_records)
            else:result=pair_status(args.work)
        elif args.command=='run':result=advance(args.work,max_records=args.max_records)
        elif args.command=='publish':result=publish(args.work,output=args.output,max_records=args.max_records)
        else:
            with ImportWorkspace.restore(args.work) as workspace:result=workspace.status()
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as error:
        print(json.dumps({'status':'stopped','detail':str(error),'automatic_retry':False},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
