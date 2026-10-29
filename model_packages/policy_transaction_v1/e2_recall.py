"""E2-only consumer of archived recall contributions using the existing reducer."""
from collections.abc import Mapping
from framework.model_api import InteractionModelManifest, model_data_fingerprint, thaw_model_data
from . import PolicyTransactionV1Package
from .memory import validate_historical_influence


class E2RecallInfluencePolicyTransactionPackage(PolicyTransactionV1Package):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        manifest = self.manifest.to_record()
        manifest.pop('schema_fingerprint')
        manifest.pop('parameter_fingerprint')
        manifest['package_version'] += '.e2-recall-influence-v1'
        manifest['operator_versions']['read_historical_influence'] = 'policy-transaction-recall-edge-influence-v1'
        self.manifest = InteractionModelManifest.create(**manifest)

    def read_historical_influence(self, *, payload, context):
        record = thaw_model_data(payload)
        edges = record.pop('runtime_recall_edges', [])
        output = super().read_historical_influence(payload=record, context=context)
        influence = output['experience_influence']
        experiences = {row['event_id']: row for row in influence['event_influences']}
        tools = set(influence['exact_tool_ids'])
        edge_refs = []
        for edge in edges:
            contribution = edge['contribution_payload']
            if not isinstance(contribution, Mapping):
                raise ValueError('recall edge contribution must be a mapping')
            # Other model packages' numeric payloads are not this package's experience.
            experience = contribution.get('experience')
            if experience is not None:
                experience = thaw_model_data(experience)
                if experience['event_id'] != edge['parent_fact_id'] or experience['tool_id'] not in tools:
                    raise ValueError('archived recall experience identity differs')
                if experience['event_id'] in experiences and experiences[experience['event_id']] != experience:
                    raise ValueError('conflicting archived experience for one source event')
                experiences[experience['event_id']] = experience
            edge_refs.append(edge['fingerprint'])
        material = {key: value for key, value in influence.items() if key != 'fingerprint'}
        material['event_influences'] = sorted(experiences.values(), key=lambda row: (row['ordinal'], row['event_id']))
        material['fingerprint'] = model_data_fingerprint(material)
        output['experience_influence'] = validate_historical_influence(material)
        # Trace stays in the stage receipt, not the model's expression context.
        output['consumed_recall_edge_fingerprints'] = edge_refs
        return output
