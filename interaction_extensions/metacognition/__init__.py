"""Optional, versioned records for evidence and audience intent around an actual effect."""
from framework.history import CommittedFact
from framework.model_api import model_data_fingerprint
from interaction_extensions import InteractionExtensionManifest

VERSION = 'tide.metacognition.v1'
EXTENSION_MANIFEST = InteractionExtensionManifest(
    extension_id='tide.metacognition',
    version='1.0.0-alpha1',
    capabilities=('record_experience', 'record_epistemic_basis', 'record_expression_purpose'),
    required_framework_contracts=('committed_event', 'authorized_history_read', 'execution_receipt'),
)


def record_layers(history, *, ordinal, position, event, owner, observed, decision, effect, receipt):
    """Validate all layers before appending. The host supplies the verified model/effect receipts."""
    ids = effect['completed_content_ids']
    if 'choice' not in ids:
        raise ValueError('no completed action')
    actual = effect['details']
    evidence = decision['evidence']
    if not evidence or any(ref not in observed for ref in evidence):
        raise ValueError('decision cites unknown observations')
    spoken = 'speech' in ids
    if spoken and (not decision['purpose'] or not decision['recipient']
                   or (actual['speech'], actual['recipient']) != (decision['speech'], decision['recipient'])):
        raise ValueError('speech receipt differs from the recorded expression decision')

    experience = f'{event}:experience'
    basis = f'{event}:basis'
    receipt_link = {'model_decision': receipt, 'effect': model_data_fingerprint(effect)}
    layers = [
        (experience, (owner,),
         {'observed': observed, 'effect': effect}),
        (basis, (owner,), {'experience': experience, 'choice': decision['choice'],
                           'basis': decision['basis'], 'evidence': evidence}),
    ]
    if spoken:
        layers.append((f'{event}:purpose', (owner,), {'experience': experience, 'basis_ref': basis,
                       'recipient': actual['recipient'], 'purpose': decision['purpose'],
                       'expression_ref': effect['trace_ref']}))
    facts = [
        CommittedFact.create(fact_id=identity, session_binding_fingerprint=history.session_binding_fingerprint,
                             source_ref=event, scope_ref=VERSION, visible_to_refs=readers,
                             temporal_position_fingerprint=position.fingerprint,
                             accepted_runtime_ordinal=ordinal + index, content_ref={'event': event},
                             model_payload={'event': event, **data}, settlement_ref=receipt_link)
        for index, (identity, readers, data) in enumerate(layers)
    ]
    existing = {fact.fact_id: fact for fact in history.facts()}
    latest = history.facts()[-1].accepted_runtime_ordinal if existing else -1
    for fact in facts:
        if fact.fact_id in existing:
            if existing[fact.fact_id].fingerprint != fact.fingerprint:
                raise ValueError('conflicting metacognition layer identity')
        elif fact.accepted_runtime_ordinal <= latest:
            raise ValueError('metacognition layers must append in order')
    return tuple(history.append_fact(fact).fact_id for fact in facts)


def select_layers(history, reader, event, ordinal, ids):
    return history.authorize_read(reader_ref=reader, trigger_fact_id=event,
                                  before_runtime_ordinal=ordinal, requested_fact_ids=ids,
                                  authorization_ref={'contract': VERSION})
