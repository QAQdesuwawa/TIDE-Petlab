"""Compose a non-CX package, settle an event, and restore its numeric profile.

Run from the repository root: python -B -m examples.runtime_minimal
This example has no desktop, language model, full-memory capability or Store.
"""
import argparse
import json
from pathlib import Path
import sys

from framework.model_api import thaw_model_data
from framework.runtime_variant import RuntimeVariantDefinition
from model_packages.minimal_affinity_v1 import MinimalAffinityV1Package
from temporal_packages.wall_clock_ms_v1 import WallClockMillisecondsV1
from ops.runtime_package_composition import compose_runtime_packages
from ops.runtime_session_bootstrap import bootstrap_runtime_session,restore_bootstrapped_runtime_session
from ops.runtime_event_transaction import execute_committed_event_transaction
from ops.runtime_progress_transaction import execute_runtime_progress_transaction


def create_runtime(session_id='example:minimal'):
    variant=RuntimeVariantDefinition.create(
        implementation_id='example.minimal-host.v1',kernel_implementation_id='tide.runtime-kernel.v1',
        mechanism_implementations={'persistent_state_authority':'state-authority-v1','commit_gate':'commit-gate-v1'},
        realizer_profile_id='example.no-language.v1',resource_policy={
            'current_input':'enabled','persona':'enabled','conversation':'enabled',
            'expression':'enabled','memory':'disabled','form':'enabled'})
    composed=compose_runtime_packages(model_package=MinimalAffinityV1Package(observation_unit='milliseconds'),
        temporal_package=WallClockMillisecondsV1(),session_id=session_id,variant_definition=variant,
        required_model_capabilities=('initialize_parameters','initialize_state','settle_state'))
    session=bootstrap_runtime_session(profile={},model_runtime=composed.model_runtime,
        temporal_runtime=composed.temporal_runtime,runtime_variant=composed.variant_binding,
        initial_coordinate=1000,parameter_payload={'responsiveness':.75,'restraint':.25},
        state_payload={'affinity':.4,'tension':.2,'expression_drive':.1})
    return composed,session


def settle(session,composed,measurement,*,event_ref='example:observed-signal:1'):
    # The host supplies an actual observed event and a package-shaped measurement.
    # This function does not infer measurements from text or commit UI output.
    return execute_committed_event_transaction(session=session,model_runtime=composed.model_runtime,
        temporal_runtime=composed.temporal_runtime,scope_ref='example:conversation',subject_ref='example:agent',
        event_ref=event_ref,action_ref=None,measurement_payload=measurement)


def run(output=None):
    composed,initial=create_runtime()
    progress=execute_runtime_progress_transaction(session=initial,model_runtime=composed.model_runtime,
        temporal_runtime=composed.temporal_runtime,target_coordinate=1100,scope_ref='example:conversation',
        subject_ref='example:agent',event_ref='example:elapsed-time:1',action_ref=None)
    # These two declared numbers are fixtures, not an LLM or a psychological claim.
    event=settle(progress.session,composed,{'affinity_stimulus':.6,'conflict_stimulus':.2})
    encoded=json.dumps(thaw_model_data(event.session.profile),ensure_ascii=False)
    restored=restore_bootstrapped_runtime_session(profile=json.loads(encoded),model_runtime=composed.model_runtime,
        temporal_runtime=composed.temporal_runtime,runtime_variant=composed.variant_binding)
    if output is not None:
        path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf8') as f:f.write(encoded+'\n')
    return {'package':composed.model_runtime.binding.package_id,
        'state_before':thaw_model_data(initial.state.state),'state_after':thaw_model_data(event.session.state.state),
        'position':event.session.position.position.coordinate,'settlement_receipt':event.acceptance.fingerprint,
        'restored_same_state':restored.state.fingerprint==event.session.state.fingerprint,
        'capabilities':sorted(composed.model_runtime.manifest.capabilities),
        'cx_package_imported':any(n.startswith('model_packages.cx_six_seven') for n in sys.modules),
        'desktop_app_imported':'petlab_app.desktop_app' in sys.modules,'network_requests':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,
        help='Optional NEW numeric profile JSON file; existing files are never overwritten.')
    args=parser.parse_args();print(json.dumps(run(args.output),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
