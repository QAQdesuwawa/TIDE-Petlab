"""One development CLI over the existing formal Runtime APIs (trusted Python)."""
import argparse
import importlib
from importlib.resources import files
import json
import keyword
from pathlib import Path
import re
import os
import shlex
import sys
import tomllib


def new(name, destination):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', name) or keyword.iskeyword(name):
        raise ValueError('new: name must be a lowercase Python identifier (1–48 characters)')
    reserved = set(sys.stdlib_module_names) | {
        'framework', 'ops', 'model_packages', 'temporal_packages', 'tide_dev',
        'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1,10)),
        *(f'lpt{i}' for i in range(1,10))}
    if name in reserved:
        raise ValueError('new: name conflicts with a standard library, SDK module or device name; choose a domain-specific name')
    target = Path(destination).resolve()
    # mkdir is exclusive; existing directories are never adopted or overwritten.
    target.mkdir(parents=True, exist_ok=False)
    package = target/name
    package.mkdir()
    (package/'__init__.py').write_text('from .package import Package\n', encoding='utf8')
    for template, output in [('package.py.txt', 'package.py'), ('definition.py.txt', 'definition.py')]:
        body = files('tide_dev').joinpath('templates', template).read_text(encoding='utf8')
        (package/output).write_text(body.replace('__NAME__', name), encoding='utf8')
    (target/'tide.toml').write_text(
        f'model = "{name}:Package"\ntemporal = "temporal_packages.wall_clock_ms_v1:WallClockMillisecondsV1"\n'
        'scenario = "scenario.json"\nsession_id = "dev:session"\nscope_ref = "dev:scope"\n'
        'subject_ref = "dev:agent"\n[model_kwargs]\nobservation_unit = "milliseconds"\n', encoding='utf8')
    scenario = {'initial_coordinate': 1000, 'parameters': {'responsiveness': .75, 'restraint': .25},
                'state': {'affinity': .4, 'tension': .2, 'expression_drive': .1},
                'events': [{'event_ref': 'dev:signal:1', 'coordinate': 1100,
                            'measurement': {'affinity_stimulus': .6, 'conflict_stimulus': .2}}]}
    (target/'scenario.json').write_text(json.dumps(scenario, indent=2)+'\n', encoding='utf8')
    (target/'AGENTS.md').write_text(
        '# Generated TIDE package\n\nEdit `'+name+'/definition.py` for schema names, domain math and action eligibility. '
        'Edit `scenario.json` to match its input fields. `package.py` implements existing model API v2 stages; '
        'it is ordinary editable package code, not a new framework protocol.\n\n'
        'Run `tide-dev check .`, then `tide-dev run . --output first.json`. '
        'Restore with `tide-dev run . --restore first.json`. Use a new output path; outputs are never overwritten.\n\n'
        'The starter is a bounded numeric model: parameters/state in [0,1], measurement in [-1,1], '
        'time progress holds state. Change the generated package if your domain needs other ranges or time flow. '
        'Do not change installed Runtime code or existing sample package math. '
        'Bump PACKAGE_VERSION when definitions change; restore must reject incompatible old bindings. '
        'No memory/Writer/GUI or real external execution is implemented by this fixture host.\n', encoding='utf8')
    command = [sys.executable, '-m', 'tide_dev', 'check', str(target)]
    next_command = ('& "'+sys.executable+'" -m tide_dev check "'+str(target)+'"'
                    if os.name == 'nt' else shlex.join(command))
    return {'created': str(target), 'next': next_command, 'next_argv': command}


def factory(spec, kwargs):
    module, sep, name = spec.partition(':')
    if not sep or not module or not name:raise ValueError('factory must be module:callable')
    return getattr(importlib.import_module(module), name)(**kwargs)


