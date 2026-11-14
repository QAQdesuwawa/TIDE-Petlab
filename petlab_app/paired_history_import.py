"""Two participant views over the existing sequential history importer."""
from dataclasses import replace
from pathlib import Path
import time
import uuid

from .history_import import ImportWorkspace, read_transcript

VERSION = 'petlab-npc-pair-import-v1'
LABELS = ('first', 'second')


def _pair_root(work):
    return Path(work) / 'npc-pair-import.json'


def prepare_pair(*,source,config,peer_config,work,time_mode,infer_persona=False):
    from .history_import_tool import _prepare
    from .multi_pet import _immutable
    from .setup_dialog import read_configuration_record

    original = read_transcript(source)
    if any(m.event_tag is not None for m in original.messages):
        raise ValueError('paired import currently requires attributed dialogue')
    if {m.role for m in original.messages} != {'user','assistant'}:
        raise ValueError('paired import requires both source speakers')
    configs = (peer_config, config)
    input_names = tuple(read_configuration_record(path)['definition']['name'] for path in configs)
    names = ('旧对话甲','旧对话乙') if infer_persona else input_names
    if len(set(names)) != 2:
        raise ValueError('paired author configurations need distinct display names')
    token = uuid.uuid4().hex
    roles = {role:'npc:import:'+token+':'+label for role,label in zip(('user','assistant'),LABELS)}
    now = time.time_ns() // 1_000_000
    root = Path(work)
    root.mkdir(parents=True,exist_ok=False)
    for role,label,path,name in zip(roles,LABELS,configs,names):
        view = replace(original,messages=tuple(
            replace(m,role='assistant' if m.role==role else 'user') for m in original.messages))
        pair = {'roles':roles,'owner_source_role':role,'name':name,'host_user_ref':'user:local'}
        _prepare(view,config=path,work=root/label,time_mode=time_mode,infer_persona=infer_persona,pair=pair,now=now)
    manifest = {'version':VERSION,'source_digest':original.digest,'roles':roles,'participants':list(LABELS)}
    _immutable(_pair_root(root),manifest)
    return {**pair_status(root),'api_called':False,
            'estimated_model_calls':2*(2*len(original.messages)+(0 if infer_persona else 1))}


def pair_status(work):
    from .multi_pet import _read
    root = Path(work)
    manifest = _read(_pair_root(root))
    if manifest['version'] != VERSION or manifest['participants'] != list(LABELS):
        raise ValueError('paired import manifest differs')
    rows = {}
    for source_role,label in zip(('user','assistant'),LABELS):
        with ImportWorkspace.restore(root/label) as workspace:
            binding=workspace.setting('npc_pair')
            row=workspace.status()
            if (binding['owner_source_role']!=source_role or binding['roles']!=manifest['roles']
                    or row['source_digest']!=manifest['source_digest']):
                raise ValueError('paired import source binding differs')
            rows[label]=row
    return {'version':VERSION,'participants':rows}


def advance_pair(work,*,max_records):
    from .history_import_tool import advance
    pair_status(work)
    for label in LABELS:
        advance(Path(work)/label,max_records=max_records)
    return pair_status(work)


def publish_pair(work,*,output,max_records=None):
    from .composition import PetDefinition
    from .history_import_tool import publish
    from .multi_pet import MultiPetWorkspace, _immutable, _key
    from .setup_dialog import read_configuration_record

    status=pair_status(work)
    if any(row['next_index']!=row['message_count'] for row in status['participants'].values()):
        raise ValueError('finish reconstruction for both NPCs before publication')
    root=Path(output).resolve()
    reports={}
    definitions={}
    for label in LABELS:
        child=Path(work)/label
        with ImportWorkspace.restore(child) as workspace:
            owner=workspace.setting('configuration')['definition']['owner_ref']
        local=root/'actors'/_key(owner)[:16]
        report=publish(child,output=local,max_records=max_records)
        reports[label]=report
        definitions[label]=PetDefinition.from_record(read_configuration_record(local/'config.json')['definition'])
    complete=all(report['complete'] for report in reports.values())
    if complete:
        MultiPetWorkspace(root,definitions)
        _immutable(root/'imported-pair.json',{'version':VERSION,
            'definitions':{label:d.fingerprint for label,d in definitions.items()}})
    return {'complete':complete,'workspace':str(root),'participants':reports}


