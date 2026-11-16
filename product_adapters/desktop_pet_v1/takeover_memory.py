"""Reauthorize executed memories already supplied to a superseded Writer."""

from framework.model_api import thaw_model_data
from model_packages.cx_six_seven_v1.realizer_packet import WriterCard
from petlab_app.destinations import destination as resolve_destination


def _destination_key(destination):
    conversation = destination['conversation']
    return (destination['primary'], conversation['conversation_id'],
            frozenset(destination['audience']), conversation['membership_version'])


def _parents(app, event_id):
    from petlab_app.multi_pet import _key
    from petlab_app.peer_reaction import KEY, _verify

    outcomes = app.runtime.session.profile.get(KEY, {}).get('outcomes', {})
    for parent_id, outcome in outcomes.items():
        if (outcome.get('status') not in ('pending', 'continued') or not outcome.get('eligible')
                or not any(event_id == 'participant:' + _key(delivery)
                           for delivery in outcome.get('superseded_by', ()))):
            continue
        _verify(outcome)
        if outcome['event_id'] == parent_id and outcome.get('continued_by', event_id) == event_id:
            yield parent_id, outcome['fingerprint']


def _previous_prompt(journal, parent, destination, owner):
    rows = journal.read_parent_receipts(parent.event.event_id)
    dispatched = sorted((row for row in rows if row['stage'] == 'provider_dispatch'),
                        key=lambda row: row['attempt_ordinal'], reverse=True)
    for receipt in dispatched:
        artifact = journal.read_attempt(receipt['writer_request_id'])
        source = artifact.get('source_evidence', {})
        if (source.get('event_id') != parent.event.event_id
                or source.get('source_fact_fingerprint') != parent.fact.fingerprint):
            continue
        packet = artifact.get('packet', {})
        request = source['request']['readonly_facts']
        prior_destination = request.get('participant_destination')
        identity = packet['output_identity']
        delivery = request['prompt_delivery']
        dispatch = artifact.get('provider_dispatch', {})
        prompt = {key: delivery[key] for key in ('system', 'user', 'prompt_fingerprint')}
        if (not prior_destination or _destination_key(prior_destination) != destination
                or packet != request['writer_packet']
                or identity['speaker_participant_id'] != owner
                or frozenset(identity['audience_participant_ids']) != destination[2]
                or artifact.get('prompt') != prompt
                or dispatch.get('prompt_fingerprint') != delivery['prompt_fingerprint']
                or dispatch.get('packet_fingerprint') != packet['packet_fingerprint']):
            continue
        return receipt['writer_request_id'], delivery, packet['sections']['memory']
    return None


def cards(app, journal, current, memories, already_selected, visibility, *, source_typed=False):
    """Reuse exact historical card rows without carrying old scores or output."""
    from petlab_app.writer_visibility import EXPERIENCE_VERSIONS, allowed
    from .metacognition import execution_note

    if visibility is None or visibility['version'] not in EXPERIENCE_VERSIONS:
        return (), {}
    parents = tuple(_parents(app, current.event.event_id))
    if not parents:
        return (), {}
    selected_destination = resolve_destination(app, current.event.event_id)
    if selected_destination is None:
        return (), {}
    edge = _destination_key(selected_destination)
    readers = frozenset(visibility['audience'])
    if readers != edge[2] or current.event.actor_ref != edge[0]:
        return (), {}

    added, by_id = [], {}
    sources = app._product()['sources']
    for parent_id, handoff_ref in parents:
        parent = app.runtime.memory_instance(parent_id)
        if (parent.event.actor_ref != edge[0] or parent.event.owner_ref != app.owner_ref
                or parent.event.scope_ref != current.event.scope_ref
                or parent.fact.session_binding_fingerprint != current.fact.session_binding_fingerprint
                or parent.fact.accepted_runtime_ordinal >= current.fact.accepted_runtime_ordinal):
            continue
        previous = _previous_prompt(journal, parent, edge, app.owner_ref)
        if previous is None:
            continue
        request_id, delivery, prior_cards = previous
        for card in prior_cards:
            fields = card['fields']
            rows = fields.get('相关记忆（按发生顺序）', (fields,))
            for source_id, row in zip(card['provenance']['selected_memory_ids'], rows, strict=True):
                if (source_id in already_selected or source_id in by_id
                        or delivery['memory_aliases'].get(row['记忆标记']) != source_id):
                    continue
                note = execution_note(sources.get(source_id, {}), app.owner_ref)
                if note is None or set(note.get('speech_targets', ())) != {edge[0]}:
                    continue
                memory = memories.get(source_id) or app.runtime.memory_instance(source_id)
                if (memory.fact.session_binding_fingerprint != current.fact.session_binding_fingerprint
                        or memory.event.scope_ref != current.event.scope_ref
                        or memory.fact.accepted_runtime_ordinal >= parent.fact.accepted_runtime_ordinal
                        or not allowed(app, memory, readers, version=visibility['version'])):
                    continue
                by_id[source_id] = memory
                fields = {**thaw_model_data(row), '记忆标记': 'C' + str(len(by_id))}
                if source_typed:
                    for key in ('语义属性', '内容归属注解', '话语语义（说话者的说法）', '属性归属绑定'):
                        fields.pop(key, None)
                added.append(WriterCard(card_id='memory:takeover:' + source_id, lane='memory',
                    kind='addressed_pet_event', source_id=source_id, fields=fields,
                    source_receipt_refs=(memory.fact.fingerprint, delivery['fingerprint'], handoff_ref),
                    provenance={'selected_memory_ids': [source_id], 'state_eligible': False,
                                'previous_writer_request_id': request_id},
                    budget_policy='preserve_selection'))
    return tuple(added), by_id
