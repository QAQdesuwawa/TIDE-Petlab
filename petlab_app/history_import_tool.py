"""User-facing import host over the package codec and original memory runtime."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import uuid
from .history_import import ImportWorkspace,read_transcript


def prepare(*,source,config,work,time_mode):
    from .setup_dialog import read_configuration_record
    from .composition import PetDefinition
    transcript=read_transcript(source);seed=deepcopy(read_configuration_record(config))
    if seed.get('expression_profile') is not None:
        seed['definition']['background']=seed['expression_profile']['description']
    token=uuid.uuid4().hex
    seed['definition'].update(session_id='petlab:import:'+token,owner_ref='pet:import:'+token,
        scope_ref='scope:desktop:import:'+token)
    seed.pop('expression_profile',None)
    definition=PetDefinition.from_record(seed['definition'])
    now=time.time_ns()//1_000_000
    if time_mode=='original':
        coordinates=[m.timestamp_ms for m in transcript.messages]
        if (any(x is None for x in coordinates) or any(a>b for a,b in zip(coordinates,coordinates[1:]))
                or any(x>now for x in coordinates)):
            raise ValueError('original mode requires nondecreasing Unix-ms timestamps, none missing or in the future')
    elif time_mode=='order':coordinates=list(range(now-len(transcript.messages)+1,now+1))
    else:raise ValueError('choose original or order time mode explicitly')
    with ImportWorkspace.create(work,transcript,definition_ref=definition.fingerprint) as workspace:
        workspace.setting('configuration',seed)
        workspace.setting('timeline',{'mode':time_mode,'coordinates':coordinates,
            'created_at_ms':now,'synthetic_positions':time_mode=='order'})
        status=workspace.status()
    return {**status,'workspace':str(Path(work).resolve()),
        'time_mode':time_mode,'estimated_model_calls':1+2*len(transcript.messages),
        'api_called':False,'new_owner_ref':definition.owner_ref}


def advance(work,*,max_records):
    from .desktop_app import configuration_from_record
    from .providers import ProviderGateway
    from product_adapters.desktop_pet_v1.import_reconstruction import Reconstruction
    with ImportWorkspace.restore(work) as workspace:
        configuration=configuration_from_record(workspace.setting('configuration'))
        gateway=ProviderGateway(configuration.transport,trace_dir=workspace.path/'providers')
        try:
            return Reconstruction(workspace,gateway,definition=configuration.definition,
                coordinates=workspace.setting('timeline')['coordinates']).advance(max_records=max_records)
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
    run=sub.add_parser('run',help='measure and jointly refine at most this many source records; paid API calls')
    run.add_argument('work');run.add_argument('--max-records',type=int,default=1)
    commit=sub.add_parser('publish',help='freeze final estimate and build formal memory, without model calls')
    commit.add_argument('work');commit.add_argument('--output',required=True)
    commit.add_argument('--max-records',type=int)
    status=sub.add_parser('status',help='read local progress without calling a model');status.add_argument('work')
    args=parser.parse_args(argv)
    try:
        if args.command=='prepare':result=prepare(source=args.source,config=args.config,work=args.work,time_mode=args.time_mode)
        elif args.command=='run':result=advance(args.work,max_records=args.max_records)
        elif args.command=='publish':result=publish(args.work,output=args.output,max_records=args.max_records)
        else:
            with ImportWorkspace.restore(args.work) as workspace:result=workspace.status()
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as error:
        print(json.dumps({'status':'stopped','detail':str(error),'automatic_retry':False},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
