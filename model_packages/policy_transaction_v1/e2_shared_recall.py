"""Tool delegation with the ordinary package recall, not all-memory selection."""
from framework.model_api import InteractionModelManifest
from . import PolicyTransactionV1Package
from .e2_delegated import E2DelegatedPolicyTransactionPackage


class E2SharedRecallDelegatedPackage(E2DelegatedPolicyTransactionPackage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        manifest = self.manifest.to_record()
        manifest.pop('schema_fingerprint')
        manifest.pop('parameter_fingerprint')
        manifest['package_version'] += '.shared-recall-tools-v1'
        manifest['operator_versions']['recall_history'] = 'shared-policy-transaction-recall-v1'
        manifest['operator_versions']['assess_action'] = 'fixed-text-delegated-tools-v1'
        self.manifest = InteractionModelManifest.create(**manifest)

    def recall_history(self, *, payload, context):
        return PolicyTransactionV1Package.recall_history(self, payload=payload, context=context)

    def project_shared_semantics(self, *, payload, context):
        if payload['delegated_control']['action_eligible'] is not True:
            raise ValueError('shared-recall runtime fixes text response eligibility')
        return super().project_shared_semantics(payload=payload, context=context)
