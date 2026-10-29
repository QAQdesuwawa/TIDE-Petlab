"""Display the original accepted measurement; no invocation or normalization."""
from framework.model_api import model_data_fingerprint,thaw_model_data

VERSION='desktop-pet-source-measurement-view-v1'


def source_measurement(application,lifecycle,event_id):
    # PetExpressionService.project has verified this original continuation.
    evidence=lifecycle.evidence
    payload=thaw_model_data(evidence.measurement_payload)
    product_ref=model_data_fingerprint(payload)
    if (evidence.event_id!=event_id or evidence.receipt.get('source_binding_ref')!=
            application.runtime.memory_instance(event_id).event.source_binding_ref
            or evidence.receipt.get('measurement_fingerprint')!=product_ref):
        raise ValueError('archived measurement differs from accepted input')
    measured=payload['measurement']
    fields={'version':VERSION,'source_event_ref':event_id,'measurement_product_ref':product_ref,
        'status':'measured' if measured is not None else 'no_usable_measurement'}
    if measured is not None:
        fields.update({key:measured[key] for key in ('variable_deltas','confidence','axis_presence','source','model')})
    return fields,(product_ref,evidence.receipt['fingerprint'])
