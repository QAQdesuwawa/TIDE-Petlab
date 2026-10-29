"""Public offline checks: python -B -m unittest examples.test_examples."""
from pathlib import Path
import json
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class DeveloperExamples(unittest.TestCase):
    def test_runtime_runs_without_desktop_or_reference_package(self):
        root=Path(__file__).resolve().parents[1]
        # A clean child keeps unrelated imports out of the independence check.
        code="import runpy,socket;socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('network forbidden'));runpy.run_module('examples.runtime_minimal',run_name='__main__')"
        result=subprocess.run([sys.executable,'-B','-X','utf8','-c',code],cwd=root,capture_output=True,text=True,encoding='utf8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertTrue(data['restored_same_state'])
        self.assertFalse(data['cx_package_imported']);self.assertFalse(data['desktop_app_imported'])

    def test_existing_client_uses_gateway_and_package_settlement(self):
        from examples.existing_provider import exercise
        with tempfile.TemporaryDirectory(prefix='tide-example-test-') as tmp:
            with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
                result=exercise(tmp)
        self.assertEqual(result['existing_client_calls'],1)
        self.assertEqual(result['provider_status'],'parsed')
        self.assertEqual(result['provider_trace_files'],1)
        self.assertTrue(result['state_commit_receipt'])

    def test_existing_client_failure_does_not_expose_sdk_error_text(self):
        from examples.existing_provider import ExistingClientTransport
        from framework.provider_host import ProviderInvocationFailure
        def failed(_):raise RuntimeError('DO_NOT_EXPORT_ME')
        transport=ExistingClientTransport(endpoint='http://127.0.0.1:9/offline',model='fixture',completion=failed)
        with self.assertRaises(ProviderInvocationFailure) as caught:
            transport(SimpleNamespace(request_ref='example:request'),{'model':'fixture'})
        self.assertNotIn('DO_NOT_EXPORT_ME',str(caught.exception))
        self.assertEqual(caught.exception.failure_kind,'existing_client_failed')

    def test_player_complete_partial_and_pre_effect_cancel(self):
        from examples.existing_player import run
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):result=run()
        self.assertEqual(result['completed_frames'],3);self.assertEqual(result['partial_frames'],2)
        self.assertEqual(result['partial_status'],'interrupted');self.assertEqual(result['cancelled_frames'],0)
        self.assertTrue(result['ineligible_has_no_selection']);self.assertFalse(result['memory_committed'])

    def test_unsupported_player_channel_is_rejected_before_dispatch(self):
        from examples.existing_player import ExistingPlayerExecutor
        from model_packages.desktop_pet_v1.events import Speech
        from model_packages.desktop_pet_v1.composite import CompositePlan
        calls=[];executor=ExistingPlayerExecutor(lambda cancel:calls.append(True))
        voice=Speech(content_id='voice',description='example',recipient_refs=('user:example',))
        plan=CompositePlan.create({'voice':{'kind':'speech','key':'reply'}})
        with self.assertRaises(ValueError):executor.execute_composite('example:unsupported',(voice,),plan=plan)
        self.assertEqual(calls,[])

    def test_missing_memory_capability_is_explicit(self):
        from framework.model_registry import InteractionModelRegistry,ModelRegistrationError
        from model_packages.minimal_affinity_v1 import MinimalAffinityV1Package
        package=MinimalAffinityV1Package();registry=InteractionModelRegistry();registry.register(package)
        binding=registry.binding_for(package.manifest.package_id,package.manifest.package_version)
        with self.assertRaises(ModelRegistrationError):
            registry.bind(binding,session_id='example:needs-memory',required_capabilities=('recall_history',))


if __name__=='__main__':unittest.main()
