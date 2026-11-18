"""Admission for successive visible checkpoints of one actual performance."""
from model_packages.desktop_pet_v1 import meal_button, prop_move, shared_meal, social_contact
from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY

from .multi_pet import _key
from .visible_delivery import FRAGMENT_VERSIONS

# These actions have a completed effect beyond their animation. A newly
# observed effect must remain measurable even when its approach was seen.
_EFFECT_ACTIONS = frozenset((*shared_meal.KEYS, *social_contact.KEYS,
                             prop_move.KEY, meal_button.KEY))


def order_speech_episodes(rows, speech_first_parents, started):
    """Keep a pending first observation ahead of its tail after restart."""
    if not speech_first_parents:
        return rows
    started = set(started)
    pending_prefixes = {
        row['visible']['parent_event_id'] for row in rows
        if row.get('visible', {}).get('parent_event_id') in speech_first_parents
        and row['visible'].get('sequence') == 0
    }
    result = []
    deferred = {}
    for row in rows:
        visible = row.get('visible', {})
        parent = visible.get('parent_event_id')
        if parent in pending_prefixes:
            if visible.get('sequence') == 0:
                pending_prefixes.remove(parent)
            elif visible.get('sequence') == 1 and row['delivery']['delivery_id'] not in started:
                deferred[parent] = row
                continue
        result.append(row)
        if visible.get('sequence') == 0 and parent in deferred:
            result.append(deferred.pop(parent))
    return result


def _scope(row):
    p = row['projection']
    return tuple(p.get(key) for key in ('sender_participant_id', 'recipient_participant_id',
                                         'conversation_id', 'membership_version'))


def _speech_scopes(row):
    visible = row['visible']
    targets = tuple(sorted(visible['target_refs']))
    return {(tuple(sorted(item['recipient_refs'])), targets)
            for item in visible['contents'] if item['kind'] == 'speech'}


def _has_independent_effect(link, row):
    action_ids = {item['content_id'] for item in row['visible']['contents']
                  if item['kind'] == 'action' and item.get('semantic_key') in _EFFECT_ACTIONS}
    if not action_ids:
        return False
    source = link.workspace.source_event(row['projection']['source_event_id'])
    reader = row['projection']['recipient_participant_id']
    return any(item['content_id'] in action_ids and item.get('phase') == 'completed'
               and reader in item['recipient_refs'] for item in source['visible_manifest']['items'])


def measured_parent(link, row, *, include_inflight=False):
    """Find the measured anchor, or reserve its sibling while measured in flight."""
    visible = row.get('visible')
    if row['sender_kind'] != 'npc' or not visible or visible.get('version') not in FRAGMENT_VERSIONS:
        return None
    siblings = getattr(link, '_visible_siblings', {}).get(visible['parent_event_id'], ())
    if len(siblings) < 2:
        return None
    app = link.product.application
    identity = row['delivery']['delivery_id']
    from .session import PRODUCT_KEY
    profile = app.runtime.session.profile
    sources = profile.get(PRODUCT_KEY, {}).get('sources', {})
    if 'participant:' + _key(identity) in sources:
        return None
    settled = profile.get(EVENT_JOURNAL_KEY, {})
    anchor_ids = sources if include_inflight else settled
    scope = _scope(row)
    anchors = [other for other in siblings if other['delivery']['delivery_id'] != identity
               and _scope(other) == scope
               and 'participant:' + _key(other['delivery']['delivery_id']) in anchor_ids]
    if not anchors or _has_independent_effect(link, row):
        # Existing transactions resume by their original identity.
        return None
    speech = _speech_scopes(row)
    if speech:
        # The publisher removes already emitted phases before routing the
        # terminal episode. Its remaining speech was actually heard.
        if (visible['parent_event_id'] in getattr(link, '_speech_first_parents', ())
                and visible['sequence'] == 1):
            for anchor in anchors:
                anchor_event = 'participant:' + _key(anchor['delivery']['delivery_id'])
                if include_inflight and anchor_event not in settled:
                    # Reserve this sibling until the frozen request's actual
                    # coverage has been committed by the owner.
                    return anchor['delivery']['delivery_id']
                source = sources.get(anchor_event, {})
                coverage = source.get('measurement', {}).get('visible_input_coverage', {})
                if identity in coverage.get('additional_deliveries', ()):
                    return anchor['delivery']['delivery_id']
            return None
        # Do not consume first actual speech on the strength of a prior look,
        # or a line under a different audience/addressing contract.
        if not speech.issubset(set().union(*map(_speech_scopes, anchors))):
            return None
        anchors = [other for other in anchors if _speech_scopes(other) & speech]
    return min(anchors, key=lambda other: other['visible']['sequence'])['delivery']['delivery_id']


def ready_tail(link, delivery_id, parent):
    """Snapshot the already routed terminal speech when preparing the prefix."""
    from .multi_pet import _immutable, _read, _signed, _verify
    link._refresh()
    if parent not in link._speech_first_parents:
        return None
    rows = link._visible_siblings.get(parent, ())
    anchor = next((r for r in rows if r['delivery']['delivery_id'] == delivery_id), None)
    if anchor is None or anchor['visible']['sequence'] != 0:
        return None
    path = link.local / 'measurement-scopes' / (_key(delivery_id) + '.json')
    if path.exists():
        saved = _verify(_read(path))
        if saved['anchor'] != delivery_id or saved['parent'] != parent:
            raise ValueError('measurement scope binding differs')
        tail_id = saved['tail']
        if tail_id is None:
            return None
        return link.workspace.read_delivery(link.alias, link.workspace.inbox_path(link.alias, tail_id))
    tail = None
    for row in rows:
        if (row['visible']['sequence'] == 1 and _scope(row) == _scope(anchor)
                and _speech_scopes(row) and _speech_scopes(row) == _speech_scopes(anchor)
                and not _has_independent_effect(link, row)):
            tail = row
            break
    _immutable(path, _signed({'anchor':delivery_id,'parent':parent,
                              'tail':tail['delivery']['delivery_id'] if tail else None}))
    return tail