def fork_pair(work,*,source,output,start,stop):
    """Retain an exact sealed prefix and add one explicitly indexed source segment."""
    import json
    from shutil import copyfile
    from .history_import_tool import advance
    from .multi_pet import _immutable, _read

    root=Path(work);destination=Path(output)
    status=pair_status(root)
    counts={row['message_count'] for row in status['participants'].values()}
    if len(counts)!=1 or any(row['next_index']!=row['message_count'] for row in status['participants'].values()):
        raise ValueError('paired continuation requires a completed common prefix')
    count=next(iter(counts))
    full=read_transcript(source)
    if not count<=start<stop<=len(full.messages) or any(m.event_tag for m in full.messages):
        raise ValueError('invalid paired continuation source segment')
    manifest=_read(_pair_root(root))
    destination.mkdir(parents=True,exist_ok=False)
    reused=0
    for label in LABELS:
        with ImportWorkspace.restore(root/label) as old:
            pair=old.setting('npc_pair')
            timeline=old.setting('timeline')
            if old.setting('source_extension') is not None or timeline['mode']!='order':
                raise ValueError('continuation requires the original order-only workspace')
            selected=(*full.messages[:count],*full.messages[start:stop])
            messages=tuple(replace(m,index=i,role='assistant' if m.role==pair['owner_source_role'] else 'user')
                           for i,m in enumerate(selected))
            if messages[:count]!=tuple(old.message(i) for i in range(count)):
                raise ValueError('continuation source prefix differs')
            prefix_path=old.connection.execute('SELECT source_path FROM metadata WHERE id=1').fetchone()[0]
            transcript=replace(full,source_path=prefix_path,digest=manifest['source_digest'],messages=messages)
            coordinates=timeline['coordinates']
            continued=coordinates+list(range(coordinates[-1]+1,coordinates[-1]+1+stop-start))
            expected=[old.step(i) for i in range(count)]
            with ImportWorkspace.create(destination/label,transcript,definition_ref=old.status()['definition_ref']) as branch:
                for key in ('configuration','npc_pair','initial_background','pair_expression_contract'):
                    value=old.setting(key)
                    if value is not None:branch.setting(key,value)
                branch.setting('timeline',{**timeline,'coordinates':continued})
                branch.setting('source_extension',{'source_digest':full.digest,'source_path':full.source_path,
                    'prefix_count':count,'start':start,'stop':stop})
                trace_dir=branch.path/'providers'
                trace_dir.mkdir()
                for key,reservation in old.connection.execute('SELECT operation_key,reservation FROM calls'):
                    if key.startswith('expression-refresh-'):continue
                    nonce=json.loads(reservation)['invocation_id']
                    if len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):
                        raise ValueError('invalid original invocation reference')
                    filename=nonce+'.json'
                    copyfile(old.path/'providers'/filename,trace_dir/filename)
                    branch.connection.execute('INSERT INTO calls VALUES (?,?)',(key,reservation))
                    reused+=1
                branch.connection.commit()
        result=advance(destination/label,max_records=count,reuse_only=True)
        if result['next_index']!=count:raise ValueError('replay did not restore the complete prefix')
        with ImportWorkspace.restore(destination/label) as branch:
            if [branch.step(i) for i in range(count)]!=expected:
                raise ValueError('replayed estimates differ from the original prefix')
    _immutable(_pair_root(destination),manifest)
    return {**pair_status(destination),'reused_provider_calls':reused,'source_rows_1based':[start+1,stop]}
