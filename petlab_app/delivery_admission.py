"""Intake priority, not occurrence order or a psychological interrupt gate."""
from .visible_delivery import delivery_order


def human_frontier(rows):
    # Public human input is current input for witnesses too; addressing is
    # a visibility/response contract, not permission to prioritize intake.
    return max((delivery_order(r) for r in rows if r['sender_kind']=='human'),default=None)


def ordered(rows,frontier,started=(),*,human_anchor=None):
    started=set(started)
    if human_anchor is None:human_anchor=human_frontier(rows)
    # A frontier can be a terminal checkpoint of a response whose speech is
    # still pending. Prioritize its received siblings, keeping their real order.
    boundary=next((r for r in rows if delivery_order(r)==frontier),None)
    parent=boundary.get('visible',{}).get('parent_event_id') if boundary is not None else None
    def key(row):
        order=delivery_order(row)
        visible=row.get('visible',{})
        contents=visible.get('contents',())
        visual_only=(row['sender_kind']=='npc' and bool(contents)
                     and all(item['kind'] in {'action','expression'} for item in contents))
        if row['delivery']['delivery_id'] in started:rank=0
        elif row['sender_kind']=='human' and order==human_anchor:rank=1
        elif parent is not None and visible.get('parent_event_id')==parent and not visual_only:rank=2
        elif frontier is None or order>=frontier:rank=4 if visual_only else 3
        else:rank=5
        return rank,order
    return sorted(rows,key=key)


def after_source(row,source):
    """Old unconsumed history is not a new interruption of a newer intention."""
    return delivery_order(row)>delivery_order(source)


def current_input(row,source,frontier):
    return (source is None or after_source(row,source)) and (frontier is None or delivery_order(row)>=frontier)


def is_peer_speech(row):
    if row['sender_kind']!='npc':
        return False
    visible=row.get('visible')
    if visible is None:
        # Legacy addressed messages contain a single speech item.
        return row['delivery']['delivery_scope']=='addressed'
    return any(item['kind']=='speech' for item in visible['contents'])


def can_interrupt_peer_reply(row):
    """A visible observation can qualify a reply without taking over one."""
    if not is_peer_speech(row):
        return False
    visible=row.get('visible')
    return visible is None or row['projection']['recipient_participant_id'] in visible['target_refs']


def historical_action_end(row, human_frontier, workspace):
    """An action-only ending before a newer human turn is history on arrival."""
    if (human_frontier is None or row['sender_kind']!='npc' or not row.get('visible')
            or row['projection']['committed_at_ms']>=human_frontier[0]):
        return False
    from .visible_delivery import expected_items
    source=workspace.source_event(row['projection']['source_event_id'])
    items=expected_items(source,row['projection']['recipient_participant_id'])
    return bool(items) and all(item['kind']=='action' and item.get('phase') in {'completed','interrupted'}
                              and item['at_ms']<human_frontier[0] for item in items)


def source_delivery(link, source_ref):
    """Resolve the validated original observation, never its processing time.

    A missing participant source is an error for speculative intake. Callers
    defer that intake; ordinary inbox consumption and history remain intact.
    """
    if source_ref is None or not source_ref.startswith('participant:'):
        return None
    from .multi_pet import _key
    path=link.local/'inbox'/(source_ref.removeprefix('participant:')[:24]+'.json')
    row=link.workspace.read_delivery(link.alias,path)
    if 'participant:'+_key(row['delivery']['delivery_id'])!=source_ref:
        raise ValueError('prefetch source differs from its inbox delivery')
    return row


def retained_delivery(link, source_ref, original):
    """Advance occurrence checks without changing the original reply scope."""
    if source_ref is None:return original
    from .retained_intention import head,validate
    app=link.product.application
    retained=head(app,source_ref)
    if retained is None:return original
    validate(app,source_ref,retained)
    delivery=source_delivery(link,retained)
    if delivery is None:raise ValueError('retained input has no participant delivery')
    return delivery


def reply_conflict(row, conversation, *, reference=None):
    """A directed turn in the same conversation can replace pending speech.

    This describes scope only. Formal eligibility and occurrence checks remain
    mandatory at the actor, independently of the stage of presentation.
    """
    if not can_interrupt_peer_reply(row):
        return False
    projection=row['projection']
    if conversation is not None and projection['conversation_id']!=conversation:
        return False
    return reference is None or all(
        projection.get(key)==reference['projection'].get(key)
        for key in ('membership_version','recipient_participant_id')
    )
