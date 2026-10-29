"""Adapt an existing completion callable without replacing Runtime/ProviderGateway.

Run: python -B -m examples.existing_provider
The built-in callable is a deterministic offline fixture, not a real LLM.
"""
import argparse
from dataclasses import dataclass,field
import json
from pathlib import Path
import tempfile
from collections.abc import Callable,Mapping

from framework.model_api import thaw_model_data
from framework.provider_host import ProviderInvocationFailure
from petlab_app.providers import ChatTransport,ProviderGateway,completion_text
from examples.runtime_minimal import create_runtime,settle


@dataclass(frozen=True,kw_only=True)
class ExistingClientTransport(ChatTransport):
    completion:Callable=field(repr=False,compare=False)

    def __post_init__(self):
        super().__post_init__()
        if not callable(self.completion):raise TypeError('completion must be an existing callable')

    def __call__(self,request,payload):
        body=thaw_model_data(payload)
        if body.get('model')!=self.model:raise ValueError('selected model changed')
        try:wire=self.completion(body)
        except Exception:
            # Keep SDK errors/credentials out of shared failure messages.
            # Explicit recovery, rather than this adapter, owns any retry.
            raise ProviderInvocationFailure(failure_kind='existing_client_failed',failure_source_ref=request.request_ref) from None
        if not isinstance(wire,Mapping):raise TypeError('return a Chat Completions-shaped response')
        return dict(wire)


def offline_existing_client(payload):
    # Stand-in for a user's existing SDK/classifier. The host sends observed
    # input only, never the current Runtime state for the client to rewrite.
    observed=json.loads(payload['messages'][-1]['content'])
    measured={'affinity_stimulus':observed['interest_score'],'conflict_stimulus':observed['friction_score']}
    return {'model':payload['model'],'choices':[{'index':0,'finish_reason':'stop',
        'message':{'role':'assistant','content':json.dumps(measured)}}]}


def exercise(trace_dir):
    composed,session=create_runtime('example:existing-client');calls=[]
    def complete(payload):calls.append(payload);return offline_existing_client(payload)
    transport=ExistingClientTransport(endpoint='http://127.0.0.1:9/offline-callback',model='offline-example',completion=complete)
    gateway=ProviderGateway(transport,trace_dir=Path(trace_dir))
    result=gateway.run(session_binding=session.session_binding,current_position=session.position,
        operation_kind='example_measurement',source_fact_ref='example:observed-signal:1',
        codec_contract='example-affinity-json-v1',messages=[
            {'role':'system','content':'Return the two measurement fields as one JSON object.'},
            {'role':'user','content':json.dumps({'interest_score':.6,'friction_score':.2})}],
        temperature=0,require_json=True,parse=lambda wire:json.loads(completion_text(wire)))
    if result.status!='parsed':raise RuntimeError('provider result not parsed; original trace retained')
    event=settle(session,composed,thaw_model_data(result.parsed_payload))
    return {'provider_status':result.status,'existing_client_calls':len(calls),'network_requests':0,
        'parsed_measurement':thaw_model_data(result.parsed_payload),'state_after':thaw_model_data(event.session.state.state),
        'provider_trace_files':len(list(Path(trace_dir).glob('*.json'))),'state_commit_receipt':event.acceptance.fingerprint}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,help='Optional trace directory.')
    args=parser.parse_args()
    if args.output is not None:
        args.output.mkdir(parents=True,exist_ok=True);value=exercise(args.output)
    else:
        with tempfile.TemporaryDirectory(prefix='tide-provider-example-') as tmp:value=exercise(tmp)
    print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