def execute(project, restore=None):
    """Shared check/run path. It simulates declared fixtures, never external effects."""
    from framework.model_api import thaw_model_data, BASE_MODEL_CAPABILITIES_V2
    from framework.runtime_variant import RuntimeVariantDefinition
    from ops.runtime_package_composition import compose_runtime_packages
    from ops.runtime_session_bootstrap import bootstrap_runtime_session, restore_bootstrapped_runtime_session
    from ops.runtime_progress_transaction import execute_runtime_progress_transaction
    from ops.runtime_event_transaction import execute_committed_event_transaction
    from ops.runtime_stage_verification import verify_runtime_stage_execution

    phase = 'configuration'
    try:
        root = Path(project).resolve()
        config = tomllib.loads((root/'tide.toml').read_text(encoding='utf-8-sig'))
        sys.path.insert(0, str(root))
        sys.dont_write_bytecode = True
        phase = 'registration'
        model = factory(config['model'], config.get('model_kwargs', {}))
        temporal = factory(config['temporal'], config.get('temporal_kwargs', {}))
        variant = RuntimeVariantDefinition.create(
            implementation_id='tide.dev-fixture-host.v1', kernel_implementation_id='tide.runtime-kernel.v1',
            mechanism_implementations={'persistent_state_authority':'state-authority-v1','commit_gate':'commit-gate-v1'},
            realizer_profile_id='tide.dev-no-language.v1', resource_policy={
                'current_input':'enabled','persona':'enabled','conversation':'enabled',
                'expression':'enabled','memory':'disabled','form':'enabled'})
        composed = compose_runtime_packages(model_package=model, temporal_package=temporal,
            session_id=config['session_id'], variant_definition=variant,
            required_model_capabilities=(*BASE_MODEL_CAPABILITIES_V2, 'advance_progress'))
        phase = 'restore' if restore else 'initialization'
        if restore:
            saved = json.loads(Path(restore).read_text(encoding='utf8'))
            session = restore_bootstrapped_runtime_session(profile=saved['profile'],
                model_runtime=composed.model_runtime, temporal_runtime=composed.temporal_runtime,
                runtime_variant=composed.variant_binding)
            if session.state.fingerprint != saved['state_fingerprint']:
                raise ValueError('restored state fingerprint differs from saved report')
            return {'mode':'restore', 'state':thaw_model_data(session.state.state),
                    'state_fingerprint':session.state.fingerprint, 'restored_same_state':True,
                    'profile':thaw_model_data(session.profile)}
        scenario_path = (root/config['scenario']).resolve()
        if not scenario_path.is_relative_to(root):raise ValueError('scenario path must stay in project')
        scenario = json.loads(scenario_path.read_text(encoding='utf-8-sig'))
        session = bootstrap_runtime_session(profile={}, model_runtime=composed.model_runtime,
            temporal_runtime=composed.temporal_runtime, runtime_variant=composed.variant_binding,
            initial_coordinate=scenario['initial_coordinate'], parameter_payload=scenario['parameters'],
            state_payload=scenario['state'])
        before = thaw_model_data(session.state.state)
        rows = scenario['events']
        if not isinstance(rows, list) or not rows:raise ValueError('scenario requires at least one event')
        ids = [row['event_ref'] for row in rows]
        if len(set(ids)) != len(ids):raise ValueError('scenario event_ref values must be unique')
        receipts = []
        for row in rows:
            phase = 'progress:'+row['event_ref']
            common = dict(model_runtime=composed.model_runtime, temporal_runtime=composed.temporal_runtime,
                          scope_ref=config['scope_ref'], subject_ref=config['subject_ref'], action_ref=None)
            progress = execute_runtime_progress_transaction(session=session,
                target_coordinate=row['coordinate'], event_ref=row['event_ref']+':progress', **common)
            phase = 'event:'+row['event_ref']
            event = execute_committed_event_transaction(session=progress.session,
                event_ref=row['event_ref'], measurement_payload=row['measurement'], **common)
            phase = 'shared_projection:'+row['event_ref']
            projection_input = {'parameters':thaw_model_data(event.session.parameters.parameters),
                                'settlement':thaw_model_data(event.settlement_execution.payload['settlement'])}
            runtime = composed.model_runtime
            projection = runtime.invoke('project_shared_semantics', payload=projection_input, context=event.context)
            verify_runtime_stage_execution(projection, stage='project_shared_semantics', runtime=runtime,
                context=event.context, invocation_payload=projection_input)
            phase = 'assess_action:'+row['event_ref']
            assessment_input = {'shared_projection':projection.payload['shared_projection']}
            assessment = runtime.invoke('assess_action', payload=assessment_input, context=event.context)
            verify_runtime_stage_execution(assessment, stage='assess_action', runtime=runtime,
                context=event.context, invocation_payload=assessment_input)
            receipts.append({'event_ref':row['event_ref'], 'progress':thaw_model_data(progress.receipt),
                'event':thaw_model_data(event.receipt), 'acceptance':event.acceptance.to_record(),
                'projection':{'payload':thaw_model_data(projection.payload), 'receipt':projection.receipt.to_record()},
                'assessment':{'payload':thaw_model_data(assessment.payload), 'receipt':assessment.receipt.to_record()}})
            session = event.session
        phase = 'roundtrip_restore'
        profile = json.loads(json.dumps(thaw_model_data(session.profile), allow_nan=False))
        restored = restore_bootstrapped_runtime_session(profile=profile, model_runtime=composed.model_runtime,
            temporal_runtime=composed.temporal_runtime, runtime_variant=composed.variant_binding)
        if restored.state.fingerprint != session.state.fingerprint:raise ValueError('roundtrip state differs')
        return {'mode':'fixture', 'package':runtime.binding.package_id,
            'state_before':before, 'state':thaw_model_data(session.state.state),
            'state_fingerprint':session.state.fingerprint, 'restored_same_state':True,
            'receipts':receipts, 'profile':profile, 'external_effects_executed':False}
    except Exception as error:
        raise ValueError(f'{phase}: {type(error).__name__}: {error}') from None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('new', help='generate an ordinary API v2 package in a NEW directory')
    create.add_argument('name');create.add_argument('--output', required=True)
    for name in ('check', 'run'):
        cmd = sub.add_parser(name, help='execute trusted local package fixtures through formal Runtime')
        cmd.add_argument('project', nargs='?', default='.')
        if name == 'run':
            cmd.add_argument('--output', help='NEW report/profile JSON file')
            cmd.add_argument('--restore', help='restore a prior run report without replaying events')
    args = parser.parse_args(argv)
    try:
        if args.command == 'new':result = new(args.name, args.output)
        else:
            output = getattr(args, 'output', None)
            if output and Path(output).exists():raise ValueError('output already exists')
            result = execute(args.project, getattr(args, 'restore', None))
            if output:
                with Path(output).open('x', encoding='utf8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
                    stream.write('\n')
            if args.command == 'check':
                count = len(result['receipts'])
                result = {k:v for k,v in result.items() if k not in ('profile', 'receipts')}
                result['checked_events'] = count
                result['check'] = 'passed'
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps({'status':'error','detail':str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
